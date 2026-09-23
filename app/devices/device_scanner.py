"""Device scanning on a background thread.

Why this module exists
----------------------
Scanning is not metadata-only work.  `_score_volume` probes the *device*
itself — it stats `documents/` and several Kindle marker folders — so every
scan performs real filesystem I/O against the Kindle.

That scan used to run on a 2000 ms QTimer owned by the GUI thread.  A single
blocked `stat()` on a stalled USB connection was therefore enough to freeze
the whole window until Windows declared the application hung (Application
Hang / AppHangB1).  The user could not even close it.

Moving the blocking part onto its own thread means:

* the window keeps repainting and stays closable even if the device never
  answers, and
* the GUI thread only ever receives a finished candidate list, so all
  selection policy state stays single-threaded on the GUI side.

`QThread.terminate()` is deliberately never used — it cannot interrupt a
blocking syscall, and the rest of this codebase avoids it too.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QThread, Signal

from app.devices.device_matcher import DetectionResult, detect_all_kindle_devices
from app.devices.windows_detector import enumerate_volumes

logger = logging.getLogger(__name__)

# Poll cadence.  Matches the interval the GUI-thread timer used to run at.
DEFAULT_INTERVAL_MS = 2000

# How long stop() is willing to wait for an in-flight scan to come back.
DEFAULT_STOP_TIMEOUT_MS = 1500

# Workers that did not stop in time are parked here.
#
# A scan stuck inside a blocking device call cannot be interrupted, so the
# only safe options are to leak the thread or to let Qt destroy a running
# QThread (which aborts the process).  Leaking is strictly better: the thread
# is unreachable, the process still exits, and the OS reaps it.
_PARKED_WORKERS: list["DeviceScanWorker"] = []


class DeviceScanWorker(QThread):
    """Repeatedly scans for Kindle devices off the GUI thread.

    ``scan_completed`` carries the candidate list.  The receiver lives in the
    GUI thread, so Qt delivers it as a queued call and the policy code runs
    there — never on this thread.
    """

    scan_completed = Signal(list)
    scan_failed = Signal(str)

    def __init__(
        self,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._interval_s = interval_ms / 1000.0
        self._stop_event = threading.Event()

    # ── Thread body ──────────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("Device scan thread started (interval %.1fs)", self._interval_s)

        # Scan immediately, then wait out the interval.  Waiting on the event
        # (rather than sleeping) means stop() takes effect at once instead of
        # after up to one full interval.
        while not self._stop_event.is_set():
            try:
                volumes = enumerate_volumes()
                candidates = detect_all_kindle_devices(volumes)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.exception("Device scan failed")
                self.scan_failed.emit(str(exc))
            else:
                self.scan_completed.emit(candidates)

            self._stop_event.wait(self._interval_s)

        logger.info("Device scan thread exiting")

    # ── Shutdown ─────────────────────────────────────────────────────────────

    def stop(self, timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS) -> bool:
        """Ask the thread to stop and wait briefly for it.

        Returns True if the thread exited.  Returns False if it is still stuck
        inside device I/O — in that case it is parked rather than terminated,
        so a stalled device can never turn into a hung or aborted process.
        """
        self._stop_event.set()

        if self.wait(timeout_ms):
            return True

        if self not in _PARKED_WORKERS:
            _PARKED_WORKERS.append(self)
        logger.warning(
            "Device scan thread did not stop within %d ms — the device is "
            "probably not responding. Parking the thread; the window stays "
            "usable and the process will still exit.",
            timeout_ms,
        )
        return False
