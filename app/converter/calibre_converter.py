"""Calibre ebook-convert integration.

Wraps calibre's ebook-convert CLI tool for ebook format conversion.
Uses subprocess.Popen for cancelable long-running conversions.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

# ── QSettings keys ───────────────────────────────────────────────────────────

SETTINGS_KEY_EBOOK_CONVERT = "ebook_convert_path"

# Default QSettings organization/app names. These can be overridden for testing.
_QSETTINGS_ORG = "KindleTransfer"
_QSETTINGS_APP = "KindleTransfer"


def set_qsettings_names(org: str, app: str) -> None:
    """Override QSettings organization and application names.

    Useful for tests to isolate from production settings.
    """
    global _QSETTINGS_ORG, _QSETTINGS_APP
    _QSETTINGS_ORG = org
    _QSETTINGS_APP = app


def _get_settings() -> QSettings:
    return QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)


# ── QSettings helpers ────────────────────────────────────────────────────────

def get_saved_ebook_convert_path() -> str | None:
    """Get the user-saved ebook-convert path from QSettings."""
    settings = _get_settings()
    value = settings.value(SETTINGS_KEY_EBOOK_CONVERT, None)
    if value and isinstance(value, str) and value.strip():
        return value.strip()
    return None


def save_ebook_convert_path(path: str) -> None:
    """Save the ebook-convert path to QSettings."""
    settings = _get_settings()
    settings.setValue(SETTINGS_KEY_EBOOK_CONVERT, path)
    settings.sync()
    logger.info("Saved ebook-convert path: %s", path)


def clear_saved_ebook_convert_path() -> None:
    """Remove the saved ebook-convert path from QSettings."""
    settings = _get_settings()
    settings.remove(SETTINGS_KEY_EBOOK_CONVERT)
    settings.sync()


# ── Detection ────────────────────────────────────────────────────────────────

def verify_ebook_convert(executable_path: str) -> bool:
    """Verify that an ebook-convert executable is actually runnable.

    Runs ``ebook-convert --version`` and checks the return code.
    Does NOT parse the version string — only checks returncode == 0.

    Args:
        executable_path: Path to the suspected ebook-convert executable.

    Returns:
        True if the executable runs and returns exit code 0.
    """
    if not Path(executable_path).exists():
        return False

    try:
        result = subprocess.run(
            [str(executable_path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            version_output = (result.stdout or "").strip()
            logger.info("ebook-convert verified: %s", version_output[:200])
            return True
        else:
            logger.warning(
                "ebook-convert --version returned %d: %s",
                result.returncode,
                (result.stderr or "")[:200],
            )
            return False
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("Failed to verify ebook-convert at %s: %s", executable_path, e)
        return False


def find_ebook_convert() -> str | None:
    """Find the ebook-convert executable.

    Search order:
    1. User-saved custom path (QSettings).
    2. ``shutil.which("ebook-convert")``
    3. ``shutil.which("ebook-convert.exe")``
    4. Common calibre installation directories on Windows.

    Every candidate is verified by running ``--version``.

    Returns:
        Path to a verified ebook-convert executable, or None.
    """
    # 1. User-saved path
    saved = get_saved_ebook_convert_path()
    if saved and verify_ebook_convert(saved):
        logger.info("Using saved ebook-convert: %s", saved)
        return saved
    elif saved:
        logger.warning("Saved ebook-convert path is no longer valid: %s", saved)

    # 2. PATH: ebook-convert
    path = shutil.which("ebook-convert")
    if path and verify_ebook_convert(path):
        logger.info("Found ebook-convert in PATH: %s", path)
        return path

    # 3. PATH: ebook-convert.exe
    path = shutil.which("ebook-convert.exe")
    if path and verify_ebook_convert(path):
        logger.info("Found ebook-convert.exe in PATH: %s", path)
        return path

    # 4. Common installation paths on Windows
    common_dirs = [
        Path(r"C:\Program Files\Calibre2"),
        Path(r"C:\Program Files (x86)\Calibre2"),
        Path.home() / "AppData" / "Local" / "calibre" / "Calibre2",
        Path.home() / "AppData" / "Local" / "Programs" / "calibre" / "Calibre2",
        Path.home() / "scoop" / "apps" / "calibre" / "current",
        Path(r"C:\ProgramData\chocolatey\lib\calibre\tools"),
        Path.home() / "Calibre Portable" / "Calibre",
    ]

    for base_dir in common_dirs:
        exe = base_dir / "ebook-convert.exe"
        if exe.exists() and verify_ebook_convert(str(exe)):
            logger.info("Found ebook-convert at: %s", exe)
            return str(exe)

    logger.warning("ebook-convert not found in any known location")
    return None


def is_ebook_convert_available() -> bool:
    """Check if ebook-convert is available."""
    return find_ebook_convert() is not None


# ── Conversion status ────────────────────────────────────────────────────────

class ConversionStatus(Enum):
    """Outcome of an ebook conversion operation."""
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()
    DRM_PROTECTED = auto()


class ConversionError(Exception):
    """Raised when ebook conversion fails."""


class ConversionResult:
    """Result of an ebook conversion operation."""

    def __init__(
        self,
        status: ConversionStatus,
        input_path: Path,
        output_path: Path | None = None,
        error_message: str | None = None,
        elapsed_seconds: float = 0.0,
    ) -> None:
        self.status: ConversionStatus = status
        self.input_path: Path = input_path
        self.output_path: Path | None = output_path
        self.error_message: str | None = error_message
        self.elapsed_seconds: float = elapsed_seconds

    @property
    def success(self) -> bool:
        return self.status == ConversionStatus.SUCCESS

    @property
    def cancelled(self) -> bool:
        return self.status == ConversionStatus.CANCELLED

    @property
    def drm_detected(self) -> bool:
        return self.status == ConversionStatus.DRM_PROTECTED


# ── Conversion ───────────────────────────────────────────────────────────────

def _read_stream(stream, output_list: list[str]) -> None:
    """Read lines from a stream into a list (for use in a background thread)."""
    try:
        for line in iter(stream.readline, ""):
            output_list.append(line)
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def convert_ebook(
    input_path: Path,
    source_format: str,
    target_format: str,
    ebook_convert_path: str,
    output_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> ConversionResult:
    """Convert an ebook using ebook-convert.

    Uses subprocess.Popen for cancelable conversion. The caller can pass a
    ``threading.Event`` to request cancellation: when set, the child process
    is terminated.

    Args:
        input_path: Path to the source ebook.
        source_format: Source format extension (e.g. '.epub').
        target_format: Target format extension (e.g. '.azw3').
        ebook_convert_path: Path to the ebook-convert executable.
        output_dir: Directory for the output file. If None, a temporary
            directory is created (and the caller is responsible for cleanup).
        cancel_event: Optional threading.Event; set it to cancel the conversion.

    Returns:
        ConversionResult with status, output path, and timing.
    """
    import time as time_module

    if not input_path.exists():
        return ConversionResult(
            status=ConversionStatus.FAILED,
            input_path=input_path,
            error_message=f"Input file not found: {input_path}",
        )

    if not Path(ebook_convert_path).exists():
        return ConversionResult(
            status=ConversionStatus.FAILED,
            input_path=input_path,
            error_message=f"ebook-convert not found at: {ebook_convert_path}",
        )

    # Prepare output path
    owned_temp_dir: Path | None = None
    if output_dir is None:
        owned_temp_dir = Path(tempfile.mkdtemp(prefix="kindle_transfer_"))
        output_dir = owned_temp_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    output_name = input_path.stem + target_format
    output_path = output_dir / output_name

    # Build command
    cmd = [
        str(ebook_convert_path),
        str(input_path),
        str(output_path),
    ]

    # Set working directory to the ebook-convert directory so calibre
    # can find its dependent DLLs and Python modules.
    calibre_dir = str(Path(ebook_convert_path).parent)

    logger.info("Running conversion: %s", subprocess.list2cmdline(cmd))
    start_time = time_module.monotonic()

    process: subprocess.Popen | None = None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    try:
        # Start the process — use utf-8 encoding with replace to handle
        # calibre's mixed-encoding output on Windows (prevents GBK decode errors).
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            cwd=calibre_dir,
            # No shell=True, no timeout — runs until completion or cancel
        )

        # Background threads to read stdout/stderr (prevents pipe buffer deadlock)
        stdout_thread = threading.Thread(
            target=_read_stream, args=(process.stdout, stdout_lines), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_read_stream, args=(process.stderr, stderr_lines), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

        # Poll for completion or cancellation
        poll_interval = 0.2  # seconds
        while process.poll() is None:
            if cancel_event and cancel_event.is_set():
                logger.info("Cancelling conversion of %s", input_path.name)
                _terminate_process(process)
                elapsed = time_module.monotonic() - start_time
                # Wait for reader threads
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
                logger.info(
                    "Conversion cancelled: %s (%.1fs)", input_path.name, elapsed
                )
                return ConversionResult(
                    status=ConversionStatus.CANCELLED,
                    input_path=input_path,
                    error_message="用户取消了转换。",
                    elapsed_seconds=elapsed,
                )

            try:
                process.wait(timeout=poll_interval)
            except subprocess.TimeoutExpired:
                continue

        # Process finished normally — wait for reader threads
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        elapsed = time_module.monotonic() - start_time
        returncode = process.returncode
        stderr_text = "".join(stderr_lines)
        stdout_text = "".join(stdout_lines)

        if returncode != 0:
            # Check for DRM indicators
            drm_keywords = ["drm", "encrypt", "protected", "digital rights"]
            combined = (stderr_text + stdout_text).lower()
            drm_detected = any(kw in combined for kw in drm_keywords)

            if drm_detected:
                error_msg = (
                    "该文件可能受到 DRM 或其他加密保护，无法转换。"
                    "本程序不会移除 DRM。"
                )
                status = ConversionStatus.DRM_PROTECTED
            else:
                error_msg = (
                    f"电子书转换失败。\n"
                    f"退出码: {returncode}\n"
                    f"详情已写入日志文件，请查看 logs/kindle_transfer.log"
                )
                status = ConversionStatus.FAILED

            logger.error(
                "Conversion failed (exit %s, %.1fs): %s",
                str(returncode),
                elapsed,
                stderr_text[:500],
            )

            return ConversionResult(
                status=status,
                input_path=input_path,
                error_message=error_msg,
                elapsed_seconds=elapsed,
            )

        # Verify output
        if not output_path.exists():
            return ConversionResult(
                status=ConversionStatus.FAILED,
                input_path=input_path,
                error_message="电子书转换失败：输出文件未生成。",
                elapsed_seconds=elapsed,
            )

        if output_path.stat().st_size == 0:
            return ConversionResult(
                status=ConversionStatus.FAILED,
                input_path=input_path,
                error_message="电子书转换失败：输出文件为空。",
                elapsed_seconds=elapsed,
            )

        logger.info(
            "Conversion succeeded: %s -> %s (%d bytes, %.1fs)",
            input_path.name,
            output_path.name,
            output_path.stat().st_size,
            elapsed,
        )

        return ConversionResult(
            status=ConversionStatus.SUCCESS,
            input_path=input_path,
            output_path=output_path,
            elapsed_seconds=elapsed,
        )

    except OSError as e:
        elapsed = time_module.monotonic() - start_time
        return ConversionResult(
            status=ConversionStatus.FAILED,
            input_path=input_path,
            error_message=f"无法运行 ebook-convert: {e}",
            elapsed_seconds=elapsed,
        )
    finally:
        # If we created a temp dir and the conversion failed, clean it up
        # (successful conversions keep the output file; caller cleans up)
        if owned_temp_dir is not None and output_path is not None:
            if not output_path.exists() or output_path.stat().st_size == 0:
                try:
                    shutil.rmtree(owned_temp_dir, ignore_errors=True)
                except OSError:
                    pass


def _terminate_process(process: subprocess.Popen) -> None:
    """Terminate a process gracefully, then force-kill if needed.

    Windows-compatible: uses terminate() then kill().
    """
    if process.poll() is not None:
        return  # Already finished

    try:
        process.terminate()
    except OSError:
        pass

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Process did not terminate, force-killing")
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.error("Process could not be killed")