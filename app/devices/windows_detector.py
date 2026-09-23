"""Windows storage device enumeration.

Uses ctypes to call Win32 API for volume information without external dependencies.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Win32 constants
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

# Kindles are always FAT32
KINDLE_FILESYSTEMS = {"FAT32", "FAT"}


@dataclass
class WindowsVolume:
    """Information about a single Windows volume."""

    drive_letter: str  # e.g. "E"
    root_path: Path  # e.g. Path("E:/")
    volume_label: str  # e.g. "Kindle"
    filesystem: str  # e.g. "FAT32"
    drive_type: int  # Win32 drive type constant
    serial_number: str  # volume serial number (hex string, stable identifier)
    total_bytes: int
    free_bytes: int

    def __repr__(self) -> str:
        return (
            f"WindowsVolume({self.drive_letter}:, "
            f"label={self.volume_label!r}, "
            f"fs={self.filesystem}, "
            f"type={self.drive_type})"
        )


def _get_drive_type(root: str) -> int:
    """Get the Win32 drive type for a root path."""
    kernel32 = ctypes.windll.kernel32
    return kernel32.GetDriveTypeW(root)


def _get_volume_info(root: str) -> tuple[str, str, str, int, int]:
    """Get volume label, filesystem, serial, and size info.

    Returns (label, filesystem, serial_hex, total_bytes, free_bytes).
    """
    kernel32 = ctypes.windll.kernel32

    label_buf = ctypes.create_unicode_buffer(256)
    fs_buf = ctypes.create_unicode_buffer(256)
    serial = wintypes.DWORD()

    kernel32.GetVolumeInformationW(
        root, label_buf, 256, ctypes.byref(serial),
        None, None, fs_buf, 256,
    )

    label = label_buf.value or ""
    fs = fs_buf.value or ""
    serial_hex = f"{serial.value:08X}"

    # Free space
    free_bytes = wintypes.ULARGE_INTEGER()
    total_bytes = wintypes.ULARGE_INTEGER()
    kernel32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total_bytes), ctypes.byref(free_bytes))

    return label, fs, serial_hex, total_bytes.value, free_bytes.value


def enumerate_volumes() -> list[WindowsVolume]:
    """Enumerate all accessible Windows volumes.

    Returns a list of WindowsVolume objects for volumes that are ready
    (have a filesystem, not empty card readers, etc.).
    """
    kernel32 = ctypes.windll.kernel32
    drives_bitmask = kernel32.GetLogicalDrives()
    if drives_bitmask == 0:
        logger.warning("GetLogicalDrives failed")
        return []

    volumes: list[WindowsVolume] = []

    for i in range(26):
        if not (drives_bitmask & (1 << i)):
            continue
        drive_letter = chr(ord("A") + i)
        root = f"{drive_letter}:\\"

        drive_type = _get_drive_type(root)

        # Skip CD-ROM and network drives
        if drive_type in (DRIVE_CDROM, DRIVE_REMOTE, DRIVE_RAMDISK):
            continue

        try:
            label, fs, serial, total, free = _get_volume_info(root)
        except OSError:
            # Volume not ready (empty card reader, etc.)
            continue

        if not fs:
            # No filesystem — skip
            continue

        volume = WindowsVolume(
            drive_letter=drive_letter,
            root_path=Path(f"{drive_letter}:/"),
            volume_label=label,
            filesystem=fs,
            drive_type=drive_type,
            serial_number=serial,
            total_bytes=total,
            free_bytes=free,
        )
        volumes.append(volume)
        logger.debug("Found volume: %s", volume)

    return volumes


def get_volume_by_drive(drive_letter: str) -> WindowsVolume | None:
    """Get volume info for a specific drive letter."""
    root = f"{drive_letter}:\\"
    drive_type = _get_drive_type(root)
    try:
        label, fs, serial, total, free = _get_volume_info(root)
    except OSError:
        return None
    if not fs:
        return None
    return WindowsVolume(
        drive_letter=drive_letter.upper(),
        root_path=Path(f"{drive_letter.upper()}:/"),
        volume_label=label,
        filesystem=fs,
        drive_type=drive_type,
        serial_number=serial,
        total_bytes=total,
        free_bytes=free,
    )