"""USB file transfer to Kindle device.

Handles copying files to the Kindle documents folder with verification.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class TransferError(Exception):
    """Raised when a file transfer fails."""


class TransferResult:
    """Result of a single file transfer."""

    def __init__(
        self,
        success: bool,
        source_path: Path,
        dest_path: Path | None = None,
        error_message: str | None = None,
        skipped: bool = False,
        skip_reason: str | None = None,
    ) -> None:
        self.success: bool = success
        self.source_path: Path = source_path
        self.dest_path: Path | None = dest_path
        self.error_message: str | None = error_message
        self.skipped: bool = skipped
        self.skip_reason: str | None = skip_reason


def check_disk_space(source_path: Path, dest_dir: Path) -> bool:
    """Check if there is enough free space on the destination drive.

    Uses a simple size comparison. For large batches, this is approximate.

    Args:
        source_path: The source file to copy.
        dest_dir: The destination directory.

    Returns:
        True if there appears to be enough space.
    """
    try:
        source_size = source_path.stat().st_size
        # Get free space on the destination drive
        usage = shutil.disk_usage(dest_dir)
        free_space = usage.free
        # Require at least 10MB extra margin
        return free_space >= (source_size + 10 * 1024 * 1024)
    except OSError:
        # If we can't check, proceed anyway
        return True


def verify_transfer(source_path: Path, dest_path: Path) -> bool:
    """Verify that a file was transferred correctly.

    Compares file sizes. In future versions, this may upgrade to SHA-256.

    Args:
        source_path: The original source file.
        dest_path: The transferred destination file.

    Returns:
        True if sizes match.
    """
    try:
        if not dest_path.exists():
            logger.error("Destination file does not exist: %s", dest_path)
            return False

        source_size = source_path.stat().st_size
        dest_size = dest_path.stat().st_size

        if source_size != dest_size:
            logger.error(
                "Size mismatch: source=%d, dest=%d (%s)",
                source_size,
                dest_size,
                source_path.name,
            )
            return False

        logger.info(
            "Transfer verified: %s (%d bytes)", source_path.name, source_size
        )
        return True
    except OSError as e:
        logger.error("Verification failed: %s", e)
        return False


def transfer_file(
    source_path: Path,
    dest_dir: Path,
    overwrite: bool = False,
) -> TransferResult:
    """Copy a file to the destination directory with verification.

    Args:
        source_path: Path to the source file.
        dest_dir: Destination directory.
        overwrite: If True, overwrite existing files. If False, skip.

    Returns:
        TransferResult indicating success, failure, or skip.
    """
    if not source_path.exists():
        return TransferResult(
            success=False,
            source_path=source_path,
            error_message=f"Source file not found: {source_path}",
        )

    if not dest_dir.exists():
        return TransferResult(
            success=False,
            source_path=source_path,
            error_message=f"Destination directory not found: {dest_dir}",
        )

    if not dest_dir.is_dir():
        return TransferResult(
            success=False,
            source_path=source_path,
            error_message=f"Destination is not a directory: {dest_dir}",
        )

    dest_path = dest_dir / source_path.name

    # Check if destination already exists
    if dest_path.exists():
        if not overwrite:
            return TransferResult(
                success=False,
                source_path=source_path,
                dest_path=dest_path,
                skipped=True,
                skip_reason=f"File already exists: {dest_path.name}",
            )
        logger.info("Overwriting existing file: %s", dest_path.name)

    # Check disk space
    if not check_disk_space(source_path, dest_dir):
        return TransferResult(
            success=False,
            source_path=source_path,
            error_message="Insufficient disk space on destination",
        )

    # Perform the copy
    try:
        shutil.copy2(source_path, dest_path)
        logger.info("Copied: %s -> %s", source_path.name, dest_path)
    except OSError as e:
        return TransferResult(
            success=False,
            source_path=source_path,
            dest_path=dest_path,
            error_message=f"Copy failed: {e}",
        )

    # Verify the transfer
    if not verify_transfer(source_path, dest_path):
        return TransferResult(
            success=False,
            source_path=source_path,
            dest_path=dest_path,
            error_message="Transfer verification failed: size mismatch",
        )

    return TransferResult(
        success=True,
        source_path=source_path,
        dest_path=dest_path,
    )