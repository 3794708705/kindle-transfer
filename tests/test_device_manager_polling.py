"""V0.2.1 device-safety tests.

These tests drive DeviceManager._poll() with injected volumes so the
multi-device safety rules are exercised end-to-end, rather than by poking
private state.

Covers the acceptance cases:
  - one high-confidence Kindle  -> auto-select
  - two Kindles                -> never auto-select
  - two Kindles, different score -> still never auto-select
  - user picks the 2nd device  -> becomes current_device
  - selected device unplugged, another still present -> no silent switch
  - drive letter changes E: -> F: -> profile mapping survives
  - first connect, unknown model -> asks the user
  - fingerprint changed before transfer -> validation blocks
  - detection fails -> manual selection still works
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from app.devices.windows_detector import WindowsVolume, DRIVE_REMOVABLE
import app.devices.device_manager as dm_module


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_settings():
    """Keep device-profile mappings out of the user's real QSettings."""
    from app.devices.device_matcher import set_qsettings_names

    set_qsettings_names("KindleTransferTests", "KindleTransferTests_Devices")
    yield
    set_qsettings_names("KindleTransfer", "KindleTransfer")


@pytest.fixture
def volumes(monkeypatch):
    """Replace enumerate_volumes with a controllable fake."""
    state: dict[str, list[WindowsVolume]] = {"items": []}
    monkeypatch.setattr(dm_module, "enumerate_volumes", lambda: list(state["items"]))
    return state


def _kindle_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "documents").mkdir()
    return root


def _vol(
    letter: str,
    serial: str,
    root: Path,
    label: str = "Kindle",
    free_gb: float = 6.0,
) -> WindowsVolume:
    return WindowsVolume(
        drive_letter=letter,
        root_path=root,
        volume_label=label,
        filesystem="FAT32",
        drive_type=DRIVE_REMOVABLE,
        serial_number=serial,
        total_bytes=8 * 1024**3,
        free_bytes=int(free_gb * 1024**3),
    )


