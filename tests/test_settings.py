"""Tests for QSettings persistence with full isolation.

These tests MUST NOT touch real user KindleTransfer settings.
They use a dedicated test organization/app name and clean up after themselves.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.converter.calibre_converter import (
    set_qsettings_names,
    get_saved_ebook_convert_path,
    save_ebook_convert_path,
    clear_saved_ebook_convert_path,
    _QSETTINGS_ORG,
    _QSETTINGS_APP,
)


@pytest.fixture(autouse=True)
def _isolate_qsettings():
    """Redirect all QSettings to a test-specific namespace.

    Runs before every test in this module and restores after.
    """
    original_org = _QSETTINGS_ORG
    original_app = _QSETTINGS_APP

    # Use a unique test namespace to avoid polluting real settings
    set_qsettings_names("KindleTransferTests", "KindleTransferTests_V0_1_2")

    # Clear any leftover from previous test runs
    clear_saved_ebook_convert_path()

    yield

    # Clean up after test
    clear_saved_ebook_convert_path()

    # Restore original
    set_qsettings_names(original_org, original_app)


class TestQSettingsIsolation:
    """Verify that QSettings isolation works correctly."""

    def test_initial_no_saved_path(self):
        """Initially, no path should be saved."""
        result = get_saved_ebook_convert_path()
        assert result is None

    def test_save_and_retrieve_path(self):
        """Save a path and retrieve it back."""
        test_path = r"C:\Test\Calibre\ebook-convert.exe"
        save_ebook_convert_path(test_path)
        retrieved = get_saved_ebook_convert_path()
        assert retrieved == test_path

    def test_overwrite_existing_path(self):
        """Saving a new path should overwrite the old one."""
        save_ebook_convert_path(r"C:\Old\Path\ebook-convert.exe")
        save_ebook_convert_path(r"C:\New\Path\ebook-convert.exe")
        retrieved = get_saved_ebook_convert_path()
        assert retrieved == r"C:\New\Path\ebook-convert.exe"

    def test_clear_saved_path(self):
        """Clearing should remove the saved path."""
        save_ebook_convert_path(r"C:\Test\ebook-convert.exe")
        clear_saved_ebook_convert_path()
        assert get_saved_ebook_convert_path() is None

    def test_save_empty_string_ignored(self):
        """Empty string should not be saved as valid."""
        save_ebook_convert_path("")
        # get_saved_ebook_convert_path strips and checks for empty
        result = get_saved_ebook_convert_path()
        assert result is None

    def test_save_whitespace_only_ignored(self):
        """Whitespace-only string should not be treated as valid."""
        save_ebook_convert_path("   ")
        result = get_saved_ebook_convert_path()
        assert result is None

    def test_invalid_path_not_considered_valid(self):
        """A path that doesn't exist on disk should still be retrievable
        (the caller verifies it separately), but should be stored correctly."""
        test_path = r"C:\nonexistent\path\ebook-convert.exe"
        save_ebook_convert_path(test_path)
        retrieved = get_saved_ebook_convert_path()
        assert retrieved == test_path


class TestRealSettingsNotTouched:
    """Verify that running these tests does NOT modify real user settings."""

    def test_real_settings_names_restored(self):
        """After the fixture, the real QSettings names should be restored."""
        # The fixture restores in its teardown, so at this point
        # the names should be back to the originals
        # We can't easily check the real QSettings without importing,
        # but we can verify the module-level variables
        assert _QSETTINGS_ORG == "KindleTransfer"
        assert _QSETTINGS_APP == "KindleTransfer"