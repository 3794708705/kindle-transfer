"""Tests for the calibre converter module.

All tests use fake executables or mock subprocess — no real calibre required.
Updated for V0.1.2 Popen-based cancelable API.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY

import pytest

from app.converter.calibre_converter import (
    find_ebook_convert,
    verify_ebook_convert,
    convert_ebook,
    ConversionResult,
    ConversionStatus,
    set_qsettings_names,
    save_ebook_convert_path,
    get_saved_ebook_convert_path,
    clear_saved_ebook_convert_path,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_fake_exe(dir_path: Path, name: str, exit_code: int = 0, stdout: str = "", stderr: str = "") -> Path:
    """Create a fake executable script that returns the given exit code and output."""
    if sys.platform == "win32":
        bat_path = dir_path / f"{name}.bat"
        lines = ["@echo off"]
        if stdout:
            lines.append(f"echo {stdout}")
        if stderr:
            lines.append(f"echo {stderr} >&2")
        lines.append(f"exit /b {exit_code}")
        bat_path.write_text("\n".join(lines), encoding="ascii")
        return bat_path
    else:
        exe_path = dir_path / name
        script = f"#!/bin/sh\necho '{stdout}'\necho '{stderr}' >&2\nexit {exit_code}\n"
        exe_path.write_text(script)
        exe_path.chmod(exe_path.stat().st_mode | stat.S_IEXEC)
        return exe_path


def _make_fake_convert_exe(dir_path: Path, exit_code: int = 0, stdout: str = "", stderr: str = "") -> Path:
    return _make_fake_exe(dir_path, "ebook-convert", exit_code, stdout, stderr)


def _make_mock_popen(returncode: int = 0, stdout_text: str = "", stderr_text: str = "",
                      delay: float = 0.0, create_output: Path | None = None,
                      output_content: str = "converted"):
    """Create a mock for subprocess.Popen that simulates a process.

    Args:
        returncode: Exit code to return.
        stdout_text: Text to return on stdout.
        stderr_text: Text to return on stderr.
        delay: Seconds to wait before "completing" (simulates processing time).
        create_output: If set, create this file before completing.
        output_content: Content to write to create_output if set.
    """
    def _mock_popen_factory(*args, **kwargs):
        mock_process = MagicMock()
        mock_process._real_returncode = returncode
        mock_process.returncode = None  # Running initially

        # Simulate stdout/stderr pipes
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [stdout_text, ""]
        mock_stderr = MagicMock()
        mock_stderr.readline.side_effect = [stderr_text, ""]

        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr

        poll_count = [0]

        def poll():
            poll_count[0] += 1
            if delay > 0 and poll_count[0] < 3:
                return None  # Still running
            # Create output file before "completing"
            if create_output is not None:
                create_output.parent.mkdir(parents=True, exist_ok=True)
                create_output.write_text(output_content)
            mock_process.returncode = returncode
            return returncode

        mock_process.poll = poll
        mock_process.wait = lambda timeout=None: returncode
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        return mock_process

    return _mock_popen_factory


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_qsettings():
    """Isolate QSettings for tests."""
    set_qsettings_names("KindleTransferTests", "KindleTransferTests_Converter")
    clear_saved_ebook_convert_path()
    yield
    clear_saved_ebook_convert_path()
    set_qsettings_names("KindleTransfer", "KindleTransfer")


# ── Tests: verify_ebook_convert ──────────────────────────────────────────────

class TestVerifyEbookConvert:
    """Tests for verify_ebook_convert."""

    def test_valid_executable(self, tmp_path):
        exe = _make_fake_convert_exe(tmp_path, exit_code=0, stdout="calibre")
        assert verify_ebook_convert(str(exe)) is True

    def test_invalid_executable_nonzero(self, tmp_path):
        exe = _make_fake_convert_exe(tmp_path, exit_code=1, stderr="error")
        assert verify_ebook_convert(str(exe)) is False

    def test_nonexistent_path(self):
        assert verify_ebook_convert("/nonexistent/ebook-convert.exe") is False

    def test_not_executable(self, tmp_path):
        not_exe = tmp_path / "not_an_exe.txt"
        not_exe.write_text("not executable")
        assert verify_ebook_convert(str(not_exe)) is False


# ── Tests: QSettings persistence ─────────────────────────────────────────────

class TestQSettingsPersistence:
    """Tests for QSettings path persistence (isolated)."""

    def test_save_and_retrieve(self):
        clear_saved_ebook_convert_path()
        save_ebook_convert_path(r"C:\Test\ebook-convert.exe")
        assert get_saved_ebook_convert_path() == r"C:\Test\ebook-convert.exe"

    def test_clear(self):
        save_ebook_convert_path(r"C:\Test\ebook-convert.exe")
        clear_saved_ebook_convert_path()
        assert get_saved_ebook_convert_path() is None


# ── Tests: convert_ebook with Popen mocking ──────────────────────────────────

class TestConvertEbook:
    """Tests for convert_ebook with mocked Popen."""

    def test_conversion_success(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("mock epub content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output_path = output_dir / "test.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="OK", stderr_text="",
                create_output=output_path, output_content="converted azw3 content",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.SUCCESS
            assert result.success is True
            assert result.output_path is not None
            assert result.output_path.suffix == ".azw3"

    def test_conversion_failure_nonzero(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=1)

        src = tmp_path / "test.epub"
        src.write_text("mock epub content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=1, stdout_text="", stderr_text="Conversion error",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.FAILED
            assert result.success is False
            assert "电子书转换失败" in result.error_message

    def test_drm_detection(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=1)

        src = tmp_path / "test.epub"
        src.write_text("mock epub content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=1, stdout_text="",
                stderr_text="This file is DRM protected and cannot be converted",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.DRM_PROTECTED
            assert result.drm_detected is True
            assert "DRM" in result.error_message

    def test_output_not_created(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("mock epub content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.Popen") as mock_popen:
            # Return success but don't create output file
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="OK", stderr_text="",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.FAILED
            assert "输出文件未生成" in result.error_message

    def test_input_not_found(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir)

        src = tmp_path / "nonexistent.epub"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
        )
        assert result.status == ConversionStatus.FAILED
        assert "not found" in result.error_message.lower()

    def test_ebook_convert_not_found(self, tmp_path):
        src = tmp_path / "test.epub"
        src.write_text("content")

        result = convert_ebook(
            src, ".epub", ".azw3", "/nonexistent/ebook-convert.exe",
        )
        assert result.status == ConversionStatus.FAILED
        assert "not found" in result.error_message.lower()

    def test_output_path_with_spaces(self, tmp_path):
        exe_dir = tmp_path / "fake calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "my book.epub"
        src.write_text("content")

        output_dir = tmp_path / "output dir with spaces"
        output_dir.mkdir()
        output_path = output_dir / "my book.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="",
                create_output=output_path,
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.SUCCESS

    def test_output_path_with_chinese(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "三体.epub"
        src.write_text("content")

        output_dir = tmp_path / "输出目录"
        output_dir.mkdir()
        output_path = output_dir / "三体.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="",
                create_output=output_path,
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status == ConversionStatus.SUCCESS

    def test_no_shell_used(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output_path = output_dir / "test.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="",
                create_output=output_path,
            )

            convert_ebook(src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir)

            call_kwargs = mock_popen.call_args
            assert call_kwargs is not None
            shell_value = call_kwargs[1].get("shell", False)
            assert shell_value is False, f"shell={shell_value}, expected False"

    def test_conversion_failure_not_marked_success(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=1)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=1, stdout_text="", stderr_text="error",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )
            assert result.status != ConversionStatus.SUCCESS
            assert result.success is False

    def test_verify_compares_azw3_not_epub(self, tmp_path):
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("short epub")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output_path = output_dir / "test.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="",
                create_output=output_path,
                output_content="longer converted azw3 content here",
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir,
            )

            assert result.status == ConversionStatus.SUCCESS
            assert result.output_path is not None
            assert result.output_path.suffix == ".azw3"
            assert result.output_path != src
            assert result.output_path.name == "test.azw3"

    def test_no_timeout_in_popen(self, tmp_path):
        """Verify that Popen is NOT called with a fixed timeout."""
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        output_path = output_dir / "test.azw3"

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="",
                create_output=output_path,
            )

            convert_ebook(src, ".epub", ".azw3", str(fake_exe), output_dir=output_dir)

            call_kwargs = mock_popen.call_args
            assert call_kwargs is not None
            # Popen should not have a timeout parameter
            assert "timeout" not in call_kwargs[1], (
                f"timeout={call_kwargs[1].get('timeout')} found in Popen call, "
                "expected no timeout"
            )


# ── Tests: Cancellation ──────────────────────────────────────────────────────

class TestConvertEbookCancel:
    """Tests for cancelable conversion."""

    def test_cancel_before_start(self, tmp_path):
        """Cancel event already set before conversion starts."""
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        cancel_event = threading.Event()
        cancel_event.set()  # Already cancelled

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="", delay=0.5,
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe),
                output_dir=output_dir, cancel_event=cancel_event,
            )
            assert result.status == ConversionStatus.CANCELLED
            assert result.cancelled is True
            assert "取消" in result.error_message

    def test_cancel_during_conversion(self, tmp_path):
        """Cancel while conversion is running."""
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        cancel_event = threading.Event()

        def slow_popen(*args, **kwargs):
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_stdout = MagicMock()
            mock_stdout.readline.side_effect = ["", ""]
            mock_stderr = MagicMock()
            mock_stderr.readline.side_effect = ["", ""]
            mock_process.stdout = mock_stdout
            mock_process.stderr = mock_stderr

            poll_count = [0]

            def poll():
                poll_count[0] += 1
                if poll_count[0] < 2:
                    # Set cancel event after first poll
                    cancel_event.set()
                    return None
                return -15  # Simulate terminated

            mock_process.poll = poll
            mock_process.wait = MagicMock()
            mock_process.terminate = MagicMock()
            mock_process.kill = MagicMock()
            return mock_process

        with patch("subprocess.Popen", side_effect=slow_popen):
            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe),
                output_dir=output_dir, cancel_event=cancel_event,
            )
            assert result.status == ConversionStatus.CANCELLED
            assert result.cancelled is True

    def test_cancel_distinct_from_error(self, tmp_path):
        """Cancelled status should be distinct from FAILED and DRM_PROTECTED."""
        exe_dir = tmp_path / "fake_calibre"
        exe_dir.mkdir()
        fake_exe = _make_fake_convert_exe(exe_dir, exit_code=0)

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        cancel_event = threading.Event()
        cancel_event.set()  # Already cancelled

        # Use delay > 0 so the process is "running" and cancel is checked
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = _make_mock_popen(
                returncode=0, stdout_text="", stderr_text="", delay=0.1,
            )

            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe),
                output_dir=output_dir, cancel_event=cancel_event,
            )

            assert result.cancelled is True
            assert result.drm_detected is False
            assert result.success is False
            assert result.status == ConversionStatus.CANCELLED


# ── Tests: Real subprocess with fake batch files ─────────────────────────────

class TestConvertEbookRealSubprocess:
    """Tests that actually run a fake batch file through subprocess."""

    def test_real_subprocess_success(self, tmp_path):
        bat_content = f"""@echo off
