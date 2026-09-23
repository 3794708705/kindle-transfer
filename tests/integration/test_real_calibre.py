"""Real calibre integration tests.

These tests require a real calibre/ebook-convert installation.
They are skipped automatically if ebook-convert is not found.

To run:
    pytest tests/integration/ -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.converter.calibre_converter import (
    find_ebook_convert,
    verify_ebook_convert,
    convert_ebook,
    ConversionStatus,
)
from tests.integration.epub_fixture import create_minimal_epub


# ── Fixture: find ebook-convert or skip ──────────────────────────────────────

@pytest.fixture(scope="module")
def ebook_convert_path():
    """Find ebook-convert or skip all integration tests."""
    path = find_ebook_convert()
    if path is None:
        pytest.skip("ebook-convert not found on this system — skipping integration tests")
    if not verify_ebook_convert(path):
        pytest.skip(f"ebook-convert found at {path} but failed --version check")
    return path


# ── Fixture: create a minimal EPUB ───────────────────────────────────────────

@pytest.fixture(scope="module")
def minimal_epub(tmp_path_factory):
    """Create a minimal valid EPUB file for testing."""
    epub_path = tmp_path_factory.mktemp("epub") / "minimal_test.epub"
    create_minimal_epub(epub_path)
    assert epub_path.exists()
    assert epub_path.stat().st_size > 0
    return epub_path


# ── Tests ────────────────────────────────────────────────────────────────────

class TestRealCalibreIntegration:
    """Tests that require a real calibre installation."""

    def test_ebook_convert_version(self, ebook_convert_path):
        """Verify ebook-convert --version returns success."""
        assert verify_ebook_convert(ebook_convert_path) is True

    def test_epub_to_azw3_conversion(self, ebook_convert_path, minimal_epub, tmp_path):
        """Convert a minimal EPUB to AZW3 and verify the result."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            input_path=minimal_epub,
            source_format=".epub",
            target_format=".azw3",
            ebook_convert_path=ebook_convert_path,
            output_dir=output_dir,
        )

        # Check status
        assert result.status == ConversionStatus.SUCCESS, (
            f"Conversion failed: {result.error_message}"
        )
        assert result.success is True

        # Check output file
        assert result.output_path is not None
        assert result.output_path.exists(), (
            f"Output file not found: {result.output_path}"
        )
        assert result.output_path.stat().st_size > 0, (
            "Output file is empty"
        )
        assert result.output_path.suffix == ".azw3"

        # Check timing was recorded
        assert result.elapsed_seconds > 0

    def test_conversion_with_chinese_content(self, ebook_convert_path, tmp_path):
        """Convert an EPUB with Chinese content."""
        # Create a custom EPUB with Chinese title
        epub_path = tmp_path / "chinese_test.epub"
        create_minimal_epub(
            epub_path,
            title="KindleTransfer 集成测试",
            author="转换测试",
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            input_path=epub_path,
            source_format=".epub",
            target_format=".azw3",
            ebook_convert_path=ebook_convert_path,
            output_dir=output_dir,
        )

        assert result.status == ConversionStatus.SUCCESS
        assert result.output_path is not None
        assert result.output_path.exists()
        assert result.output_path.stat().st_size > 0

    def test_conversion_with_spaces_in_path(self, ebook_convert_path, tmp_path):
        """Convert when paths contain spaces."""
        # Create EPUB in a path with spaces
        src_dir = tmp_path / "my test books"
        src_dir.mkdir()
        epub_path = src_dir / "test book.epub"
        create_minimal_epub(epub_path)

        output_dir = tmp_path / "output with spaces"
        output_dir.mkdir()

        result = convert_ebook(
            input_path=epub_path,
            source_format=".epub",
            target_format=".azw3",
            ebook_convert_path=ebook_convert_path,
            output_dir=output_dir,
        )

        assert result.status == ConversionStatus.SUCCESS
        assert result.output_path is not None
        assert result.output_path.exists()

    def test_bad_input_fails_gracefully(self, ebook_convert_path, tmp_path):
        """A file that is not a valid EPUB should fail gracefully."""
        # Create a file that is not a valid EPUB
        bad_file = tmp_path / "not_an_epub.epub"
        bad_file.write_text("This is not a valid EPUB file")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = convert_ebook(
            input_path=bad_file,
            source_format=".epub",
            target_format=".azw3",
            ebook_convert_path=ebook_convert_path,
            output_dir=output_dir,
        )

        # Should fail, but not crash
        assert result.status != ConversionStatus.SUCCESS
        assert result.error_message is not None