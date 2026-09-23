"""Tests for device profile loading and validation."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from app.devices.profiles import DeviceProfile, load_device_profiles, get_default_profile
from app.devices.detector import validate_kindle_root, get_kindle_documents_path


class TestDeviceProfile:
    """Tests for the DeviceProfile class."""

    def test_load_oasis3_profile(self):
        """Test that Kindle Oasis 3 profile loads correctly."""
        profile = get_default_profile()
        assert profile.name == "Kindle Oasis 3"
        assert profile.target_folder == "documents"
        assert profile.preferred_format == ".azw3"

    def test_direct_formats(self):
        """Test that direct formats are correctly identified."""
        profile = get_default_profile()
        # EPUB should NOT be direct
        assert ".epub" not in profile.direct_formats
        # AZW3 should be direct
        assert ".azw3" in profile.direct_formats
        # PDF should be direct
        assert ".pdf" in profile.direct_formats

    def test_convert_formats(self):
        """Test that conversion formats are correctly mapped."""
        profile = get_default_profile()
        assert ".epub" in profile.convert_formats
        assert ".docx" in profile.convert_formats
        assert profile.convert_formats[".epub"] == ".azw3"

    def test_epub_converts_to_azw3(self):
        """EPUB in Oasis 3 should be 'convert' → '.azw3'."""
        profile = get_default_profile()
        action, target = profile.get_action(".epub")
        assert action == "convert"
        assert target == ".azw3"

    def test_azw3_is_direct(self):
        """AZW3 should be direct transfer."""
        profile = get_default_profile()
        action, target = profile.get_action(".azw3")
        assert action == "direct"
        assert target == ".azw3"

    def test_pdf_is_direct(self):
        """PDF should be direct transfer."""
        profile = get_default_profile()
        action, target = profile.get_action(".pdf")
        assert action == "direct"
        assert target == ".pdf"

    def test_unknown_extension_unsupported(self):
        """Unknown extensions should be unsupported."""
        profile = get_default_profile()
        action, target = profile.get_action(".xyz")
        assert action == "unsupported"
        assert target is None

    def test_extension_case_insensitive(self):
        """Extension matching should be case-insensitive."""
        profile = get_default_profile()
        # Uppercase
        action, target = profile.get_action(".EPUB")
        assert action == "convert"
        assert target == ".azw3"
        # Mixed case
        action, target = profile.get_action(".AzW3")
        assert action == "direct"
        assert target == ".azw3"

    def test_mobi_is_direct(self):
        """MOBI should be direct transfer."""
        profile = get_default_profile()
        action, target = profile.get_action(".mobi")
        assert action == "direct"
        assert target == ".mobi"

    def test_txt_is_direct(self):
        """TXT should be direct transfer."""
        profile = get_default_profile()
        action, target = profile.get_action(".txt")
        assert action == "direct"
        assert target == ".txt"

    def test_docx_converts_to_azw3(self):
        """DOCX should convert to AZW3."""
        profile = get_default_profile()
        action, target = profile.get_action(".docx")
        assert action == "convert"
        assert target == ".azw3"


class TestLoadDeviceProfiles:
    """Tests for loading profiles from JSON."""

    def test_load_from_valid_json(self):
        """Test loading profiles from a valid JSON file."""
        data = {
            "test_device": {
                "name": "Test Device",
                "target_folder": "books",
                "preferred_format": ".mobi",
                "direct_formats": [".mobi", ".txt"],
                "convert_formats": {".epub": ".mobi"},
            }
        }
        with NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            temp_path = Path(f.name)

        try:
            profiles = load_device_profiles(temp_path)
            assert "test_device" in profiles
            profile = profiles["test_device"]
            assert profile.name == "Test Device"
            action, target = profile.get_action(".epub")
            assert action == "convert"
            assert target == ".mobi"
        finally:
            temp_path.unlink(missing_ok=True)

    def test_load_missing_file_raises(self):
        """Loading a nonexistent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_device_profiles(Path("/nonexistent/devices.json"))


class TestValidateKindleRoot:
    """Tests for Kindle root directory validation."""

    def test_valid_kindle_root(self, tmp_path):
        """A directory with a 'documents' subfolder should be valid."""
        kindle_root = tmp_path / "Kindle"
        kindle_root.mkdir()
        documents = kindle_root / "documents"
        documents.mkdir()

        assert validate_kindle_root(kindle_root) is True

    def test_missing_documents_folder(self, tmp_path):
        """A directory without a 'documents' subfolder should be invalid."""
        kindle_root = tmp_path / "NotKindle"
        kindle_root.mkdir()

        assert validate_kindle_root(kindle_root) is False

    def test_nonexistent_root(self, tmp_path):
        """A nonexistent path should be invalid."""
        fake_root = tmp_path / "does_not_exist"
        assert validate_kindle_root(fake_root) is False

    def test_custom_target_folder(self, tmp_path):
        """Should work with a custom target folder."""
        root = tmp_path / "CustomDevice"
        root.mkdir()
        custom_folder = root / "my_books"
        custom_folder.mkdir()

        assert validate_kindle_root(root, target_folder="my_books") is True
        assert validate_kindle_root(root, target_folder="documents") is False

    def test_get_kindle_documents_path(self, tmp_path):
        """Should return the correct documents path."""
        root = tmp_path / "Kindle"
        root.mkdir()
        root_documents = root / "documents"
        root_documents.mkdir()

        docs_path = get_kindle_documents_path(root)
        assert docs_path == root_documents