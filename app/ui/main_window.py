"""Main window GUI for KindleTransfer.

Uses PySide6 for the desktop interface.
"""

from __future__ import annotations

import logging
import sys
import threading
import tempfile
import shutil as shutil_mod
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot, QSettings
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStatusBar,
    QTextEdit,
)

from app.books.analyzer import analyze_books, BookAnalysis
from app.converter.calibre_converter import (
    find_ebook_convert,
    verify_ebook_convert,
    save_ebook_convert_path,
    convert_ebook,
    ConversionResult,
    ConversionStatus,
)
from app.devices.detector import validate_kindle_root, get_kindle_documents_path
from app.devices.profiles import get_default_profile, load_device_profiles
from app.devices.device_manager import DeviceManager
from app.devices.device_matcher import DetectionResult, save_device_profile, get_profiles
from app.transfer.usb_transfer import transfer_file, TransferResult

logger = logging.getLogger(__name__)

# Column indices for the file table
COL_FILENAME = 0
COL_FORMAT = 1
COL_ACTION = 2
COL_TARGET = 3
COL_STATUS = 4

# Book status constants
STATUS_PENDING = "待处理"
STATUS_CONVERTING = "正在转换"
STATUS_TRANSFERRING = "正在传输"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"
STATUS_UNSUPPORTED = "不支持"
STATUS_SKIPPED = "跳过"
STATUS_CANCELLED = "已取消"


