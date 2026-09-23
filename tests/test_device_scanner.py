"""Tests for background device scanning.

The regression these guard against: the device scan probes the *device*
filesystem, and it used to run on a 2-second QTimer in the GUI thread.  One
blocked call on a stalled USB connection froze the whole window until Windows
declared the app hung (Application Hang / AppHangB1).

The important tests here are the ones that assert the GUI thread keeps
running while a scan is stuck.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_settings():
    from app.converter.calibre_converter import set_qsettings_names as set_conv
    from app.devices.device_matcher import set_qsettings_names as set_dev

    set_conv("KindleTransferTests", "KindleTransferTests_Scanner")
    set_dev("KindleTransferTests", "KindleTransferTests_Scanner")
    yield
    set_conv("KindleTransfer", "KindleTransfer")
    set_dev("KindleTransfer", "KindleTransfer")


@pytest.fixture(autouse=True)
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield


@pytest.fixture
def scanner_module():
    import app.devices.device_scanner as module

    return module


def _pump(predicate, timeout_s: float = 3.0) -> bool:
    """Process GUI events until predicate() is true or the timeout expires."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _no_devices(monkeypatch, module) -> None:
    """Make the scan cheap and deterministic."""
    monkeypatch.setattr(module, "enumerate_volumes", lambda: [])
    monkeypatch.setattr(module, "detect_all_kindle_devices", lambda volumes: [])


# ── The scan really is off the GUI thread ────────────────────────────────────


class TestScanRunsOffTheGuiThread:
    def test_scan_executes_on_a_worker_thread(self, scanner_module, monkeypatch):
        """The core guarantee: the blocking scan is not on the caller's thread."""
        main_thread = threading.current_thread()
        seen: list[threading.Thread] = []
        done = threading.Event()

        def recording_enum():
            seen.append(threading.current_thread())
            done.set()
            return []

        monkeypatch.setattr(scanner_module, "enumerate_volumes", recording_enum)
        monkeypatch.setattr(
            scanner_module, "detect_all_kindle_devices", lambda volumes: []
        )

        worker = scanner_module.DeviceScanWorker(interval_ms=50)
        worker.start()
        try:
            assert done.wait(3.0), "scan never ran"
            assert seen, "scan produced no record"
            assert seen[0] is not main_thread
            assert seen[0].name != main_thread.name
        finally:
            worker.stop()

    def test_start_returns_without_waiting_for_the_scan(
        self, scanner_module, monkeypatch
    ):
        """A slow device must not delay start()."""
        from app.devices.device_manager import DeviceManager

        release = threading.Event()

        def slow_enum():
            release.wait(5.0)
            return []

        monkeypatch.setattr(scanner_module, "enumerate_volumes", slow_enum)
        monkeypatch.setattr(
            scanner_module, "detect_all_kindle_devices", lambda volumes: []
        )

        manager = DeviceManager()
        began = time.monotonic()
        manager.start()
        elapsed = time.monotonic() - began

        try:
            # start() must not have waited on the blocked scan.
            assert elapsed < 1.0, f"start() blocked for {elapsed:.2f}s"
        finally:
            release.set()
            manager.stop()


# ── The regression test ──────────────────────────────────────────────────────


class TestGuiStaysResponsive:
    def test_gui_timer_keeps_firing_while_the_scan_is_stuck(
        self, scanner_module, monkeypatch
    ):
        """Regression: a stalled scan must not freeze the GUI thread.

        Before the fix this test deadlocks: the blocked scan ran inside the
        GUI thread, so the timer below could never fire.
        """
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from app.devices.device_manager import DeviceManager

        release = threading.Event()
        scan_started = threading.Event()

        def stalled_enum():
            scan_started.set()
            release.wait(10.0)  # simulate a device that never answers
            return []

        monkeypatch.setattr(scanner_module, "enumerate_volumes", stalled_enum)
        monkeypatch.setattr(
            scanner_module, "detect_all_kindle_devices", lambda volumes: []
        )

        manager = DeviceManager()
        manager.start()

        try:
            assert scan_started.wait(3.0), "scan never started"

            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(50)

            # Pump the GUI event loop while the scan is still blocked.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)

            timer.stop()
            assert len(ticks) >= 5, (
                "GUI timer did not fire while the scan was blocked — "
                f"only {len(ticks)} ticks"
            )
        finally:
            release.set()
            manager.stop()


