"""KindleTransfer - Kindle Smart Book Transfer Assistant."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow

APP_VERSION = "0.2.2"


def setup_logging() -> None:
    """Configure logging to file and console."""
    # Ensure logs directory exists
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "kindle_transfer.log"

    # Root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # File handler - detailed logs
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler - only warnings and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_formatter = logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logger.info("=" * 50)
    logger.info("KindleTransfer V%s starting", APP_VERSION)
    logger.info("=" * 50)


def main() -> None:
    """Application entry point."""
    setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("KindleTransfer")
    app.setOrganizationName("KindleTransfer")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()