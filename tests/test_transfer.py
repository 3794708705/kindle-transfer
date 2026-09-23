"""Tests for the USB transfer module."""

from pathlib import Path

import pytest

from app.transfer.usb_transfer import (
    transfer_file,
    verify_transfer,
    TransferResult,
    TransferError,
)


class TestVerifyTransfer:
    """Tests for transfer verification."""

    def test_identical_files(self, tmp_path):
        """Two identical files should pass verification."""
        src = tmp_path / "source.txt"
        dest = tmp_path / "dest.txt"
        content = "Hello, Kindle!"
        src.write_text(content)
        dest.write_text(content)

        assert verify_transfer(src, dest) is True

    def test_size_mismatch(self, tmp_path):
        """Files with different sizes should fail verification."""
        src = tmp_path / "source.txt"
        dest = tmp_path / "dest.txt"
        src.write_text("Hello, Kindle!")
        dest.write_text("Shorter")

        assert verify_transfer(src, dest) is False

    def test_dest_missing(self, tmp_path):
        """Missing destination should fail verification."""
        src = tmp_path / "source.txt"
        dest = tmp_path / "dest.txt"
        src.write_text("Hello")

        assert verify_transfer(src, dest) is False

    def test_empty_files(self, tmp_path):
        """Two empty files should match."""
        src = tmp_path / "source.txt"
        dest = tmp_path / "dest.txt"
        src.write_text("")
        dest.write_text("")

        assert verify_transfer(src, dest) is True


class TestTransferFile:
    """Tests for the transfer_file function."""

    def test_successful_transfer(self, tmp_path):
        """A file should be successfully copied to the destination."""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        dest_dir = tmp_path / "Kindle" / "documents"
        dest_dir.mkdir(parents=True)

        src = src_dir / "book.azw3"
        src.write_text("AZW3 content here")

        result = transfer_file(src, dest_dir)

        assert result.success is True
        assert result.skipped is False
        assert result.dest_path is not None
        assert result.dest_path.exists()
        assert result.dest_path.read_text() == "AZW3 content here"

    def test_source_not_found(self, tmp_path):
        """Transfer should fail if source doesn't exist."""
        dest_dir = tmp_path / "Kindle" / "documents"
        dest_dir.mkdir(parents=True)

        src = tmp_path / "nonexistent.epub"
        result = transfer_file(src, dest_dir)

        assert result.success is False
        assert "not found" in result.error_message.lower()

    def test_dest_dir_not_found(self, tmp_path):
        """Transfer should fail if destination directory doesn't exist."""
        src = tmp_path / "book.azw3"
        src.write_text("content")

        dest_dir = tmp_path / "nonexistent" / "documents"

        result = transfer_file(src, dest_dir)

        assert result.success is False

    def test_skip_existing_file(self, tmp_path):
        """Should skip if destination file already exists and overwrite=False."""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        dest_dir = tmp_path / "Kindle" / "documents"
        dest_dir.mkdir(parents=True)

        src = src_dir / "book.azw3"
        src.write_text("new content")

        # Pre-create destination file
        existing = dest_dir / "book.azw3"
        existing.write_text("old content")

        result = transfer_file(src, dest_dir, overwrite=False)

        assert result.success is False
        assert result.skipped is True
        # Original content should be preserved
        assert existing.read_text() == "old content"

    def test_overwrite_existing_file(self, tmp_path):
        """Should overwrite if destination file exists and overwrite=True."""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        dest_dir = tmp_path / "Kindle" / "documents"
        dest_dir.mkdir(parents=True)

        src = src_dir / "book.azw3"
        src.write_text("new content")

        existing = dest_dir / "book.azw3"
        existing.write_text("old content")

        result = transfer_file(src, dest_dir, overwrite=True)

        assert result.success is True
        assert existing.read_text() == "new content"

    def test_transfer_with_chinese_filename(self, tmp_path):
        """Transfer should work with Chinese characters in filename."""
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        dest_dir = tmp_path / "Kindle" / "documents"
        dest_dir.mkdir(parents=True)

        src = src_dir / "三体.azw3"
        src.write_text("三体 content")

        result = transfer_file(src, dest_dir)

        assert result.success is True
        assert result.dest_path is not None
        assert result.dest_path.exists()

    def test_transfer_with_spaces_in_path(self, tmp_path):
        """Transfer should work with spaces in path."""
        src_dir = tmp_path / "my source files"
        src_dir.mkdir()
        dest_dir = tmp_path / "Kindle Device" / "documents"
        dest_dir.mkdir(parents=True)

        src = src_dir / "my book.azw3"
        src.write_text("content with spaces")

        result = transfer_file(src, dest_dir)

        assert result.success is True
        assert result.dest_path is not None
        assert result.dest_path.exists()