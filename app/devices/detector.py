"""Kindle device detection utilities.

V0.1: Manual selection only. Automatic USB detection is a future feature.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_kindle_root(kindle_root: Path, target_folder: str = "documents") -> bool:
    """Check whether a directory looks like a valid Kindle root.

    A valid Kindle root must contain a 'documents' folder (or the specified
    target_folder).

    Args:
        kindle_root: Path to the suspected Kindle root directory.
        target_folder: Name of the target folder to check for.

    Returns:
        True if the target folder exists inside the root.
    """
    if not kindle_root.exists():
        logger.warning("Kindle root does not exist: %s", kindle_root)
        return False

    if not kindle_root.is_dir():
        logger.warning("Kindle root is not a directory: %s", kindle_root)
        return False

    target = kindle_root / target_folder
    if not target.exists() or not target.is_dir():
        logger.warning(
            "Target folder '%s' not found in %s", target_folder, kindle_root
        )
        return False

    logger.info("Valid Kindle root: %s (target: %s)", kindle_root, target_folder)
    return True


def get_kindle_documents_path(kindle_root: Path, target_folder: str = "documents") -> Path:
    """Get the full path to the Kindle documents folder.

    Args:
        kindle_root: Path to the Kindle root directory.
        target_folder: Name of the target folder.

    Returns:
        Path to the documents folder.
    """
    return kindle_root / target_folder