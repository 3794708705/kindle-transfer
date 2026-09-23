"""Tests for the book analyzer module."""

from pathlib import Path

import pytest

from app.books.analyzer import analyze_book, analyze_books, BookAnalysis
from app.devices.profiles import get_default_profile


class TestAnalyzeBook:
    """Tests for analyze_book function."""

    @pytest.fixture
    def profile(self):
        return get_default_profile()

    def test_epub_converts_to_azw3(self, profile, tmp_path):
        """EPUB file should be marked for conversion to AZW3."""
        book = tmp_path / "test.epub"
        book.write_text("mock epub content")

        result = analyze_book(book, profile)
        assert result.action == "convert"
        assert result.target_format == ".azw3"
        assert result.extension == ".epub"

    def test_azw3_is_direct(self, profile, tmp_path):
        """AZW3 file should be marked for direct transfer."""
        book = tmp_path / "test.azw3"
        book.write_text("mock azw3 content")

        result = analyze_book(book, profile)
        assert result.action == "direct"
        assert result.target_format == ".azw3"

    def test_pdf_is_direct(self, profile, tmp_path):
        """PDF file should be marked for direct transfer."""
        book = tmp_path / "test.pdf"
        book.write_text("mock pdf content")

        result = analyze_book(book, profile)
        assert result.action == "direct"
        assert result.target_format == ".pdf"

    def test_unknown_extension_unsupported(self, profile, tmp_path):
        """Unknown extension should be unsupported."""
        book = tmp_path / "test.xyz"
        book.write_text("mock content")

        result = analyze_book(book, profile)
        assert result.action == "unsupported"
        assert result.target_format is None

    def test_case_insensitive_extension(self, profile, tmp_path):
        """Extension matching should be case-insensitive."""
        book = tmp_path / "test.EPUB"
        book.write_text("mock epub content")

        result = analyze_book(book, profile)
        assert result.action == "convert"
        assert result.target_format == ".azw3"

    def test_uppercase_extension_direct(self, profile, tmp_path):
        """Uppercase extension for direct format should work."""
        book = tmp_path / "test.PDF"
        book.write_text("mock pdf content")

        result = analyze_book(book, profile)
        assert result.action == "direct"

    def test_file_not_found(self, profile, tmp_path):
        """Nonexistent file should raise FileNotFoundError."""
        book = tmp_path / "nonexistent.epub"
        with pytest.raises(FileNotFoundError):
            analyze_book(book, profile)

    def test_empty_file(self, profile, tmp_path):
        """Empty file should raise ValueError."""
        book = tmp_path / "empty.epub"
        book.write_text("")

        with pytest.raises(ValueError, match="empty"):
            analyze_book(book, profile)

    def test_no_extension(self, profile, tmp_path):
        """File with no extension should raise ValueError."""
        book = tmp_path / "noextension"
        book.write_text("content")

        with pytest.raises(ValueError, match="extension"):
            analyze_book(book, profile)

    def test_mobi_is_direct(self, profile, tmp_path):
        """MOBI should be direct."""
        book = tmp_path / "test.mobi"
        book.write_text("mock mobi")

        result = analyze_book(book, profile)
        assert result.action == "direct"

    def test_docx_converts(self, profile, tmp_path):
        """DOCX should convert to AZW3."""
        book = tmp_path / "test.docx"
        book.write_text("mock docx")

        result = analyze_book(book, profile)
        assert result.action == "convert"
        assert result.target_format == ".azw3"

    def test_result_to_dict(self, profile, tmp_path):
        """BookAnalysis.to_dict should return a proper dict."""
        book = tmp_path / "test.epub"
        book.write_text("content")

        result = analyze_book(book, profile)
        d = result.to_dict()
        assert d["action"] == "convert"
        assert d["target_format"] == ".azw3"
        assert "source_path" in d


class TestAnalyzeBooks:
    """Tests for analyze_books (batch analysis)."""

    def test_batch_analysis(self, tmp_path):
        profile = get_default_profile()

        books = []
        for name in ["book1.epub", "book2.azw3", "book3.pdf", "book4.xyz"]:
            p = tmp_path / name
            p.write_text("mock content")
            books.append(p)

        results = analyze_books(books, profile)
        assert len(results) == 4

        # EPUB → convert
        assert results[0].action == "convert"
        # AZW3 → direct
        assert results[1].action == "direct"
        # PDF → direct
        assert results[2].action == "direct"
        # XYZ → unsupported
        assert results[3].action == "unsupported"

    def test_batch_with_missing_file(self, tmp_path):
        """Batch analysis should handle missing files gracefully."""
        profile = get_default_profile()

        existing = tmp_path / "exists.epub"
        existing.write_text("content")
        missing = tmp_path / "missing.epub"

        results = analyze_books([existing, missing], profile)
        assert len(results) == 2
        assert results[0].action == "convert"
        assert results[1].action == "unsupported"