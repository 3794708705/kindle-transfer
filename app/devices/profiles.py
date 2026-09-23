"""Device profile loading and management.

Device capabilities are data-driven from config/devices.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeviceProfile:
    """Represents a single e-reader device profile."""

    def __init__(self, profile_id: str, data: dict[str, Any]) -> None:
        self.profile_id: str = profile_id
        self._data = data

    @property
    def name(self) -> str:
        """Human-readable device name."""
        return self._data["name"]

    @property
    def target_folder(self) -> str:
        """Target folder on the device (e.g. 'documents')."""
        return self._data["target_folder"]

    @property
    def preferred_format(self) -> str:
        """Preferred ebook format for this device."""
        return self._data["preferred_format"]

    @property
    def direct_formats(self) -> list[str]:
        """Formats that can be transferred directly without conversion."""
        return [f.lower() for f in self._data["direct_formats"]]

    @property
    def convert_formats(self) -> dict[str, str]:
        """Mapping of source format -> target format for conversion."""
        return {k.lower(): v.lower() for k, v in self._data["convert_formats"].items()}

    def get_action(self, extension: str) -> tuple[str, str | None]:
        """Determine the action for a given file extension.

        Args:
            extension: File extension including the dot (e.g. '.epub').

        Returns:
            A tuple of (action, target_format) where action is one of
            'direct', 'convert', or 'unsupported'.
        """
        ext = extension.lower()

        if ext in self.direct_formats:
            return ("direct", ext)

        if ext in self.convert_formats:
            target = self.convert_formats[ext]
            return ("convert", target)

        return ("unsupported", None)


def load_device_profiles(config_path: Path | None = None) -> dict[str, DeviceProfile]:
    """Load all device profiles from the config file.

    Args:
        config_path: Path to devices.json. If None, uses the default
            config/devices.json relative to the project root.

    Returns:
        A dict mapping profile_id to DeviceProfile objects.
    """
    if config_path is None:
        # Default: config/devices.json relative to this file's grandparent
        this_dir = Path(__file__).resolve().parent
        config_path = this_dir.parent.parent / "config" / "devices.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Device config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    profiles: dict[str, DeviceProfile] = {}
    for profile_id, data in raw.items():
        profiles[profile_id] = DeviceProfile(profile_id, data)

    logger.info("Loaded %d device profile(s) from %s", len(profiles), config_path)
    return profiles


def get_default_profile() -> DeviceProfile:
    """Get the default device profile (Kindle Oasis 3)."""
    profiles = load_device_profiles()
    if "kindle_oasis_3" not in profiles:
        raise ValueError("Default profile 'kindle_oasis_3' not found in config")
    return profiles["kindle_oasis_3"]