# ── Results still flow through ───────────────────────────────────────────────


class TestResultsFlowThrough:
    def test_scan_results_reach_the_manager(self, scanner_module, monkeypatch):
        """Detection still works end-to-end, just asynchronously."""
        from pathlib import Path

        from app.devices.device_manager import DeviceManager
        from app.devices.device_matcher import DetectionResult

        fake = DetectionResult(
            is_kindle=True,
            confidence=9,
            root_path=Path("E:/"),
            target_path=Path("E:/documents"),
            device_name="Fake Kindle",
        )
        monkeypatch.setattr(scanner_module, "enumerate_volumes", lambda: [])
        monkeypatch.setattr(
            scanner_module, "detect_all_kindle_devices", lambda volumes: [fake]
        )

        manager = DeviceManager()
        manager.start()
        try:
            assert _pump(lambda: manager.device_count == 1, timeout_s=3.0), (
                "scan result never reached the manager"
            )
            assert manager.current_device is not None
            assert manager.has_device is True
        finally:
            manager.stop()

    def test_results_are_ignored_after_stop(self, scanner_module, monkeypatch):
        """A scan finishing during shutdown must not touch policy state."""
        from app.devices.device_manager import DeviceManager

        _no_devices(monkeypatch, scanner_module)

        manager = DeviceManager()
        manager.start()
        manager.stop()

        # Deliver a scan as if the worker had just finished one.
        manager._on_scan_completed([])
        assert manager.device_count == 0

    def test_poll_still_works_synchronously(self, scanner_module, monkeypatch):
        """_poll() remains a deterministic single-cycle entry point."""
        from app.devices.device_manager import DeviceManager

        _no_devices(monkeypatch, scanner_module)

        manager = DeviceManager()
        manager._poll()
        assert manager.has_device is False


# ── Shutdown behaviour ───────────────────────────────────────────────────────


class TestShutdown:
    def test_stop_returns_promptly_when_the_device_answers(
        self, scanner_module, monkeypatch
    ):
        from app.devices.device_manager import DeviceManager

        _no_devices(monkeypatch, scanner_module)

        manager = DeviceManager()
        manager.start()
        began = time.monotonic()
        manager.stop()
        elapsed = time.monotonic() - began

        assert elapsed < 1.0, f"stop() took {elapsed:.2f}s"

    def test_stop_parks_a_stuck_thread_instead_of_terminating(
        self, scanner_module, monkeypatch
    ):
        """A thread stuck in device I/O is parked, never terminate()d."""
        from app.devices.device_manager import DeviceManager

        release = threading.Event()
        scan_entered = threading.Event()

        def stalled_enum():
            scan_entered.set()
            release.wait(10.0)
            return []

        monkeypatch.setattr(scanner_module, "enumerate_volumes", stalled_enum)
        monkeypatch.setattr(
            scanner_module, "detect_all_kindle_devices", lambda volumes: []
        )

        manager = DeviceManager()
        manager.start()
        worker = manager._scanner
        assert worker is not None

        try:
            # Must wait until the thread is *inside* the blocked call —
            # otherwise stop() wins the race and the thread exits cleanly.
            assert scan_entered.wait(3.0), "scan never entered the blocked call"

            stopped = worker.stop(timeout_ms=200)
            assert stopped is False, "a stalled scan cannot stop on its own"
            assert worker in scanner_module._PARKED_WORKERS, (
                "stuck worker must be parked so Qt never destroys a running "
                "QThread"
            )
        finally:
            release.set()
            manager.stop()

    def test_worker_stops_cleanly_when_not_blocked(
        self, scanner_module, monkeypatch
    ):
        _no_devices(monkeypatch, scanner_module)

        worker = scanner_module.DeviceScanWorker(interval_ms=50)
        worker.start()
        assert worker.stop(timeout_ms=3000) is True
        assert worker.isFinished()
