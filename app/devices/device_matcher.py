"""Kindle device matching — evidence-based scoring.

Identifies Kindle devices from WindowsVolume information without
relying on any single heuristic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings

from app.devices.windows_detector import WindowsVolume, DRIVE_REMOVABLE, DRIVE_FIXED
from app.devices.profiles import load_device_profiles, DeviceProfile

logger = logging.getLogger(__name__)

# QSettings key for device-to-profile mapping
SETTINGS_KEY_DEVICE_PROFILES = "device_profiles"

# Confidence thresholds
HIGH_CONFIDENCE = 7   # >= this: auto-select OK (single device only)
LOW_CONFIDENCE = 4    # >= this: candidate, but needs user confirmation
MIN_CANDIDATE = 3     # >= this: possible Kindle

# Kindle root indicators
KINDLE_ROOT_FOLDERS = {
    "documents",
    ".active_content_sandbox",
}

KINDLE_OPTIONAL_FOLDERS = {
    "fonts",
    "audible",
    "voice",
    "screenshots",
    "system",
    ".hidden",
}


@dataclass
class DetectionResult:
    """Result of attempting to detect a Kindle device."""

    is_kindle: bool
    confidence: int  # 0-10 scale
    root_path: Path | None = None
    target_path: Path | None = None  # e.g. documents folder
    device_name: str | None = None
    profile_id: str | None = None
    volume: WindowsVolume | None = None
    model: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def is_connected(self) -> bool:
        return self.is_kindle and self.root_path is not None

    @property
    def free_bytes(self) -> int:
        if self.volume:
            return self.volume.free_bytes
        return 0

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE

    @property
    def serial_number(self) -> str:
        if self.volume:
            return self.volume.serial_number
        return ""


def _score_volume(volume: WindowsVolume) -> tuple[int, list[str]]:
    """Score a WindowsVolume for how likely it is a Kindle.

    Returns (score, reasons).  Score is 0-10.
    """
    score = 0
    reasons: list[str] = []

    root = volume.root_path

    # 1. Volume label check
    label_lower = volume.volume_label.lower()
    if "kindle" in label_lower:
        score += 3
        reasons.append("卷标包含 'Kindle'")
    elif "amazon" in label_lower:
        score += 2
        reasons.append("卷标包含 'Amazon'")

    # 2. Documents folder
    documents = root / "documents"
    if documents.exists() and documents.is_dir():
        score += 2
        reasons.append("存在 documents 文件夹")

    # 3. Kindle-specific folders
    kindle_folder_count = 0
    for folder in KINDLE_ROOT_FOLDERS:
        if (root / folder).exists():
            kindle_folder_count += 1
    for folder in KINDLE_OPTIONAL_FOLDERS:
        if (root / folder).exists():
            kindle_folder_count += 1

    if kindle_folder_count >= 3:
        score += 2
        reasons.append(f"存在 {kindle_folder_count} 个 Kindle 特征文件夹")
    elif kindle_folder_count >= 1:
        score += 1
        reasons.append(f"存在 {kindle_folder_count} 个 Kindle 相关文件夹")

    # 4. Removable drive — strong indicator
    if volume.drive_type == DRIVE_REMOVABLE:
        score += 1
        reasons.append("可移动存储设备")

    # 5. Filesystem — Kindles are always FAT32
    if volume.filesystem.upper() in ("FAT32", "FAT"):
        score += 1
        reasons.append(f"FAT 文件系统 ({volume.filesystem})")

    # 6. Size — typical Kindle is 4-64 GB
    size_gb = volume.total_bytes / (1024**3)
    if 2 <= size_gb <= 64:
        score += 1
        reasons.append(f"容量 {size_gb:.1f} GB（符合 Kindle 范围）")

    # 7. Require at least one strong indicator to avoid false positives
    has_strong_indicator = (
        "kindle" in label_lower
        or "amazon" in label_lower
        or volume.drive_type == DRIVE_REMOVABLE
    )
    if not has_strong_indicator:
        if score >= MIN_CANDIDATE:
            logger.debug(
                "Volume %s: score=%d but no strong indicator — suppressed",
                volume.root_path, score,
            )
        return 0, ["缺少强特征（非 Kindle 标签、非可移动设备）"]

    return score, reasons


# Cached device profiles.  The device list is polled every ~2 seconds, so
# re-reading config/devices.json on every poll is needless disk I/O.
_PROFILES_CACHE: dict[str, DeviceProfile] | None = None


def get_profiles() -> dict[str, DeviceProfile]:
    """Return device profiles, loading them from disk only once."""
    global _PROFILES_CACHE
    if _PROFILES_CACHE is None:
        try:
            _PROFILES_CACHE = load_device_profiles()
        except Exception:
            logger.exception("Failed to load device profiles")
            _PROFILES_CACHE = {}
    return _PROFILES_CACHE


def invalidate_profile_cache() -> None:
    """Drop the cached profiles.

    Call after editing config/devices.json, or from tests that need a
    fresh read.
    """
    global _PROFILES_CACHE
    _PROFILES_CACHE = None


def _build_result(volume: WindowsVolume, score: int, reasons: list[str]) -> DetectionResult:
    """Build a DetectionResult from a scored volume."""
    profile_id = _get_saved_profile(volume.serial_number)
    model = None
    if profile_id:
        profile = get_profiles().get(profile_id)
        if profile is not None:
            model = profile.name

    if not profile_id:
        profile_id = "generic_kindle"

    documents = volume.root_path / "documents"
    target_path = documents if documents.exists() and documents.is_dir() else None

    return DetectionResult(
        is_kindle=True,
        confidence=min(score, 10),
        root_path=volume.root_path,
        target_path=target_path,
        device_name=volume.volume_label or "Kindle",
        profile_id=profile_id,
        volume=volume,
        model=model,
        reasons=reasons,
    )


def detect_all_kindle_devices(volumes: list[WindowsVolume]) -> list[DetectionResult]:
    """Return all detected Kindle-like devices, sorted by confidence descending.

    Never auto-selects — caller must decide which device to use.
    """
    results: list[DetectionResult] = []
    for volume in volumes:
        score, reasons = _score_volume(volume)
        if score >= MIN_CANDIDATE:
            results.append(_build_result(volume, score, reasons))
    results.sort(key=lambda r: r.confidence, reverse=True)
    return results


def detect_single_high_confidence(volumes: list[WindowsVolume]) -> DetectionResult | None:
    """Auto-select a Kindle ONLY when there is exactly one high-confidence
    candidate.  Returns None in all other cases:

    - Zero candidates
    - One low-confidence candidate
    - Multiple candidates (regardless of scores)
    """
    candidates = detect_all_kindle_devices(volumes)

    if len(candidates) == 0:
        return None
    if len(candidates) >= 2:
        logger.info(
            "Multiple Kindle candidates (%d) — refusing to auto-select",
            len(candidates),
        )
        return None
    if not candidates[0].is_high_confidence:
        logger.info(
            "Single Kindle candidate but low confidence (%d) — needs user confirmation",
            candidates[0].confidence,
        )
        return None

    return candidates[0]


# ── Device-to-profile mapping persistence ────────────────────────────────────

# Default QSettings organization/app names. Overridable so tests can isolate
# device-profile mappings from the user's real settings.
_QSETTINGS_ORG = "KindleTransfer"
_QSETTINGS_APP = "KindleTransfer"


def set_qsettings_names(org: str, app: str) -> None:
    """Override QSettings organization and application names.

    Mirrors app.converter.calibre_converter.set_qsettings_names so tests can
    isolate device-profile mappings from production settings.
    """
    global _QSETTINGS_ORG, _QSETTINGS_APP
    _QSETTINGS_ORG = org
    _QSETTINGS_APP = app


def _get_settings() -> QSettings:
    return QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)


def _get_saved_profile(serial_number: str) -> str | None:
    """Get the saved profile_id for a device serial number."""
    settings = _get_settings()
    value = settings.value(f"{SETTINGS_KEY_DEVICE_PROFILES}/{serial_number}", None)
    if value and isinstance(value, str):
        return value
    return None


def save_device_profile(serial_number: str, profile_id: str) -> None:
    """Save the profile_id mapping for a device serial number."""
    settings = _get_settings()
    settings.setValue(f"{SETTINGS_KEY_DEVICE_PROFILES}/{serial_number}", profile_id)
    settings.sync()
    logger.info("Saved device profile: %s -> %s", serial_number, profile_id)


def get_saved_device_serial_for_profile(profile_id: str) -> str | None:
    """Find the serial number mapped to a given profile_id."""
    settings = _get_settings()
    settings.beginGroup(SETTINGS_KEY_DEVICE_PROFILES)
    keys = settings.childKeys()
    settings.endGroup()
    for key in keys:
        settings.beginGroup(SETTINGS_KEY_DEVICE_PROFILES)
        value = settings.value(key)
        settings.endGroup()
        if value == profile_id:
            return key
    return None