class TransferWorker(QThread):
    """Background worker for processing books.

    Handles conversion and transfer in a separate thread to keep the UI responsive.
    Single-file failures do not stop the entire batch.
    Supports cancellation via cancel_event.
    """

    progress_signal = Signal(int, str)  # row, status
    finished_signal = Signal()
    error_signal = Signal(int, str)  # row, error message

    def __init__(
        self,
        analyses: list[BookAnalysis],
        kindle_root: Path,
        ebook_convert_path: str | None,
        overwrite: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.analyses = analyses
        self.kindle_root = kindle_root
        self.ebook_convert_path = ebook_convert_path
        self.overwrite = overwrite
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation of the current operation."""
        self._cancel_event.set()

    def run(self) -> None:
        import traceback

        try:
            self._run_impl()
        except Exception:
            logger.error(
                "Unhandled exception in TransferWorker:\n%s",
                traceback.format_exc(),
            )
        finally:
            self.finished_signal.emit()

    def _run_impl(self) -> None:
        dest_dir = get_kindle_documents_path(self.kindle_root)

        for i, analysis in enumerate(self.analyses):
            if self._cancel_event.is_set():
                self.progress_signal.emit(i, STATUS_CANCELLED)
                break

            if analysis.action == "unsupported":
                self.progress_signal.emit(i, STATUS_UNSUPPORTED)
                continue

            source_path = analysis.source_path

            if analysis.action == "direct":
                self._process_direct(i, source_path, dest_dir)

            elif analysis.action == "convert":
                self._process_convert(i, analysis, source_path, dest_dir)

    def _process_direct(self, i: int, source_path: Path, dest_dir: Path) -> None:
        """Process a file that needs direct transfer."""
        if self._cancel_event.is_set():
            self.progress_signal.emit(i, STATUS_CANCELLED)
            return

        self.progress_signal.emit(i, STATUS_TRANSFERRING)
        try:
            result = transfer_file(source_path, dest_dir, overwrite=self.overwrite)
            if result.success:
                self.progress_signal.emit(i, STATUS_SUCCESS)
            elif result.skipped:
                self.progress_signal.emit(i, STATUS_SKIPPED)
            else:
                self.progress_signal.emit(i, STATUS_FAILED)
                self.error_signal.emit(i, result.error_message or "传输失败")
        except Exception:
            logger.exception("Direct transfer failed for %s", source_path.name)
            self.progress_signal.emit(i, STATUS_FAILED)
            self.error_signal.emit(i, "传输过程中发生异常")

    def _process_convert(
        self,
        i: int,
        analysis: BookAnalysis,
        source_path: Path,
        dest_dir: Path,
    ) -> None:
        """Process a file that needs conversion before transfer."""
        if self._cancel_event.is_set():
            self.progress_signal.emit(i, STATUS_CANCELLED)
            return

        if not self.ebook_convert_path:
            self.progress_signal.emit(i, STATUS_FAILED)
            self.error_signal.emit(i, "缺少转换引擎 (ebook-convert 未找到)")
            return

        self.progress_signal.emit(i, STATUS_CONVERTING)

        # Use TemporaryDirectory for automatic cleanup
        try:
            with tempfile.TemporaryDirectory(prefix="kindle_transfer_") as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                conv_result = convert_ebook(
                    input_path=source_path,
                    source_format=analysis.extension,
                    target_format=analysis.target_format or ".azw3",
                    ebook_convert_path=self.ebook_convert_path,
                    output_dir=temp_dir,
                    cancel_event=self._cancel_event,
                )

                if conv_result.cancelled:
                    self.progress_signal.emit(i, STATUS_CANCELLED)
                    return

                if not conv_result.success:
                    self.progress_signal.emit(i, STATUS_FAILED)
                    self.error_signal.emit(i, conv_result.error_message or "转换失败")
                    return

                # Transfer the converted file
                if self._cancel_event.is_set():
                    self.progress_signal.emit(i, STATUS_CANCELLED)
                    return

                self.progress_signal.emit(i, STATUS_TRANSFERRING)
                converted_path = conv_result.output_path
                if converted_path is None or not converted_path.exists():
                    self.progress_signal.emit(i, STATUS_FAILED)
                    self.error_signal.emit(i, "转换输出文件未生成")
                    return

                transfer_result = transfer_file(
                    converted_path, dest_dir, overwrite=self.overwrite
                )

                if transfer_result.success:
                    self.progress_signal.emit(i, STATUS_SUCCESS)
                elif transfer_result.skipped:
                    self.progress_signal.emit(i, STATUS_SKIPPED)
                else:
                    self.progress_signal.emit(i, STATUS_FAILED)
                    self.error_signal.emit(
                        i, transfer_result.error_message or "传输失败"
                    )
        except Exception:
            logger.exception("Conversion/transfer failed for %s", source_path.name)
            self.progress_signal.emit(i, STATUS_FAILED)
            self.error_signal.emit(i, "转换或传输过程中发生异常")


class SystemCheckDialog(QDialog):
    """Dialog showing system environment check results."""

    def __init__(self, parent: QMainWindow, device_manager, ebook_convert_path: str | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("系统检查")
        self.setMinimumSize(500, 400)
        self._device_manager = device_manager
        self._kindle_root = device_manager.current_device.root_path if device_manager.current_device else None
        self._ebook_convert_path = ebook_convert_path

        layout = QVBoxLayout(self)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        layout.addWidget(self._text)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._select_btn = QPushButton("选择 ebook-convert.exe")
        self._select_btn.clicked.connect(self._on_select_ebook_convert)
        btn_layout.addWidget(self._select_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self._run_check()

    def _run_check(self) -> None:
        import platform
        import shutil

        lines: list[str] = []
        lines.append("=== KindleTransfer 系统检查 ===\n")

        # Python version
        lines.append(f"✓ Python 版本: {sys.version.split()[0]}")
        lines.append(f"  操作系统: {platform.system()} {platform.release()}")
        lines.append("")

        # Kindle directory
        device = self._device_manager.current_device
        if device and device.is_connected:
            lines.append(f"● Kindle 已检测")
            lines.append(f"  设备: {device.model or device.device_name or 'Kindle'}")
            lines.append(f"  根目录: {device.root_path}")
            lines.append(f"  documents: {'✓ 存在' if device.target_path else '✗ 不存在'}")
            if device.target_path:
                try:
                    test_file = device.target_path / ".kindle_transfer_test"
                    test_file.write_text("test")
                    test_file.unlink()
                    lines.append(f"  可写: ✓")
                except OSError:
                    lines.append(f"  可写: ✗")
            if device.free_bytes:
                free_gb = device.free_bytes / (1024**3)
                lines.append(f"  可用空间: {free_gb:.1f} GB")
            if device.reasons:
                lines.append(f"  识别依据: {', '.join(device.reasons)}")
        elif self._kindle_root:
            lines.append(f"● 手动选择 Kindle")
            lines.append(f"  根目录: {self._kindle_root}")
            docs = self._kindle_root / "documents"
            if docs.exists() and docs.is_dir():
                lines.append(f"✓ documents 文件夹存在")
                try:
                    test_file = docs / ".kindle_transfer_test"
                    test_file.write_text("test")
                    test_file.unlink()
                    lines.append(f"✓ documents 可写")
                except OSError:
                    lines.append(f"✗ documents 不可写")
                try:
                    import shutil
                    usage = shutil.disk_usage(docs)
                    free_gb = usage.free / (1024**3)
                    lines.append(f"✓ 可用空间: {free_gb:.1f} GB")
                except OSError:
                    lines.append(f"? 无法检查可用空间")
            else:
                lines.append(f"✗ documents 文件夹不存在")
        else:
            lines.append(f"○ 未检测到 Kindle")
            lines.append(f"  请通过 USB 连接 Kindle，或点击「手动选择 Kindle」")
        lines.append("")

        # ebook-convert
        if self._ebook_convert_path:
            path = Path(self._ebook_convert_path)
            lines.append(f"✓ ebook-convert 路径: {self._ebook_convert_path}")
            if path.exists():
                lines.append(f"✓ 文件存在")
                try:
                    import subprocess
                    result = subprocess.run(
                        [str(path), "--version"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if result.returncode == 0:
                        version = (result.stdout or "").strip().split("\n")[0]
                        lines.append(f"✓ 可运行: {version}")
                    else:
                        lines.append(f"✗ 运行失败 (退出码: {result.returncode})")
                except Exception as e:
                    lines.append(f"✗ 运行异常: {e}")
            else:
                lines.append(f"✗ 文件不存在")
        else:
            lines.append(f"✗ ebook-convert 未找到")
            lines.append(f"  请点击下方按钮选择 ebook-convert.exe")
            self._select_btn.setVisible(True)
            return

        lines.append("")
        lines.append("=== 检查完成 ===")

        self._text.setPlainText("\n".join(lines))
        self._select_btn.setVisible(not self._ebook_convert_path)

    def _on_select_ebook_convert(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ebook-convert.exe",
            "",
            "ebook-convert.exe (ebook-convert.exe);;所有文件 (*.*)",
        )
        if not file_path:
            return

        if verify_ebook_convert(file_path):
            save_ebook_convert_path(file_path)
            self._ebook_convert_path = file_path
            parent = self.parent()
            if isinstance(parent, MainWindow):
                parent._ebook_convert_path = file_path
                parent._status_bar.showMessage(f"ebook-convert 已配置: {file_path}")
            QMessageBox.information(self, "成功", f"ebook-convert 已配置并验证成功。")
            self._run_check()
        else:
            QMessageBox.warning(
                self,
                "验证失败",
                "所选文件无法作为 ebook-convert 运行。\n"
                "请确认选择的是 calibre 安装目录中的 ebook-convert.exe。",
            )


class MainWindow(QMainWindow):
    """Main window for KindleTransfer."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Kindle Transfer - 智能传书助手")
        self.setMinimumSize(800, 500)

        self._device_profile = get_default_profile()
        self._kindle_root: Path | None = None
        self._ebook_convert_path: str | None = None
        self._analyses: list[BookAnalysis] = []
        self._worker: TransferWorker | None = None

        # Shutdown state machine
        self._closing: bool = False
        self._allow_close: bool = False

        # Device manager for auto-detection
        self._device_manager = DeviceManager(self)
        self._device_manager.device_changed.connect(self._on_device_changed)
        self._device_manager.device_connected.connect(self._on_device_connected)
        self._device_manager.device_disconnected.connect(self._on_device_disconnected)
        self._device_manager.selection_required.connect(self._on_selection_required)
        self._device_manager.low_confidence_device.connect(self._on_low_confidence_device)
        self._device_manager.model_selection_needed.connect(self._on_model_selection_needed)

        # Detect ebook-convert
        self._ebook_convert_path = find_ebook_convert()
        if self._ebook_convert_path:
            logger.info("ebook-convert detected: %s", self._ebook_convert_path)
        else:
            logger.warning("ebook-convert not found")

        self._setup_ui()
        self._setup_menu()
        self._update_device_status()

        # Start device polling
        self._device_manager.start()

    def _setup_ui(self) -> None:
        """Build the UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)

        # ── Device Selection Area ──
        device_layout = QHBoxLayout()
        self._device_status_icon = QLabel("○")
        self._device_status_icon.setStyleSheet("font-size: 18px; font-weight: bold;")
        device_layout.addWidget(self._device_status_icon)

        self._device_label = QLabel("未检测到 Kindle\n请通过 USB 连接 Kindle")
        self._device_label.setStyleSheet("font-weight: bold;")
        device_layout.addWidget(self._device_label)
        device_layout.addStretch()

        self._select_target_btn = QPushButton("选择目标设备")
        self._select_target_btn.clicked.connect(self._on_select_target_device)
        device_layout.addWidget(self._select_target_btn)

        self._select_kindle_btn = QPushButton("手动选择 Kindle")
        self._select_kindle_btn.clicked.connect(self._on_select_kindle)
        device_layout.addWidget(self._select_kindle_btn)

        main_layout.addLayout(device_layout)

        # ── File Addition Area ──
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("电子书文件:"))

        self._select_books_btn = QPushButton("选择电子书")
        self._select_books_btn.clicked.connect(self._on_select_books)
        file_layout.addWidget(self._select_books_btn)

        file_layout.addStretch()

        # List management. These edit the pending list only — the source
        # files on disk are never touched.
        self._remove_selected_btn = QPushButton("移除选中")
        self._remove_selected_btn.setToolTip(
            "从列表中移除选中的文件（不会删除磁盘上的原文件）"
        )
        self._remove_selected_btn.clicked.connect(self._on_remove_selected)
        file_layout.addWidget(self._remove_selected_btn)

        self._clear_list_btn = QPushButton("清空列表")
        self._clear_list_btn.setToolTip(
            "清空整个待传列表（不会删除磁盘上的原文件）"
        )
        self._clear_list_btn.clicked.connect(self._on_clear_list)
        file_layout.addWidget(self._clear_list_btn)

        main_layout.addLayout(file_layout)

        # ── File List Table ──
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["文件名", "原格式", "计划动作", "目标格式", "状态"]
        )
        self._table.setAcceptDrops(True)
        self._table.setDragEnabled(False)
        self._table.setDropIndicatorShown(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Install drag & drop event on the table
        self._table.dragEnterEvent = self._on_drag_enter
        self._table.dropEvent = self._on_drop

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_FILENAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_FORMAT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        # Multi-row selection (Ctrl/Shift click) so several entries can be
        # removed at once.
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # Right-click menu for list management.
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        # Delete key removes the selected rows while the table has focus.
        # WidgetShortcut keeps it scoped to the table, so it cannot fire
        # while the user is typing elsewhere in the window.
        self._delete_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Delete), self._table
        )
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._delete_shortcut.activated.connect(self._on_remove_selected)

        main_layout.addWidget(self._table)

        # ── Action Button ──
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._transfer_btn = QPushButton("智能传送")
        self._transfer_btn.setMinimumWidth(150)
        self._transfer_btn.setMinimumHeight(40)
        self._transfer_btn.setStyleSheet(
            "QPushButton { font-size: 14px; font-weight: bold; }"
        )
        self._transfer_btn.clicked.connect(self._on_transfer)
        button_layout.addWidget(self._transfer_btn)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # ── Status Bar ──
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("就绪")

    def _setup_menu(self) -> None:
        """Set up the menu bar."""
        menu_bar = self.menuBar()

        # Tools menu
        tools_menu = menu_bar.addMenu("工具")

        system_check_action = QAction("系统检查", self)
        system_check_action.triggered.connect(self._on_system_check)
        tools_menu.addAction(system_check_action)

        select_ebook_convert_action = QAction("选择 ebook-convert...", self)
        select_ebook_convert_action.triggered.connect(self._on_browse_ebook_convert)
        tools_menu.addAction(select_ebook_convert_action)

        tools_menu.addSeparator()

        change_model_action = QAction("更改当前 Kindle 型号", self)
        change_model_action.triggered.connect(self._on_change_kindle_model)
        tools_menu.addAction(change_model_action)

    # ── Window close handling ──

    def closeEvent(self, event) -> None:
        """Safe shutdown: cancel worker, wait for natural finish, then close.

        Never calls QThread.terminate().  Never blocks the GUI thread with
        a long wait.  The worker's cancel_event triggers subprocess cleanup;
        the worker finishes naturally and signals the window to close.
        """
        # Already cleared for close
        if self._allow_close:
            self._device_manager.stop()
            event.accept()
            return

        # No worker, or worker already finished — close immediately
        if self._worker is None or not self._worker.isRunning():
            self._device_manager.stop()
            event.accept()
            return

        # Worker is running — enter shutdown, do NOT close yet
        if not self._closing:
            self._closing = True
            logger.info("Window closing — entering shutdown, cancelling worker")
            self._disable_ui_for_shutdown()
            self._status_bar.showMessage("正在结束当前任务并安全退出……")
            self._worker.cancel()

        # Ignore this and any subsequent close attempts until worker finishes
        event.ignore()

    def _disable_ui_for_shutdown(self) -> None:
        """Disable all buttons that could start new tasks during shutdown."""
        self._transfer_btn.setEnabled(False)
        self._select_kindle_btn.setEnabled(False)
        self._select_books_btn.setEnabled(False)
        self._remove_selected_btn.setEnabled(False)
        self._clear_list_btn.setEnabled(False)

    # ── Drag & Drop ──

    def _on_drag_enter(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _on_drop(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        paths = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
        if paths:
            self._add_files(paths)

    # ── Slots ──

    def _on_select_kindle(self) -> None:
        """Handle manual Kindle root directory selection (fallback)."""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择 Kindle 根目录", ""
        )
        if not dir_path:
            return

        root = Path(dir_path)
        if not validate_kindle_root(root):
            QMessageBox.warning(
                self,
                "无效的 Kindle 目录",
                "所选目录看起来不像 Kindle 存储设备，因为未找到 documents 文件夹。\n\n"
                "请选择 Kindle 的根目录（例如 E:\\）。",
            )
            return

        self._kindle_root = root
        self._device_label.setText(f"手动选择: {root}")
        self._device_status_icon.setText("●")
        self._device_status_icon.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: green;"
        )
        logger.info("Manual Kindle root selected: %s", root)

    def _on_select_books(self) -> None:
        """Handle book file selection via file dialog."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择电子书文件",
            "",
            "电子书文件 (*.epub *.azw3 *.azw *.mobi *.prc *.txt *.pdf *.docx *.html *.htm *.rtf);;所有文件 (*.*)",
        )
        if files:
            paths = [Path(f) for f in files]
            self._add_files(paths)

    def _add_files(self, paths: list[Path]) -> None:
        """Add files to the analysis list and update the table."""
        new_analyses = analyze_books(paths, self._device_profile)
        self._analyses.extend(new_analyses)
        self._refresh_table()
        self._status_bar.showMessage(f"已添加 {len(new_analyses)} 个文件")

    def _is_transferring(self) -> bool:
        """True while a transfer worker is running.

        The worker is handed the analysis list and echoes row indices back
        through progress_signal, so the list must not change mid-transfer.
        """
        return self._worker is not None

    def _selected_rows(self) -> list[int]:
        """Return the selected row indices, ascending and de-duplicated."""
        return sorted({index.row() for index in self._table.selectedIndexes()})

    def _on_remove_selected(self) -> None:
        """Remove the selected entries from the pending list.

        Only the in-memory list is edited. The source files on disk are
        deliberately left untouched — removing an entry here means
        "do not transfer this", not "delete this file".
        """
        if self._is_transferring():
            QMessageBox.information(
                self, "提示", "正在传送中，请等待完成后再修改列表。"
            )
            return

        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先在列表中选择要移除的文件。")
            return

        # Remove from the end so the earlier indices stay valid.
        for row in reversed(rows):
            if 0 <= row < len(self._analyses):
                del self._analyses[row]

        self._refresh_table()
        self._status_bar.showMessage(
            f"已从列表移除 {len(rows)} 个文件（磁盘上的原文件未删除）"
        )

    def _on_clear_list(self) -> None:
        """Clear the whole pending list.

        As with removal, the source files on disk are never touched.
        """
        if self._is_transferring():
            QMessageBox.information(
                self, "提示", "正在传送中，请等待完成后再修改列表。"
            )
            return

        if not self._analyses:
            QMessageBox.information(self, "提示", "列表已经是空的。")
            return

        count = len(self._analyses)
        reply = QMessageBox.question(
            self,
            "清空列表",
            f"确定要清空列表中的 {count} 个文件吗？\n\n"
            "注意：只会清空这个待传列表，磁盘上的原文件不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._analyses.clear()
        self._refresh_table()
        self._status_bar.showMessage(
            f"已清空列表（{count} 个文件，磁盘上的原文件未删除）"
        )

    def _build_list_menu(self) -> QMenu:
        """Build the file-list right-click menu.

        Kept separate from _on_table_context_menu so the menu can be
        inspected without entering a modal event loop.
        """
        menu = QMenu(self._table)

        remove_action = menu.addAction("移除选中")
        remove_action.setEnabled(bool(self._table.selectedIndexes()))
        remove_action.triggered.connect(self._on_remove_selected)

        clear_action = menu.addAction("清空列表")
        clear_action.setEnabled(self._table.rowCount() > 0)
        clear_action.triggered.connect(self._on_clear_list)

        menu.addSeparator()
        hint = menu.addAction("仅从列表移除，不删除磁盘上的原文件")
        hint.setEnabled(False)

        return menu

    def _on_table_context_menu(self, pos) -> None:
        """Show the right-click menu for the file list."""
        if self._is_transferring():
            return

        index = self._table.indexAt(pos)
        if index.isValid():
            # Right-clicking a row outside the current selection should act
            # on that row instead of the previous selection.
            if not self._table.selectionModel().isSelected(index):
                self._table.selectRow(index.row())

        self._build_list_menu().exec(self._table.viewport().mapToGlobal(pos))

    def _refresh_table(self) -> None:
        """Rebuild the table from self._analyses."""
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._analyses))

        action_labels = {
            "direct": "直接传输",
            "convert": "转换",
            "unsupported": "不支持",
        }

        for i, analysis in enumerate(self._analyses):
            self._table.setItem(
                i, COL_FILENAME, QTableWidgetItem(analysis.source_path.name)
            )
            ext = analysis.extension.lstrip(".").upper()
            self._table.setItem(i, COL_FORMAT, QTableWidgetItem(ext))
            action_label = action_labels.get(analysis.action, analysis.action)
            self._table.setItem(i, COL_ACTION, QTableWidgetItem(action_label))
            if analysis.target_format:
                target_label = analysis.target_format.lstrip(".").upper()
            else:
                target_label = "-"
            self._table.setItem(i, COL_TARGET, QTableWidgetItem(target_label))
            if analysis.action == "unsupported":
                status = STATUS_UNSUPPORTED
            else:
                status = STATUS_PENDING
            self._table.setItem(i, COL_STATUS, QTableWidgetItem(status))

    def _update_device_status(self) -> None:
        """Update the device status label from current state."""
        device = self._device_manager.current_device
        if device and device.is_connected:
            self._device_status_icon.setText("●")
            self._device_status_icon.setStyleSheet(
                "font-size: 18px; font-weight: bold; color: green;"
            )
            model = device.model or device.device_name or "Kindle"
            root = device.root_path
            target = device.target_path
            free_gb = device.free_bytes / (1024**3) if device.free_bytes else 0
            lines = [
                f"● {model} 已连接",
                f"根目录: {root}",
                f"电子书目录: {target}",
                f"可用空间: {free_gb:.1f} GB",
                "✓ 已准备好",
            ]
            self._device_label.setText("\n".join(lines))
            self._kindle_root = device.root_path
            if device.profile_id:
                profile = get_profiles().get(device.profile_id)
                if profile is not None:
                    self._device_profile = profile
            return

        # No current selection, but Kindles may still be present
        candidates = self._device_manager.all_devices
        if len(candidates) >= 2:
            self._device_status_icon.setText("●")
            self._device_status_icon.setStyleSheet(
                "font-size: 18px; font-weight: bold; color: orange;"
            )
            lines = [f"● 检测到 {len(candidates)} 台 Kindle", "请选择目标设备："]
            for d in candidates:
                free = (
                    f"{d.free_bytes / (1024**3):.1f} GB 可用"
                    if d.free_bytes else "容量未知"
                )
                lines.append(f"  • {d.device_name or 'Kindle'} — {d.root_path} — {free}")
            lines.append("点击「选择目标设备」或使用工具菜单指定。")
            self._device_label.setText("\n".join(lines))
            self._kindle_root = None
            return

        if len(candidates) == 1:
            # Single low-confidence candidate — shown, but not transferable yet
            d = candidates[0]
            self._device_status_icon.setText("◐")
            self._device_status_icon.setStyleSheet(
                "font-size: 18px; font-weight: bold; color: orange;"
            )
            self._device_label.setText(
                f"◐ 检测到可能的 Kindle: {d.root_path}\n"
                f"可信度较低，请确认后选择该设备。"
            )
            self._kindle_root = None
            return

        self._device_status_icon.setText("○")
        self._device_status_icon.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )
        self._device_label.setText("未检测到 Kindle\n请通过 USB 连接 Kindle")
        self._kindle_root = None

    # ── Device manager signal handlers ──

    def _on_device_changed(self) -> None:
        self._update_device_status()

    def _on_device_connected(self, device: DetectionResult) -> None:
        logger.info("Device connected: %s (%s)", device.root_path, device.device_name)
        self._update_device_status()
        self._status_bar.showMessage(
            f"检测到 {device.device_name or 'Kindle'}: {device.root_path}", 5000
        )

    def _on_device_disconnected(self) -> None:
        logger.info("Device disconnected")
        self._update_device_status()
        self._status_bar.showMessage("Kindle 已断开", 3000)

    def _on_selection_required(self, devices: list) -> None:
        """The manager needs the user to pick a target device explicitly."""
        self._prompt_device_selection()

    def _on_select_target_device(self) -> None:
        """Button handler: let the user pick the target device explicitly."""
        if not self._device_manager.all_devices:
            QMessageBox.information(
                self, "提示", "未检测到 Kindle 设备。\n请通过 USB 连接 Kindle。"
            )
            return
        self._prompt_device_selection()

    def _prompt_device_selection(self) -> None:
        """Present the detected devices and let the user choose one.

        Never auto-picks: the first item is merely the highest-scoring
        candidate, and the user must confirm it.
        """
        devices = self._device_manager.all_devices
        if not devices:
            return

        items: list[str] = []
        for d in devices:
            label = d.device_name or "Kindle"
            root = str(d.root_path) if d.root_path else "?"
            free = f"{d.free_bytes / (1024**3):.1f} GB" if d.free_bytes else "?"
            items.append(f"{label} — {root} — {free} 可用")

        title = (
            "检测到多个 Kindle" if len(items) >= 2 else "选择目标设备"
        )
        prompt = (
            f"检测到 {len(items)} 台 Kindle，请选择目标设备："
            if len(items) >= 2
            else "请选择目标设备："
        )
        item, ok = QInputDialog.getItem(
            self, title, prompt, items, 0, False,
        )
        if not ok or not item:
            return

        idx = items.index(item)
        if self._device_manager.select_device(idx):
            device = self._device_manager.current_device
            if device and device.root_path:
                self._status_bar.showMessage(
                    f"当前设备: {device.root_path}", 5000
                )
            self._update_device_status()

    def _on_low_confidence_device(self, device: DetectionResult) -> None:
        """Notify user about a low-confidence Kindle candidate."""
        self._status_bar.showMessage(
            f"检测到可能的 Kindle: {device.root_path}（可信度较低，请确认）", 8000,
        )

    def _on_model_selection_needed(self, device: DetectionResult) -> None:
        """First connection with unknown model — ask user to pick a profile."""
        self._show_model_selection_dialog(device)

    def _show_model_selection_dialog(self, device: DetectionResult) -> None:
        """Show a dialog for selecting the Kindle model."""
        items = ["Kindle Oasis 3", "Generic Kindle / 暂不确定"]
        item, ok = QInputDialog.getItem(
            self,
            "选择 Kindle 型号",
            "检测到 Kindle，但无法确定具体型号。\n\n请选择您的设备型号：",
            items,
            editable=False,
        )
        if ok and item:
            if "Oasis" in item:
                self._device_manager.assign_profile("kindle_oasis_3")
            else:
                self._device_manager.assign_profile("generic_kindle")
            self._update_device_status()
            self._status_bar.showMessage(f"已设置设备型号: {item}", 5000)

    def _on_change_kindle_model(self) -> None:
        """Menu action: change the current Kindle model."""
        if not self._device_manager.has_device:
            QMessageBox.information(self, "提示", "请先连接 Kindle。")
            return
        device = self._device_manager.current_device
        if device:
            self._show_model_selection_dialog(device)

    def _on_system_check(self) -> None:
        """Open the system check dialog."""
        dialog = SystemCheckDialog(self, self._device_manager, self._ebook_convert_path)
        dialog.exec()

    def _on_browse_ebook_convert(self) -> None:
        """Browse for ebook-convert.exe."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ebook-convert.exe",
            "",
            "ebook-convert.exe (ebook-convert.exe);;所有文件 (*.*)",
        )
        if not file_path:
            return

        if verify_ebook_convert(file_path):
            save_ebook_convert_path(file_path)
            self._ebook_convert_path = file_path
            self._status_bar.showMessage(f"ebook-convert 已配置: {file_path}")
            QMessageBox.information(self, "成功", "ebook-convert 已配置并验证成功。")
        else:
            QMessageBox.warning(
                self,
                "验证失败",
                "所选文件无法作为 ebook-convert 运行。\n"
                "请确认选择的是 calibre 安装目录中的 ebook-convert.exe。",
            )

    @Slot()
    def _on_transfer(self) -> None:
        """Start the transfer process."""
        if self._closing:
            return

        if not self._analyses:
            QMessageBox.information(self, "提示", "请先添加电子书文件。")
            return

        # Validate device is still connected and writable
        if not self._kindle_root:
            QMessageBox.warning(self, "提示", "请先连接 Kindle 或手动选择根目录。")
            return

        # Re-validate device identity before transfer
        if self._device_manager.has_device:
            if not self._device_manager.validate_current_device():
                QMessageBox.warning(
                    self,
                    "设备异常",
                    "目标 Kindle 已断开或设备身份发生变化，请重新选择设备。",
                )
                return
        elif not self._kindle_root.exists():
            QMessageBox.warning(
                self,
                "设备异常",
                "Kindle 设备可能已断开。\n请检查 USB 连接后重试。",
            )
            return

        # Reset all statuses to pending
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_STATUS)
            if item and item.text() not in (STATUS_UNSUPPORTED,):
                item.setText(STATUS_PENDING)

        pending = [
            a for a in self._analyses if a.action in ("direct", "convert")
        ]
        if not pending:
            QMessageBox.information(self, "提示", "没有可处理的文件。")
            return

        needs_convert = any(a.action == "convert" for a in self._analyses)
        if needs_convert and not self._ebook_convert_path:
            reply = QMessageBox.warning(
                self,
                "缺少转换引擎",
                "检测到需要转换的文件（如 EPUB），但未找到 ebook-convert。\n\n"
                "直接传输格式（AZW3、PDF、MOBI 等）仍可正常传输。\n\n"
                "要继续吗？\n\n"
                "提示：可通过「工具 → 选择 ebook-convert」手动指定路径。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        overwrite = self._ask_overwrite_policy()

        self._transfer_btn.setEnabled(False)
        self._select_kindle_btn.setEnabled(False)
        self._select_books_btn.setEnabled(False)
        # Row indices are in flight during a transfer, so the list is frozen.
        self._remove_selected_btn.setEnabled(False)
        self._clear_list_btn.setEnabled(False)

        self._worker = TransferWorker(
            self._analyses,
            self._kindle_root,
            self._ebook_convert_path,
            overwrite=overwrite,
        )
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.error_signal.connect(self._on_error)
        # Business result signal (emitted in run()'s finally, before run() returns)
        self._worker.finished_signal.connect(self._on_worker_done)
        # Lifecycle signal (Qt native: emitted AFTER run() returns)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _ask_overwrite_policy(self) -> bool:
        reply = QMessageBox.question(
            self,
            "文件覆盖策略",
            "如果目标文件已存在：\n\n"
            "• 是 — 覆盖已有文件\n"
            "• 否 — 跳过已有文件（推荐）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    @Slot(int, str)
    def _on_progress(self, row: int, status: str) -> None:
        if 0 <= row < self._table.rowCount():
            item = self._table.item(row, COL_STATUS)
            if item:
                item.setText(status)

    @Slot(int, str)
    def _on_error(self, row: int, message: str) -> None:
        logger.error("Error on row %d: %s", row, message)
        self._status_bar.showMessage(f"错误: {message}", 8000)

    @Slot()
    def _on_worker_done(self) -> None:
        """Handle business results: re-enable UI, count results, show summary.

        Connected to TransferWorker.finished_signal (custom signal emitted in
        run()'s finally block, just before run() returns).  Does NOT clear
        _worker — that belongs to _on_worker_finished (QThread.finished).
        """
        self._transfer_btn.setEnabled(True)
        self._select_kindle_btn.setEnabled(True)
        self._select_books_btn.setEnabled(True)
        # Transfer is over, so the list can be edited again.
        self._remove_selected_btn.setEnabled(True)
        self._clear_list_btn.setEnabled(True)

        success_count = 0
        fail_count = 0
        skip_count = 0
        cancelled_count = 0
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_STATUS)
            if item:
                text = item.text()
                if text == STATUS_SUCCESS:
                    success_count += 1
                elif text == STATUS_FAILED:
                    fail_count += 1
                elif text == STATUS_SKIPPED:
                    skip_count += 1
                elif text == STATUS_CANCELLED:
                    cancelled_count += 1

        parts = [f"成功: {success_count}"]
        if fail_count > 0:
            parts.append(f"失败: {fail_count}")
        if skip_count > 0:
            parts.append(f"跳过: {skip_count}")
        if cancelled_count > 0:
            parts.append(f"取消: {cancelled_count}")

        self._status_bar.showMessage(f"传送完成 — {', '.join(parts)}")

        if fail_count > 0 or skip_count > 0:
            QMessageBox.information(
                self,
                "传送结果",
                f"传送完成！\n\n"
                f"成功: {success_count}\n"
                f"失败: {fail_count}\n"
                f"跳过: {skip_count}\n\n"
                "请检查文件列表查看详细信息。\n"
                "如有失败，请查看 logs/kindle_transfer.log 获取详细日志。",
            )

    @Slot()
    def _on_worker_finished(self) -> None:
        """Handle thread lifecycle: clean up worker reference, trigger deferred
        close if we are in shutdown.

        Connected to QThread.finished (Qt native signal emitted AFTER run()
        returns).  Only here is it safe to clear _worker and allow close.
        """
        logger.info("QThread.finished — worker thread has exited")
        self._worker = None

        if self._closing:
            self._allow_close = True
            logger.info("Worker finished after cancel — closing window")
            QTimer.singleShot(0, self.close)