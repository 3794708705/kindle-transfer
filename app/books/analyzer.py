"""Book file analysis module.

Analyzes ebook files against a device profile to determine
whether they can be transferred directly, need conversion, or are unsupported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.devices.profiles import DeviceProfile

logger = logging.getLogger(__name__)


class BookAnalysis:
    """Structured result of analyzing a book file."""

    def __init__(
        self,
        source_path: Path,
        extension: str,
        action: str,
        target_format: str | None = None,
    ) -> None:
        self.source_path: Path = source_path
        self.extension: str = extension  # e.g. '.epub'
        self.action: str = action  # 'direct', 'convert', 'unsupported'
        self.target_format: str | None = target_format  # e.g. '.azw3'

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "extension": self.extension,
            "action": self.action,
            "target_format": self.target_format,
        }

    def __repr__(self) -> str:
        return (
            f"BookAnalysis(path={self.source_path.name!r}, "
            f"ext={self.extension}, action={self.action}, "
            f"target={self.target_format})"
        )


def analyze_book(source_path: Path, device_profile: DeviceProfile) -> BookAnalysis:
    """Analyze a single book file against a device profile.

    Args:
        source_path: Path to the book file.
        device_profile: Device profile to check against.

    Returns:
        A BookAnalysis with the determined action.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is empty or has no extension.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"File not found: {source_path}")

    if not source_path.is_file():
        raise FileNotFoundError(f"Not a regular file: {source_path}")

    if source_path.stat().st_size == 0:
        raise ValueError(f"File is empty: {source_path}")

    extension = source_path.suffix
    if not extension:
        raise ValueError(f"File has no extension: {source_path}")

    action, target_format = device_profile.get_action(extension)

    logger.info(
        "Analyzed %s: ext=%s, action=%s, target=%s",
        source_path.name,
        extension,
        action,
        target_format,
    )

    return BookAnalysis(
        source_path=source_path,
        extension=extension.lower(),
        action=action,
        target_format=target_format,
    )


def analyze_books(
    source_paths: list[Path], device_profile: DeviceProfile
) -> list[BookAnalysis]:
    """Analyze multiple book files.

    Args:
        source_paths: List of paths to book files.
        device_profile: Device profile to check against.

    Returns:
        List of BookAnalysis results.
    """
    results: list[BookAnalysis] = []
    for path in source_paths:
        try:
            result = analyze_book(path, device_profile)
            results.append(result)
        except (FileNotFoundError, ValueError) as e:
            logger.error("Failed to analyze %s: %s", path, e)
            results.append(
                BookAnalysis(
                    source_path=path,
                    extension=path.suffix.lower() if path.suffix else "",
                    action="unsupported",
                )
            )
    return results