def _unique(prefix: str) -> str:
    """Unique serial so persisted mappings never leak between runs."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ── Auto-select rules ────────────────────────────────────────────────────────

class TestAutoSelectPolicy:

    def test_single_high_confidence_is_auto_selected(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", _unique("S"), root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        assert mgr.current_device is not None
        assert mgr.current_device.root_path == root
        assert mgr.has_device is True

    def test_two_kindles_are_never_auto_selected(self, tmp_path, volumes):
        r1 = _kindle_root(tmp_path, "K1")
        r2 = _kindle_root(tmp_path, "K2")
        volumes["items"] = [
            _vol("E", _unique("S1"), r1),
            _vol("F", _unique("S2"), r2),
        ]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        prompts: list = []
        mgr.selection_required.connect(prompts.append)
        mgr._poll()

        assert mgr.current_device is None
        assert mgr.has_device is False
        assert len(prompts) == 1
        assert len(prompts[0]) == 2

    def test_score_difference_does_not_trigger_auto_select(self, tmp_path, volumes):
        """A 1-2 point scoring edge must never cause an automatic write target."""
        rich = tmp_path / "Rich"
        rich.mkdir()
        (rich / "documents").mkdir()
        (rich / "fonts").mkdir()
        (rich / "audible").mkdir()
        (rich / "system").mkdir()

        poor = _kindle_root(tmp_path, "Poor")

        volumes["items"] = [
            _vol("E", _unique("RICH"), rich),
            _vol("F", _unique("POOR"), poor),
        ]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        assert mgr.current_device is None, "must not pick the higher-scoring device"

    def test_multiple_prompt_is_not_repeated_every_poll(self, tmp_path, volumes):
        """The 2s poll loop must not re-open the selector dialog constantly."""
        r1 = _kindle_root(tmp_path, "K1")
        r2 = _kindle_root(tmp_path, "K2")
        volumes["items"] = [
            _vol("E", _unique("S1"), r1),
            _vol("F", _unique("S2"), r2),
        ]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        prompts: list = []
        mgr.selection_required.connect(prompts.append)

        mgr._poll()
        mgr._poll()
        mgr._poll()

        assert len(prompts) == 1, "selector should be offered once per device set"


# ── Explicit user selection ──────────────────────────────────────────────────

class TestUserSelection:

    def test_user_can_select_the_second_device(self, tmp_path, volumes):
        r1 = _kindle_root(tmp_path, "K1")
        r2 = _kindle_root(tmp_path, "K2")
        s1, s2 = _unique("S1"), _unique("S2")
        volumes["items"] = [_vol("E", s1, r1), _vol("F", s2, r2)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        idx = next(
            i for i, d in enumerate(mgr.all_devices) if d.serial_number == s2
        )
        assert mgr.select_device(idx) is True
        assert mgr.current_device is not None
        assert mgr.current_device.serial_number == s2
        assert mgr.current_device.root_path == r2

    def test_select_device_rejects_bad_index(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", _unique("S"), root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        assert mgr.select_device(99) is False
        assert mgr.select_device(-1) is False


# ── Disconnect safety ────────────────────────────────────────────────────────

class TestDisconnectSafety:

    def test_unplugging_selected_device_does_not_switch_to_other(
        self, tmp_path, volumes
    ):
        """E: unplugged while F: remains -> no silent re-target."""
        r1 = _kindle_root(tmp_path, "K1")
        r2 = _kindle_root(tmp_path, "K2")
        s1, s2 = _unique("S1"), _unique("S2")
        volumes["items"] = [_vol("E", s1, r1), _vol("F", s2, r2)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        idx = next(
            i for i, d in enumerate(mgr.all_devices) if d.serial_number == s1
        )
        mgr.select_device(idx)
        assert mgr.current_device.serial_number == s1

        # E: disappears, F: still connected
        prompts: list = []
        mgr.selection_required.connect(prompts.append)
        volumes["items"] = [_vol("F", s2, r2)]
        mgr._poll()

        assert mgr.current_device is None, "must not silently switch to F:"
        assert mgr.has_device is False
        assert len(prompts) == 1, "user must be asked to confirm the new device"

        # Only an explicit choice may re-arm the target
        mgr.select_device(0)
        assert mgr.current_device is not None
        assert mgr.current_device.serial_number == s2
        assert mgr.has_device is True

    def test_replugging_same_device_restores_selection(self, tmp_path, volumes):
        """If the very same device comes back, its selection is preserved."""
        serial = _unique("SAME")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr.assign_profile("kindle_oasis_3")

        # Unplug, then plug the same device back in
        volumes["items"] = []
        mgr._poll()
        assert mgr.current_device is None

        volumes["items"] = [_vol("E", serial, root)]
        mgr._poll()
        assert mgr.current_device is not None
        assert mgr.current_device.serial_number == serial
        assert mgr.current_device.profile_id == "kindle_oasis_3"

    def test_all_devices_unplugged_clears_state(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", _unique("S"), root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        assert mgr.has_device is True

        volumes["items"] = []
        mgr._poll()
        assert mgr.current_device is None
        assert mgr.has_device is False

    def test_same_device_survives_poll_cycle(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        serial = _unique("S")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr._poll()
        mgr._poll()

        assert mgr.current_device is not None
        assert mgr.current_device.serial_number == serial


# ── Drive letter / fingerprint stability ─────────────────────────────────────

class TestFingerprintStability:

    def test_profile_survives_drive_letter_change(self, tmp_path, volumes):
        """E: -> F: must keep the saved model, because identity is the serial."""
        serial = _unique("S")
        r1 = _kindle_root(tmp_path, "K1")
        volumes["items"] = [_vol("E", serial, r1)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr.assign_profile("kindle_oasis_3")
        assert mgr.current_device.model == "Kindle Oasis 3"

        # Re-appears on a different drive letter, same volume serial
        r2 = _kindle_root(tmp_path, "K2")
        volumes["items"] = [_vol("F", serial, r2)]
        mgr._poll()

        assert mgr.current_device is not None
        assert mgr.current_device.serial_number == serial
        assert mgr.current_device.profile_id == "kindle_oasis_3"
        assert mgr.current_device.model == "Kindle Oasis 3"

    def test_fresh_device_reuses_saved_mapping(self, tmp_path, volumes):
        """A previously saved mapping is restored without asking again."""
        from app.devices.device_matcher import save_device_profile

        serial = _unique("KNOWN")
        save_device_profile(serial, "kindle_oasis_3")

        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        assert mgr.current_device is not None
        assert mgr.current_device.profile_id == "kindle_oasis_3"
        assert mgr.needs_model_selection is False


# ── Model selection prompt ───────────────────────────────────────────────────

class TestModelSelectionPrompt:

    def test_unknown_model_asks_user_on_first_connect(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", _unique("FRESH"), root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        asked: list = []
        mgr.model_selection_needed.connect(asked.append)
        mgr._poll()

        assert len(asked) == 1, "first connect with unknown model should ask"
        assert mgr.needs_model_selection is True

    def test_asking_happens_only_once(self, tmp_path, volumes):
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", _unique("FRESH"), root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        asked: list = []
        mgr.model_selection_needed.connect(asked.append)

        mgr._poll()
        mgr._poll()
        mgr._poll()

        assert len(asked) == 1

    def test_choosing_oasis_persists_mapping(self, tmp_path, volumes):
        from app.devices.device_matcher import _get_saved_profile

        serial = _unique("OASIS")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr.assign_profile("kindle_oasis_3")

        assert mgr.current_device.profile_id == "kindle_oasis_3"
        assert mgr.current_device.model == "Kindle Oasis 3"
        assert _get_saved_profile(serial) == "kindle_oasis_3"

    def test_choosing_generic_persists_mapping(self, tmp_path, volumes):
        from app.devices.device_matcher import _get_saved_profile

        serial = _unique("GEN")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr.assign_profile("generic_kindle")

        assert _get_saved_profile(serial) == "generic_kindle"
        assert mgr.needs_model_selection is False


# ── Pre-transfer validation ──────────────────────────────────────────────────

class TestPreTransferValidation:

    def test_validate_passes_for_intact_device(self, tmp_path, volumes, monkeypatch):
        serial = _unique("OK")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        monkeypatch.setattr(
            dm_module, "get_volume_by_drive", lambda letter: _vol(letter, serial, root)
        )
        assert mgr.validate_current_device() is True

    def test_validate_blocks_when_fingerprint_changed(
        self, tmp_path, volumes, monkeypatch
    ):
        """A different volume occupying the same drive letter must be rejected."""
        serial = _unique("ORIG")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        # Same path, different volume serial -> identity changed
        monkeypatch.setattr(
            dm_module,
            "get_volume_by_drive",
            lambda letter: _vol(letter, _unique("IMPOSTOR"), root),
        )
        assert mgr.validate_current_device() is False

    def test_validate_blocks_when_drive_gone(self, tmp_path, volumes, monkeypatch):
        serial = _unique("GONE")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        monkeypatch.setattr(dm_module, "get_volume_by_drive", lambda letter: None)
        assert mgr.validate_current_device() is False

    def test_validate_blocks_when_documents_removed(self, tmp_path, volumes, monkeypatch):
        serial = _unique("NODOC")
        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()

        monkeypatch.setattr(
            dm_module, "get_volume_by_drive", lambda letter: _vol(letter, serial, root)
        )
        # documents folder disappears (e.g. user deleted it)
        (root / "documents").rmdir()
        assert mgr.validate_current_device() is False

    def test_validate_without_device_is_false(self):
        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        assert mgr.validate_current_device() is False


# ── Profile cache (poll loop must not re-read devices.json every tick) ───────

class TestProfileCache:

    def test_get_profiles_reads_from_disk_once(self, monkeypatch):
        """Repeated lookups must hit the cache, not the filesystem."""
        import app.devices.device_matcher as matcher_module

        matcher_module.invalidate_profile_cache()

        calls = {"n": 0}
        real_loader = matcher_module.load_device_profiles

        def counting_loader(*a, **k):
            calls["n"] += 1
            return real_loader(*a, **k)

        monkeypatch.setattr(matcher_module, "load_device_profiles", counting_loader)

        matcher_module.get_profiles()
        matcher_module.get_profiles()
        matcher_module.get_profiles()

        assert calls["n"] == 1, "profiles should be loaded once and cached"

        matcher_module.invalidate_profile_cache()

    def test_poll_loop_does_not_reload_profiles(self, tmp_path, volumes, monkeypatch):
        """A device with a saved mapping polls without re-reading devices.json."""
        import app.devices.device_matcher as matcher_module
        from app.devices.device_matcher import save_device_profile

        serial = _unique("CACHED")
        save_device_profile(serial, "kindle_oasis_3")
        matcher_module.invalidate_profile_cache()

        calls = {"n": 0}
        real_loader = matcher_module.load_device_profiles

        def counting_loader(*a, **k):
            calls["n"] += 1
            return real_loader(*a, **k)

        monkeypatch.setattr(matcher_module, "load_device_profiles", counting_loader)

        root = _kindle_root(tmp_path, "K")
        volumes["items"] = [_vol("E", serial, root)]

        from app.devices.device_manager import DeviceManager
        mgr = DeviceManager()
        mgr._poll()
        mgr._poll()
        mgr._poll()

        assert mgr.current_device.model == "Kindle Oasis 3"
        assert calls["n"] == 1, "poll loop must not re-read the config each tick"

        matcher_module.invalidate_profile_cache()

    def test_cache_can_be_invalidated(self):
        import app.devices.device_matcher as matcher_module

        first = matcher_module.get_profiles()
        assert matcher_module.get_profiles() is first, "should return the cached dict"

        matcher_module.invalidate_profile_cache()
        second = matcher_module.get_profiles()
        assert second is not first, "invalidate should force a fresh read"
        assert set(second) == set(first)


# ── Manual fallback when auto-detection fails ────────────────────────────────

class TestManualFallback:

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        yield

    def test_manual_selection_works_when_detection_finds_nothing(
        self, tmp_path, volumes, monkeypatch
    ):
        """Requirement: auto-detection failure must not break manual mode."""
        from PySide6.QtWidgets import QFileDialog
        from app.ui.main_window import MainWindow

        volumes["items"] = []  # nothing auto-detected

        window = MainWindow()
        window._device_manager.stop()
        assert window._device_manager.has_device is False

        root = _kindle_root(tmp_path, "ManualKindle")
        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            staticmethod(lambda *a, **k: str(root)),
        )

        window._on_select_kindle()

        assert window._kindle_root == root
        window.close()

    def test_manual_selection_rejects_non_kindle_directory(
        self, tmp_path, volumes, monkeypatch
    ):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.ui.main_window import MainWindow

        volumes["items"] = []
        window = MainWindow()
        window._device_manager.stop()

        # A directory with no documents/ folder
        not_kindle = tmp_path / "PlainFolder"
        not_kindle.mkdir()

        monkeypatch.setattr(
            QFileDialog,
            "getExistingDirectory",
            staticmethod(lambda *a, **k: str(not_kindle)),
        )
        warnings: list = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
        )

        window._on_select_kindle()

        assert window._kindle_root is None
        assert len(warnings) == 1, "should warn about an invalid Kindle directory"
        window.close()