echo calibre 7.0 > "{tmp_path / 'output' / 'test.azw3'}"
exit /b 0
"""
        bat_path = tmp_path / "ebook-convert.bat"
        bat_path.write_text(bat_content)

        src = tmp_path / "test.epub"
        src.write_text("mock epub content here")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            src, ".epub", ".azw3", str(bat_path), output_dir=output_dir,
        )

        assert result.status == ConversionStatus.SUCCESS
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.output_path.stat().st_size > 0

    def test_real_subprocess_failure(self, tmp_path):
        bat_content = """@echo off
echo Error: DRM protected file >&2
exit /b 1
"""
        bat_path = tmp_path / "ebook-convert.bat"
        bat_path.write_text(bat_content)

        src = tmp_path / "test.epub"
        src.write_text("mock epub")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            src, ".epub", ".azw3", str(bat_path), output_dir=output_dir,
        )

        assert result.status == ConversionStatus.DRM_PROTECTED
        assert result.drm_detected is True

    def test_real_subprocess_cancel(self, tmp_path):
        """Test cancellation with a real long-running batch file."""
        # Create a batch that sleeps for a long time
        bat_content = """@echo off
ping -n 30 127.0.0.1 > nul
exit /b 0
"""
        bat_path = tmp_path / "ebook-convert.bat"
        bat_path.write_text(bat_content)

        src = tmp_path / "test.epub"
        src.write_text("mock epub")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        cancel_event = threading.Event()

        # Cancel after a short delay in a background thread
        def cancel_after_delay():
            time.sleep(0.5)
            cancel_event.set()

        cancel_thread = threading.Thread(target=cancel_after_delay, daemon=True)
        cancel_thread.start()

        result = convert_ebook(
            src, ".epub", ".azw3", str(bat_path),
            output_dir=output_dir, cancel_event=cancel_event,
        )

        assert result.status == ConversionStatus.CANCELLED
        assert result.cancelled is True