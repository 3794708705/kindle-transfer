"""Device manager — polling, state, and hotplug detection.

Manages the lifecycle of device detection: scans for devices, tracks
current state, and emits signals when devices are added/removed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.devices.windows_detector import enumerate_volumes, get_volume_by_drive
from app.devices.device_matcher import (
    detect_all_kindle_devices,
    detect_single_high_confidence,
    DetectionResult,
    get_profiles,
    save_device_profile,
)
from app.devices.profiles import DeviceProfile
from app.devices.device_scanner import DeviceScanWorker

logger = logging.getLogger(__name__)


class DeviceManager(QObject):
    """Manages device detection and lifecycle.

    Polls for connected devices on a timer and emits signals when the
    device state changes.  UI connects to these signals to update display.

    Multi-device policy:
    - Single high-confidence Kindle → auto-select
    - Single low-confidence Kindle → notify, needs user confirmation
    - Multiple Kindles → emit signal, UI shows selector, user must pick
    - Device disconnect → clear current_device, do NOT auto-switch
    """

    # Emitted when the device list or current device changes
    device_changed = Signal()
    # Emitted when a new device is connected
    device_connected = Signal(object)  # DetectionResult
    # Emitted when the device is disconnected
    device_disconnected = Signal()
    # Emitted when the user must explicitly choose a target device — either
    # because several Kindles are present, or because a previously selected
    # device disappeared while others remain.  Carries list[DetectionResult].
    selection_required = Signal(list)
    # Emitted when a single low-confidence device is found
    low_confidence_device = Signal(object)  # DetectionResult
    # Emitted when model cannot be determined — UI should ask user
    model_selection_needed = Signal(object)  # DetectionResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current_device: DetectionResult | None = None
        self._all_devices: list[DetectionResult] = []
        self._user_selected_device: bool = False  # True after user explicitly picks
        # Signature of the candidate set we already prompted the user about.
        # Prevents re-emitting selection prompts on every 2s poll tick.
        self._prompt_signature: tuple[str, ...] | None = None
        # True once a selection has been lost while other candidates remain.
        # Blocks auto-selection until the user explicitly re-chooses, so a
        # stale selection can never become a write target for another device.
        self._awaiting_user_selection: bool = False
        # Scanning runs on its own thread.  See app/devices/device_scanner.py
        # for why the blocking scan must never run on the GUI thread.
        self._scanner: DeviceScanWorker | None = None
        self._stopping: bool = False

    def start(self) -> None:
        """Start scanning for devices."""
        logger.info("DeviceManager started")
        self._stopping = False
        self._scanner = DeviceScanWorker()
        self._scanner.scan_completed.connect(self._on_scan_completed)
        self._scanner.scan_failed.connect(self._on_scan_failed)
        self._scanner.start()
        # Deliberately no synchronous first scan: that would put blocking
        # device I/O back on the GUI thread and could freeze startup.

    def stop(self) -> None:
        """Stop scanning."""
        self._stopping = True
        if self._scanner is not None:
            self._scanner.stop()
            self._scanner = None
        logger.info("DeviceManager stopped")

    # ── Scan results ────────────────────────────────────────────────────────

    def _on_scan_completed(self, candidates: list[DetectionResult]) -> None:
        """Receive a finished scan from the worker thread.

        Runs on the GUI thread (Qt queues the signal), so the policy below is
        never executed concurrently with the GUI's own calls into this object.
        """
        if self._stopping:
            return
        self._apply(candidates)

    def _on_scan_failed(self, message: str) -> None:
        logger.error("Device scan reported a failure: %s", message)

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def current_device(self) -> DetectionResult | None:
        return self._current_device

    @property
    def has_device(self) -> bool:
        return self._current_device is not None and self._current_device.is_connected

    @property
    def all_devices(self) -> list[DetectionResult]:
        return list(self._all_devices)

    @property
    def device_count(self) -> int:
        return len(self._all_devices)

    @property
    def needs_model_selection(self) -> bool:
        """True if the current device has no saved profile mapping."""
        if not self._current_device:
            return False
        return self._current_device.model is None

    # ── User actions ────────────────────────────────────────────────────────

    def select_device(self, index: int) -> bool:
        """User explicitly selects a device from the list.

        Args:
            index: Index into all_devices list.

        Returns:
            True if the selection was valid.
        """
        if index < 0 or index >= len(self._all_devices):
            return False
        self._current_device = self._all_devices[index]
        self._user_selected_device = True
        self._awaiting_user_selection = False
        logger.info("User selected device: %s", self._current_device.root_path)
        self.device_changed.emit()

        # Check if model selection is needed
        if self.needs_model_selection:
            self.model_selection_needed.emit(self._current_device)

        return True

    def assign_profile(self, profile_id: str) -> None:
        """Assign a profile to the current device and persist it."""
        if not self._current_device or not self._current_device.volume:
            return
        serial = self._current_device.volume.serial_number
        save_device_profile(serial, profile_id)
        self._current_device.profile_id = profile_id
        profile = get_profiles().get(profile_id)
        if profile is not None:
            self._current_device.model = profile.name
        logger.info("Assigned profile %s to device %s", profile_id, serial)
        self.device_changed.emit()

    def clear_device(self) -> None:
        """Clear the current device (e.g., on disconnect)."""
        self._current_device = None
        self._user_selected_device = False
        self._awaiting_user_selection = False
        self.device_changed.emit()

    # ── Validation ──────────────────────────────────────────────────────────

    def validate_current_device(self) -> bool:
        """Verify the current device is still connected, writable, and has the
        same identity (fingerprint).  Must be called before transfer.

        Returns True if the device is ready for transfer.
        """
        if not self._current_device:
            return False
        if not self._current_device.root_path:
            return False
        if not self._current_device.root_path.exists():
            logger.warning("Device root path no longer exists: %s", self._current_device.root_path)
            return False
        if not self._current_device.target_path:
            return False
        if not self._current_device.target_path.exists():
            logger.warning("Target path no longer exists: %s", self._current_device.target_path)
            return False

        # Verify device identity: re-read the volume and check serial number
        if self._current_device.volume:
            drive = self._current_device.volume.drive_letter
            current_vol = get_volume_by_drive(drive)
            if current_vol is None:
                logger.warning("Device %s: is no longer accessible", drive)
                return False
            if current_vol.serial_number != self._current_device.volume.serial_number:
                logger.warning(
                    "Device identity changed: serial %s -> %s on %s:",
                    self._current_device.volume.serial_number,
                    current_vol.serial_number,
                    drive,
                )
                return False

        # Check writable
        try:
            test = self._current_device.target_path / ".kindle_transfer_test"
            test.write_text("test")
            test.unlink()
            return True
        except OSError:
            logger.warning("Target path not writable: %s", self._current_device.target_path)
            return False

    def get_device_profile(self) -> DeviceProfile | None:
        """Get the DeviceProfile for the current device."""
        if not self._current_device or not self._current_device.profile_id:
            return None
        return get_profiles().get(self._current_device.profile_id)

    # ── Polling ─────────────────────────────────────────────────────────────

    def _scan(self) -> list[DetectionResult]:
        """Perform one blocking device scan.

        This reads the *device* filesystem (folder probes), so in production
        it only ever runs on DeviceScanWorker's thread.
        """
        volumes = enumerate_volumes()
        return detect_all_kindle_devices(volumes)

    def _poll(self) -> None:
        """Synchronous scan + apply.

        Production polling goes through DeviceScanWorker.  This entry point
        exists so a single cycle can be driven deterministically — the test
        suite uses it throughout.
        """
        try:
            all_candidates = self._scan()
        except Exception:
            logger.exception("Device polling failed")
            return
        self._apply(all_candidates)

    def _apply(self, all_candidates: list[DetectionResult]) -> None:
        """Run the multi-device selection policy against a fresh scan."""
        prev_current = self._current_device
        self._all_devices = all_candidates
        signature = self._signature(all_candidates)

        # ── Case: no devices ──
        if len(all_candidates) == 0:
            self._prompt_signature = None
            # Nothing is connected, so the next plug-in is a fresh connection
            # and may auto-select again.
            self._awaiting_user_selection = False
            if prev_current is not None:
                logger.info("All devices disconnected")
                self._current_device = None
                self._user_selected_device = False
                self.device_disconnected.emit()
                self.device_changed.emit()
            return

        # ── Case: single device ──
        if len(all_candidates) == 1:
            candidate = all_candidates[0]

            if prev_current is not None:
                if self._is_same_device(prev_current, all_candidates):
                    # Same physical device (serial matches).  Refresh the info
                    # — the drive letter may have changed — but keep the user's
                    # profile choice.
                    self._carry_over_profile(prev_current, all_candidates)
                    return

                # The selected device is gone and a *different* Kindle is now
                # present.  Never silently re-target: require the user to
                # confirm the new device explicitly.
                logger.info(
                    "Selected device disconnected; a different Kindle is present "
                    "— requiring explicit re-selection"
                )
                self._current_device = None
                self._user_selected_device = False
                self._awaiting_user_selection = True
                self._prompt_signature = None
                self.device_disconnected.emit()

            # Auto-select only when this is a fresh connection AND we are not
            # recovering from a lost selection.
            if candidate.is_high_confidence and not self._awaiting_user_selection:
                logger.info(
                    "Auto-selecting single high-confidence Kindle: %s",
                    candidate.root_path,
                )
                self._current_device = candidate
                self._user_selected_device = False
                self.device_connected.emit(candidate)
                self.device_changed.emit()
                if self.needs_model_selection and self._prompt_signature != signature:
                    self._prompt_signature = signature
                    self.model_selection_needed.emit(candidate)
                return

            # Not auto-selectable: recovering from a lost selection, or the
            # only candidate is low-confidence.  Ask the user to choose.
            if self._prompt_signature == signature:
                self.device_changed.emit()
                return
            self._prompt_signature = signature
            if not candidate.is_high_confidence:
                logger.info("Single low-confidence Kindle — needs user confirmation")
                self.low_confidence_device.emit(candidate)
            self.selection_required.emit(all_candidates)
            self.device_changed.emit()
            return

        # ── Case: multiple devices ──
        # If the previously selected device is still present, keep it.
        if prev_current is not None and self._is_same_device(prev_current, all_candidates):
            self._carry_over_profile(prev_current, all_candidates)
            return

        if prev_current is not None:
            # Selected device gone, others remain — do NOT auto-switch.
            logger.info(
                "Selected device disconnected while %d other Kindle(s) remain "
                "— NOT auto-switching",
                len(all_candidates),
            )
            self._current_device = None
            self._user_selected_device = False
            self._awaiting_user_selection = True
            self.device_disconnected.emit()
            self._prompt_signature = None

        # Multiple Kindles, no valid selection → ask the user, exactly once
        # per distinct set of devices.
        logger.info(
            "Multiple Kindles detected (%d) — user must select", len(all_candidates)
        )
        if self._prompt_signature != signature:
            self._prompt_signature = signature
            self.selection_required.emit(all_candidates)
        self.device_changed.emit()

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _signature(candidates: list[DetectionResult]) -> tuple[str, ...]:
        """Stable identity of a candidate set, based on device serial numbers."""
        return tuple(sorted(c.serial_number for c in candidates))

    @staticmethod
    def _is_same_device(
        device: DetectionResult, candidates: list[DetectionResult]
    ) -> bool:
        """True if `device` is still present in `candidates` (matched by serial)."""
        if not device.volume:
            return False
        return any(
            c.volume and c.volume.serial_number == device.volume.serial_number
            for c in candidates
        )

    def _carry_over_profile(
        self, prev: DetectionResult, candidates: list[DetectionResult]
    ) -> None:
        """Refresh _current_device from the new candidate list, preserving the
        user's saved profile choice (drive letter may have changed)."""
        if not prev.volume:
            return
        for c in candidates:
            if c.volume and c.volume.serial_number == prev.volume.serial_number:
                c.profile_id = prev.profile_id
                c.model = prev.model
                self._current_device = c
                return