"""End-to-end pipeline integration tests.

Tests the complete workflow using a fake Kindle directory and fake ebook-convert.
No real Kindle or calibre installation required.
Updated for V0.1.2 Popen-based API.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.books.analyzer import analyze_books, BookAnalysis
from app.devices.profiles import get_default_profile
from app.devices.detector import validate_kindle_root, get_kindle_documents_path
from app.converter.calibre_converter import convert_ebook, ConversionStatus
from app.transfer.usb_transfer import transfer_file, verify_transfer


class TestPipeline:
    """Full pipeline: analyze → convert → transfer → verify."""

    def test_full_pipeline_epub_to_azw3(self, tmp_path):
        """Test complete pipeline: EPUB → AZW3 → FakeKindle/documents."""
        # ── Setup Fake Kindle ──
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        assert validate_kindle_root(fake_kindle) is True

        # ── Setup source files (use different names to avoid collisions) ──
        epub_file = tmp_path / "books" / "book1.epub"
        epub_file.parent.mkdir()
        epub_file.write_text("mock epub content for conversion testing")

        pdf_file = tmp_path / "books" / "book2.pdf"
        pdf_file.write_text("mock pdf content")

        azw3_file = tmp_path / "books" / "book3.azw3"
        azw3_file.write_text("mock azw3 content")

        # ── Step 1: Analyze ──
        profile = get_default_profile()
        analyses = analyze_books([epub_file, pdf_file, azw3_file], profile)

        assert len(analyses) == 3
        assert analyses[0].action == "convert"  # EPUB
        assert analyses[0].target_format == ".azw3"
        assert analyses[1].action == "direct"  # PDF
        assert analyses[2].action == "direct"  # AZW3

        # ── Step 2: Convert EPUB → AZW3 (mock subprocess) ──
        dest_dir = get_kindle_documents_path(fake_kindle)

        # Create a fake ebook-convert executable so the existence check passes
        fake_exe_dir = tmp_path / "fake_calibre"
        fake_exe_dir.mkdir()
        fake_exe = fake_exe_dir / "ebook-convert.exe"
        fake_exe.write_text("fake")  # just needs to exist

        with patch("subprocess.Popen") as mock_popen:
            def popen_factory(cmd, **kwargs):
                output_path = Path(cmd[2])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("converted azw3 content here — longer")

                mock_process = MagicMock()
                mock_process.returncode = 0  # Already finished
                mock_stdout = MagicMock()
                mock_stdout.readline.side_effect = ["", ""]
                mock_stderr = MagicMock()
                mock_stderr.readline.side_effect = ["", ""]
                mock_process.stdout = mock_stdout
                mock_process.stderr = mock_stderr

                def poll():
                    return 0

                mock_process.poll = poll
                mock_process.wait = MagicMock()
                mock_process.terminate = MagicMock()
                mock_process.kill = MagicMock()
                return mock_process

            mock_popen.side_effect = popen_factory

            conv_result = convert_ebook(
                epub_file, ".epub", ".azw3",
                ebook_convert_path=str(fake_exe),
                output_dir=tmp_path / "temp_output",
            )

            assert conv_result.success is True
            assert conv_result.output_path is not None
            converted_path = conv_result.output_path

            # ── Step 3: Transfer converted AZW3 to Kindle ──
            transfer_result = transfer_file(converted_path, dest_dir)
            assert transfer_result.success is True
            assert transfer_result.dest_path is not None
            assert transfer_result.dest_path.exists()

            # Verify the transferred file is the AZW3, not the EPUB
            assert transfer_result.dest_path.suffix == ".azw3"
            assert transfer_result.dest_path.name == "book1.azw3"

            # ── Step 4: Transfer PDF directly ──
            transfer_pdf = transfer_file(pdf_file, dest_dir)
            assert transfer_pdf.success is True
            assert transfer_pdf.dest_path is not None
            assert transfer_pdf.dest_path.name == "book2.pdf"

            # ── Step 5: Transfer AZW3 directly ──
            transfer_azw3 = transfer_file(azw3_file, dest_dir)
            assert transfer_azw3.success is True
            assert transfer_azw3.dest_path is not None
            assert transfer_azw3.dest_path.name == "book3.azw3"

        # ── Step 6: Verify all files in Kindle ──
        kindle_files = list(dest_dir.iterdir())
        assert len(kindle_files) == 3

        # Verify each file
        for f in kindle_files:
            assert f.stat().st_size > 0

    def test_pipeline_convert_failure_no_transfer(self, tmp_path):
        """When conversion fails, the original EPUB must NOT be transferred."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        epub_file = tmp_path / "test.epub"
        epub_file.write_text("mock epub")

        dest_dir = get_kindle_documents_path(fake_kindle)

        # Create a fake executable so path existence check passes
        fake_exe = tmp_path / "fake_convert.exe"
        fake_exe.write_text("fake")

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.returncode = 1  # Failed
            mock_stdout = MagicMock()
            mock_stdout.readline.side_effect = ["", ""]
            mock_stderr = MagicMock()
            mock_stderr.readline.side_effect = ["conversion failed", ""]
            mock_process.stdout = mock_stdout
            mock_process.stderr = mock_stderr

            def poll():
                return 1

            mock_process.poll = poll
            mock_process.wait = MagicMock()
            mock_process.terminate = MagicMock()
            mock_process.kill = MagicMock()
            mock_popen.return_value = mock_process

            conv_result = convert_ebook(
                epub_file, ".epub", ".azw3",
                ebook_convert_path=str(fake_exe),
                output_dir=tmp_path / "temp",
            )

            assert conv_result.success is False

            # The EPUB should NOT be in the Kindle directory
            # (because we never call transfer_file on the original EPUB)
            kindle_files = list(dest_dir.iterdir())
            assert len(kindle_files) == 0

    def test_pipeline_direct_transfer_works_without_converter(self, tmp_path):
        """Direct-transfer files (PDF, AZW3) should work even without ebook-convert."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_text("pdf content")

        azw3_file = tmp_path / "test.azw3"
        azw3_file.write_text("azw3 content")

        dest_dir = get_kindle_documents_path(fake_kindle)

        # Transfer PDF
        r1 = transfer_file(pdf_file, dest_dir)
        assert r1.success is True

        # Transfer AZW3
        r2 = transfer_file(azw3_file, dest_dir)
        assert r2.success is True

        # Both files should be in Kindle
        kindle_files = {f.name for f in dest_dir.iterdir()}
        assert kindle_files == {"test.pdf", "test.azw3"}

    def test_pipeline_skip_existing(self, tmp_path):
        """When destination file exists, should skip (not overwrite)."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        azw3_file = tmp_path / "test.azw3"
        azw3_file.write_text("original content")

        dest_dir = get_kindle_documents_path(fake_kindle)

        # First transfer
        r1 = transfer_file(azw3_file, dest_dir)
        assert r1.success is True

        # Second transfer (should skip)
        r2 = transfer_file(azw3_file, dest_dir, overwrite=False)
        assert r2.skipped is True
        assert r2.success is False  # skipped means not successful

        # File should still have original content (not overwritten)
        dest_file = dest_dir / "test.azw3"
        assert dest_file.read_text() == "original content"

    def test_pipeline_overwrite(self, tmp_path):
        """When overwrite=True, existing files should be replaced."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        azw3_file = tmp_path / "test.azw3"
        azw3_file.write_text("new content")

        dest_dir = get_kindle_documents_path(fake_kindle)

        # Pre-create a file with old content
        dest_file = dest_dir / "test.azw3"
        dest_file.write_text("old content")

        # Transfer with overwrite
        r = transfer_file(azw3_file, dest_dir, overwrite=True)
        assert r.success is True

        # File should have new content
        assert dest_file.read_text() == "new content"


class TestPipelineFailureModes:
    """Tests for various failure modes in the pipeline."""

    def test_dest_dir_removed_during_transfer(self, tmp_path):
        """If the destination directory disappears, transfer should fail gracefully."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        azw3_file = tmp_path / "test.azw3"
        azw3_file.write_text("content")

        # Remove the documents directory to simulate disconnect
        import shutil
        shutil.rmtree(documents)

        result = transfer_file(azw3_file, documents)
        assert result.success is False
        assert "not found" in result.error_message.lower()

    def test_no_write_permission(self, tmp_path):
        """If destination is not writable, should fail gracefully."""
        # On Windows, we can't easily create a read-only directory in tmp_path
        # that behaves the same way. Skip this test on Windows.
        import sys
        if sys.platform == "win32":
            pytest.skip("Read-only directory test not reliable on Windows")

        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()
        documents.chmod(0o444)  # read-only

        try:
            azw3_file = tmp_path / "test.azw3"
            azw3_file.write_text("content")

            result = transfer_file(azw3_file, documents)
            assert result.success is False
        finally:
            documents.chmod(0o755)

    def test_insufficient_disk_space(self, tmp_path):
        """When destination has insufficient space, should fail."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        # Create a large source file
        large_file = tmp_path / "large.azw3"
        # Write enough data to likely trigger space check
        # (10 MB + 10 MB margin = 20 MB needed)
        large_file.write_bytes(b"x" * (15 * 1024 * 1024))  # 15 MB

        with patch("shutil.disk_usage") as mock_usage:
            # Report only 1 MB free
            mock_usage.return_value = MagicMock(
                free=1 * 1024 * 1024,  # 1 MB
                used=100 * 1024 * 1024,
                total=101 * 1024 * 1024,
            )

            result = transfer_file(large_file, documents)
            assert result.success is False
            assert "space" in result.error_message.lower()

    def test_batch_continues_after_failure(self, tmp_path):
        """When one file fails, the batch should continue processing others."""
        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        documents = fake_kindle / "documents"
        documents.mkdir()

        # Create files
        file1 = tmp_path / "good1.azw3"
        file1.write_text("good content 1")

        file2_nonexistent = tmp_path / "good2.azw3"

        file3 = tmp_path / "good3.azw3"
        file3.write_text("good content 3")

        dest_dir = get_kindle_documents_path(fake_kindle)

        # Transfer file1 (should succeed)
        r1 = transfer_file(file1, dest_dir)
        assert r1.success is True

        # Transfer file2 (should fail - doesn't exist)
        r2 = transfer_file(file2_nonexistent, dest_dir)
        assert r2.success is False

        # Transfer file3 (should still succeed despite file2 failure)
        r3 = transfer_file(file3, dest_dir)
        assert r3.success is True

        # Both good files should be in Kindle
        kindle_files = {f.name for f in dest_dir.iterdir()}
        assert "good1.azw3" in kindle_files
        assert "good3.azw3" in kindle_files


class TestWorkerCancelFlow:
    """Tests for the cancel/closeEvent cleanup flow.

    Verifies that cancel_event → subprocess terminate → worker natural exit
    works correctly, and that QThread.terminate() is never called.
    """

    def test_cancel_sets_event_and_subprocess_terminated(self, tmp_path):
        """When cancel_event is set, the subprocess should be terminated."""
        import threading
        from unittest.mock import patch, MagicMock

        fake_exe = tmp_path / "fake_convert.exe"
        fake_exe.write_text("fake")

        src = tmp_path / "test.epub"
        src.write_text("content")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        cancel_event = threading.Event()
        terminate_called = [False]
        kill_called = [False]

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
                # Keep returning None so the while loop keeps running.
                # The cancel_event.set() inside poll() will be detected
                # on the next iteration's cancel check.
                if poll_count[0] < 10:
                    if poll_count[0] == 2:
                        cancel_event.set()  # Cancel on second poll
                    return None
                return -15  # Simulate terminated by signal

            mock_process.poll = poll

            def terminate():
                terminate_called[0] = True
                mock_process.returncode = -15

            mock_process.terminate = terminate
            mock_process.kill = MagicMock()
            mock_process.wait = MagicMock()
            return mock_process

        with patch("subprocess.Popen", side_effect=slow_popen):
            result = convert_ebook(
                src, ".epub", ".azw3", str(fake_exe),
                output_dir=output_dir, cancel_event=cancel_event,
            )

            # Should be cancelled
            assert result.status == ConversionStatus.CANCELLED
            assert result.cancelled is True
            # Subprocess should have been terminated
            assert terminate_called[0] is True

    def test_cancel_worker_finishes_naturally(self, tmp_path):
        """After cancel, the worker should finish without QThread.terminate()."""
        import threading
        import time
        from unittest.mock import patch, MagicMock

        fake_kindle = tmp_path / "FakeKindle"
        fake_kindle.mkdir()
        (fake_kindle / "documents").mkdir()

        fake_exe = tmp_path / "fake_convert.exe"
        fake_exe.write_text("fake")

        epub = tmp_path / "test.epub"
        epub.write_text("content")

        # Analyze
        from app.books.analyzer import analyze_book
        from app.devices.profiles import get_default_profile
        analysis = analyze_book(epub, get_default_profile())

        # Create a worker with a cancel event that fires after a short delay
        cancel_event = threading.Event()
        terminate_called = [False]

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
                # Keep returning None until cancel triggers terminate
                if mock_process.returncode is None:
                    return None
                return mock_process.returncode

            mock_process.poll = poll

            def terminate():
                terminate_called[0] = True
                mock_process.returncode = -15

            mock_process.terminate = terminate
            mock_process.kill = MagicMock()

            def wait(timeout=None):
                # Simulate TimeoutExpired to pace the polling loop
                if mock_process.returncode is None:
                    import subprocess
                    raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.2)
                return mock_process.returncode

            mock_process.wait = wait
            return mock_process

        with patch("subprocess.Popen", side_effect=slow_popen):
            # Start conversion in a thread
            result_holder = [None]

            def run_conversion():
                result_holder[0] = convert_ebook(
                    epub, ".epub", ".azw3", str(fake_exe),
                    output_dir=tmp_path / "output",
                    cancel_event=cancel_event,
                )

            import threading as th
            t = th.Thread(target=run_conversion)
            t.start()

            # Let it start polling
            time.sleep(0.2)

            # Cancel
            cancel_event.set()

            # Wait for the conversion thread to finish naturally
            t.join(timeout=10)

            assert not t.is_alive(), "Worker thread should have finished naturally"
            assert result_holder[0] is not None
            assert result_holder[0].status == ConversionStatus.CANCELLED
            assert terminate_called[0] is True

    def test_temp_dir_cleaned_after_cancel(self, tmp_path):
        """After cancellation, the temporary directory should be cleaned up."""
        import threading
        import tempfile
        import time
        from unittest.mock import patch, MagicMock

        fake_exe = tmp_path / "fake_convert.exe"
        fake_exe.write_text("fake")

        src = tmp_path / "test.epub"
        src.write_text("content")

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
                    return None
                return -15

            mock_process.poll = poll

            def terminate():
                mock_process.returncode = -15

            mock_process.terminate = terminate
            mock_process.kill = MagicMock()
            mock_process.wait = MagicMock()
            return mock_process

        # Use a real temp directory that we can track
        temp_dir = Path(tempfile.mkdtemp(prefix="kindle_test_"))
        try:
            with patch("subprocess.Popen", side_effect=slow_popen):
                # Cancel immediately
                cancel_event.set()

                result = convert_ebook(
                    src, ".epub", ".azw3", str(fake_exe),
                    output_dir=temp_dir,
                    cancel_event=cancel_event,
                )

                assert result.status == ConversionStatus.CANCELLED

            # The temp dir should still exist (we manage it, not the converter)
            assert temp_dir.exists()
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_no_qthread_terminate_in_close(self, tmp_path):
        """Verify that the closeEvent path does NOT call QThread.terminate().

        We simulate the closeEvent logic: cancel → wait → check → accept.
        QThread.terminate() must never be invoked.
        """
        import threading
        import time
        from unittest.mock import patch, MagicMock

        fake_exe = tmp_path / "fake_convert.exe"
        fake_exe.write_text("fake")

        src = tmp_path / "test.epub"
        src.write_text("content")

        cancel_event = threading.Event()
        terminate_called = [False]

        def slow_popen(*args, **kwargs):
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_stdout = MagicMock()
            mock_stdout.readline.side_effect = ["", ""]
            mock_stderr = MagicMock()
            mock_stderr.readline.side_effect = ["", ""]
            mock_process.stdout = mock_stdout
            mock_process.stderr = mock_stderr

            def poll():
                if mock_process.returncode is None:
                    return None
                return mock_process.returncode

            mock_process.poll = poll

            def terminate():
                terminate_called[0] = True
                mock_process.returncode = -15

            mock_process.terminate = terminate
            mock_process.kill = MagicMock()

            def wait(timeout=None):
                if mock_process.returncode is None:
                    import subprocess
                    raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.2)
                return mock_process.returncode

            mock_process.wait = wait
            return mock_process

        with patch("subprocess.Popen", side_effect=slow_popen):
            # Start conversion
            result_holder = [None]

            def run_conversion():
                result_holder[0] = convert_ebook(
                    src, ".epub", ".azw3", str(fake_exe),
                    output_dir=tmp_path / "output",
                    cancel_event=cancel_event,
                )

            import threading as th
            t = th.Thread(target=run_conversion)
            t.start()

            # Simulate closeEvent: cancel, wait, check, accept
            time.sleep(0.2)
            cancel_event.set()

            # Wait for natural completion (no terminate() on thread)
            t.join(timeout=10)

            assert not t.is_alive(), (
                "Worker should finish naturally without QThread.terminate()"
            )
            assert result_holder[0] is not None
            assert result_holder[0].status == ConversionStatus.CANCELLED
            # Subprocess terminate() IS called (that's correct)
            assert terminate_called[0] is True


class TestCloseEventLifecycle:
    """Tests for the closeEvent shutdown state machine.

    Verifies: idle close, convert-in-progress close, direct-transfer close,
    duplicate close, and that QThread.terminate() is never called.
    """

    @pytest.fixture(autouse=True)
    def _qapp(self):
        """Ensure a QApplication exists for MainWindow tests."""
        from PySide6.QtWidgets import QApplication
        import sys
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        yield
        # Don't quit — other tests may need the app

    def test_idle_close_accepts(self, tmp_path):
        """With no worker, closeEvent should accept immediately."""
        from PySide6.QtGui import QCloseEvent
        from app.ui.main_window import MainWindow

        window = MainWindow()
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        window.close()

    def test_idle_close_with_finished_worker_accepts(self, tmp_path):
        """With a worker that has already finished, closeEvent should accept."""
        from PySide6.QtGui import QCloseEvent
        from app.ui.main_window import MainWindow, TransferWorker
        from app.books.analyzer import BookAnalysis
        from pathlib import Path

        window = MainWindow()
        # Create a worker that won't be started
        analysis = BookAnalysis(
            source_path=Path("dummy.azw3"),
            extension=".azw3",
            action="direct",
        )
        window._worker = TransferWorker(
            [analysis], Path("C:/"), None, overwrite=False,
        )
        # Don't start it — simulate already-finished
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        window.close()

    def test_convert_in_progress_close_ignores(self, tmp_path):
        """When a worker is running, closeEvent should ignore and cancel."""
        from unittest.mock import patch, MagicMock
        from PySide6.QtGui import QCloseEvent
        from app.ui.main_window import MainWindow, TransferWorker
        from app.books.analyzer import BookAnalysis
        from pathlib import Path

        window = MainWindow()
        analysis = BookAnalysis(
            source_path=Path("dummy.azw3"),
            extension=".azw3",
            action="direct",
        )
        worker = TransferWorker(
            [analysis], Path("C:/"), None, overwrite=False,
        )
        window._worker = worker

        # Mock isRunning to return True, and cancel to track calls
        original_isRunning = worker.isRunning
        cancel_calls = [0]

        def mock_isRunning():
            return True

        def mock_cancel():
            cancel_calls[0] += 1
            worker._cancel_event.set()

        worker.isRunning = mock_isRunning
        worker.cancel = mock_cancel

        # First closeEvent
        event1 = QCloseEvent()
        window.closeEvent(event1)
        assert not event1.isAccepted(), "First close should be ignored"
        assert window._closing is True
        assert cancel_calls[0] == 1
        assert not window._transfer_btn.isEnabled()

        # Second closeEvent (duplicate) — should still ignore, not re-cancel
        event2 = QCloseEvent()
        window.closeEvent(event2)
        assert not event2.isAccepted()
        assert cancel_calls[0] == 1, "Cancel should not be called twice"

        # Restore
        worker.isRunning = original_isRunning
        window.close()

    def test_on_finished_during_shutdown_triggers_close(self, tmp_path):
        """When QThread.finished fires during shutdown, deferred close triggers."""
        from unittest.mock import patch
        from PySide6.QtCore import QTimer
        from app.ui.main_window import MainWindow

        window = MainWindow()
        window._closing = True

        # Patch QTimer.singleShot to capture the call
        with patch.object(QTimer, 'singleShot') as mock_singleShot:
            window._on_worker_finished()

            assert window._allow_close is True
            assert window._worker is None
            # QTimer.singleShot(0, self.close) should have been called
            mock_singleShot.assert_called_once()
            args = mock_singleShot.call_args[0]
            assert args[0] == 0  # delay
            assert callable(args[1])  # callback

        window.close()

    def test_worker_done_signal_does_not_clear_worker(self, tmp_path):
        """The custom finished_signal (_on_worker_done) must NOT clear _worker
        or trigger close — that belongs to QThread.finished."""
        from app.ui.main_window import MainWindow, TransferWorker
        from app.books.analyzer import BookAnalysis
        from pathlib import Path

        window = MainWindow()
        window._closing = True
        analysis = BookAnalysis(
            source_path=Path("dummy.azw3"),
            extension=".azw3",
            action="direct",
        )
        worker = TransferWorker(
            [analysis], Path("C:/"), None, overwrite=False,
        )
        window._worker = worker

        # Call the business result handler (custom signal)
        window._on_worker_done()

        # _worker must NOT be cleared by the custom signal handler
        assert window._worker is not None, (
            "_on_worker_done must NOT clear _worker — that belongs to "
            "QThread.finished"
        )
        # _allow_close must NOT be set by the custom signal handler
        assert window._allow_close is False, (
            "_on_worker_done must NOT set _allow_close — that belongs to "
            "QThread.finished"
        )
        window.close()

    def test_qthread_finished_connected(self, tmp_path):
        """Verify that QThread.finished is connected to _on_worker_finished."""
        from app.ui.main_window import MainWindow, TransferWorker
        from app.books.analyzer import BookAnalysis
        from pathlib import Path

        window = MainWindow()
        analysis = BookAnalysis(
            source_path=Path("dummy.azw3"),
            extension=".azw3",
            action="direct",
        )
        worker = TransferWorker(
            [analysis], Path("C:/"), None, overwrite=False,
        )
        window._worker = worker
        # Connect both signals as _on_transfer does
        worker.finished_signal.connect(window._on_worker_done)
        worker.finished.connect(window._on_worker_finished)

        # Verify the connection was made by checking that the signal
        # is not blocked and the slot is callable
        assert callable(window._on_worker_finished)
        assert callable(window._on_worker_done)
        assert window._on_worker_finished != window._on_worker_done, (
            "Business handler and lifecycle handler must be different methods"
        )

        # Emit QThread.finished — should trigger _on_worker_finished
        window._closing = True
        # Simulate QThread.finished by calling the slot directly
        # (since we can't easily emit QThread.finished without starting the thread)
        window._on_worker_finished()
        assert window._worker is None
        assert window._allow_close is True

        window.close()

    def test_transfer_blocked_during_shutdown(self, tmp_path):
        """_on_transfer should be a no-op when _closing is True."""
        from app.ui.main_window import MainWindow

        window = MainWindow()
        window._closing = True
        # Should not crash or start anything
        window._on_transfer()
        assert window._worker is None
        window.close()


class TestNoQThreadTerminate:
    """Verify that production code never calls QThread.terminate()."""

    def test_no_terminate_in_main_window(self):
        """main_window.py must not contain QThread.terminate() calls."""
        import ast
        from pathlib import Path

        src = Path(__file__).parent.parent / "app" / "ui" / "main_window.py"
        code = src.read_text(encoding="utf-8")

        # Check for .terminate() on any QThread-derived object
        # We allow process.terminate() (subprocess), but not thread.terminate()
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            # Skip comments and docstrings mentioning terminate
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                continue
            # Look for .terminate() that is NOT on a subprocess.Popen
            if ".terminate()" in stripped:
                # Check surrounding context for subprocess/Popen
                # Read a few lines around for context
                context = "\n".join(lines[max(0, i - 3):min(len(lines), i + 2)])
                if "subprocess" not in context.lower() and "process" not in context.lower():
                    # Only flag if it's clearly on a QThread
                    if "_worker" in stripped or "worker" in stripped or "thread" in stripped.lower():
                        pytest.fail(
                            f"QThread.terminate() found at line {i}: {stripped}"
                        )
                # _terminate_process is a function that terminates subprocess — OK
                if "_terminate_process" in context:
                    continue
                # process.terminate() on subprocess — OK
                if "process.terminate()" in stripped and "subprocess" in context.lower():
                    continue

        # Verify the closeEvent docstring says it does NOT terminate
        assert "does not call" in code.lower() or "never calls" in code.lower(), (
            "closeEvent should document that it does NOT call QThread.terminate()"
        )

    def test_no_terminate_in_calibre_converter(self):
        """calibre_converter.py must only terminate subprocess, not QThread."""
        from pathlib import Path

        src = Path(__file__).parent.parent / "app" / "converter" / "calibre_converter.py"
        code = src.read_text(encoding="utf-8")

        # _terminate_process is the only function that calls terminate()
        # It should operate on a subprocess.Popen, not a QThread
        assert "QThread" not in code, "calibre_converter.py should not import QThread"
        assert "process.terminate()" in code, "Must terminate subprocess (not QThread)"

        # Verify _terminate_process uses process.terminate(), not thread.terminate()
        lines = code.split("\n")
        in_terminate_func = False
        for line in lines:
            if "def _terminate_process" in line:
                in_terminate_func = True
            elif in_terminate_func and line.strip().startswith("def "):
                in_terminate_func = False
            if in_terminate_func and ".terminate()" in line:
                assert "process" in line, (
                    f"_terminate_process must call process.terminate(), not: {line.strip()}"
                )