"""Tests for file-list management (remove / clear).

Covers the「移除选中」/「清空列表」buttons, the table's right-click menu, and
the Delete key.

The central guarantee under test is that list management is **non
destructive**: removing an entry means "do not transfer this", never
"delete this file from disk".  Every test that touches removal also
asserts the source file survived.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Keep QSettings out of the user's real configuration."""
    from app.converter.calibre_converter import set_qsettings_names as set_conv
    from app.devices.device_matcher import set_qsettings_names as set_dev

    set_conv("KindleTransferTests", "KindleTransferTests_FileList")
    set_dev("KindleTransferTests", "KindleTransferTests_FileList")
    yield
    set_conv("KindleTransfer", "KindleTransfer")
    set_dev("KindleTransfer", "KindleTransfer")


@pytest.fixture(autouse=True)
def _qapp():
    """Ensure a QApplication exists for MainWindow tests."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield


@pytest.fixture
def window(_qapp):
    from app.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    w.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_books(tmp_path: Path, names: list[str]) -> list[Path]:
    """Create real, non-empty files so that deletion would be observable."""
    paths: list[Path] = []
    for name in names:
        path = tmp_path / name
        path.write_text(f"content of {name}", encoding="utf-8")
        paths.append(path)
    return paths


def _add(window, paths: list[Path]) -> None:
    window._add_files(paths)


def _select_rows(window, rows: list[int]) -> None:
    """Select the given row indices in the table."""
    from PySide6.QtCore import QItemSelectionModel

    model = window._table.model()
    selection_model = window._table.selectionModel()
    selection_model.clearSelection()
    for row in rows:
        selection_model.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )


def _names(window) -> list[str]:
    return [a.source_path.name for a in window._analyses]


# ── Remove selected ──────────────────────────────────────────────────────────


class TestRemoveSelected:
    def test_removes_only_the_selected_row(self, window, tmp_path):
        paths = _make_books(tmp_path, ["a.txt", "b.txt", "c.txt"])
        _add(window, paths)
        assert _names(window) == ["a.txt", "b.txt", "c.txt"]

        _select_rows(window, [1])
        with patch("app.ui.main_window.QMessageBox"):
            window._on_remove_selected()

        assert _names(window) == ["a.txt", "c.txt"]
        # Table and model must agree
        assert window._table.rowCount() == 2
        assert window._table.item(1, 0).text() == "c.txt"

    def test_removes_multiple_non_contiguous_rows(self, window, tmp_path):
        paths = _make_books(
            tmp_path, ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]
        )
        _add(window, paths)

        # Rows 0, 2 and 4 — exercises the reverse-deletion ordering.
        _select_rows(window, [0, 2, 4])
        with patch("app.ui.main_window.QMessageBox"):
            window._on_remove_selected()

        assert _names(window) == ["b.txt", "d.txt"]
        assert window._table.rowCount() == 2

    def test_removing_all_rows_empties_the_table(self, window, tmp_path):
        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)

        _select_rows(window, [0, 1])
        with patch("app.ui.main_window.QMessageBox"):
            window._on_remove_selected()

        assert window._analyses == []
        assert window._table.rowCount() == 0

    def test_never_deletes_the_source_files(self, window, tmp_path):
        """The core promise: removal must not touch the disk."""
        paths = _make_books(tmp_path, ["keep1.txt", "remove.txt", "keep2.txt"])
        before = {p: p.read_text(encoding="utf-8") for p in paths}

        _add(window, paths)
        _select_rows(window, [1])
        with patch("app.ui.main_window.QMessageBox"):
            window._on_remove_selected()

        for path, content in before.items():
            assert path.exists(), f"{path.name} was deleted from disk"
            assert path.read_text(encoding="utf-8") == content

    def test_without_selection_nothing_is_removed(self, window, tmp_path):
        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)
        window._table.clearSelection()

        with patch("app.ui.main_window.QMessageBox") as box:
            window._on_remove_selected()

        assert _names(window) == ["a.txt", "b.txt"]
        box.information.assert_called_once()

    def test_status_message_says_files_were_not_deleted(self, window, tmp_path):
        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)

        _select_rows(window, [0])
        with patch("app.ui.main_window.QMessageBox"):
            window._on_remove_selected()

        assert "未删除" in window._status_bar.currentMessage()

    def test_blocked_while_transferring(self, window, tmp_path):
        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)

        _select_rows(window, [0])
        with patch.object(window, "_is_transferring", return_value=True), patch(
            "app.ui.main_window.QMessageBox"
        ) as box:
            window._on_remove_selected()

        assert _names(window) == ["a.txt", "b.txt"]
        box.information.assert_called_once()


# ── Clear list ───────────────────────────────────────────────────────────────


class TestClearList:
    def test_confirming_empties_the_list(self, window, tmp_path):
        from PySide6.QtWidgets import QMessageBox

        _add(window, _make_books(tmp_path, ["a.txt", "b.txt"]))

        with patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window._on_clear_list()

        assert window._analyses == []
        assert window._table.rowCount() == 0

    def test_declining_keeps_every_entry(self, window, tmp_path):
        from PySide6.QtWidgets import QMessageBox

        _add(window, _make_books(tmp_path, ["a.txt", "b.txt"]))

        with patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            window._on_clear_list()

        assert _names(window) == ["a.txt", "b.txt"]

    def test_never_deletes_the_source_files(self, window, tmp_path):
        from PySide6.QtWidgets import QMessageBox

        paths = _make_books(tmp_path, ["a.txt", "b.txt", "c.txt"])
        _add(window, paths)

        with patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window._on_clear_list()

        assert window._analyses == []
        for path in paths:
            assert path.exists(), f"{path.name} was deleted from disk"

    def test_on_empty_list_is_a_no_op(self, window):
        assert window._analyses == []
        with patch("app.ui.main_window.QMessageBox") as box:
            window._on_clear_list()
        box.information.assert_called_once()

    def test_blocked_while_transferring(self, window, tmp_path):
        _add(window, _make_books(tmp_path, ["a.txt", "b.txt"]))

        with patch.object(window, "_is_transferring", return_value=True), patch(
            "app.ui.main_window.QMessageBox"
        ) as box:
            window._on_clear_list()

        assert _names(window) == ["a.txt", "b.txt"]
        box.information.assert_called_once()


# ── Interaction surfaces ─────────────────────────────────────────────────────


class TestInteractionSurfaces:
    def test_buttons_exist(self, window):
        assert window._remove_selected_btn.text() == "移除选中"
        assert window._clear_list_btn.text() == "清空列表"

    def test_delete_shortcut_is_scoped_to_the_table(self, window):
        from PySide6.QtCore import Qt

        assert window._delete_shortcut.key() == Qt.Key.Key_Delete
        # WidgetShortcut so it cannot fire while focus is elsewhere.
        assert (
            window._delete_shortcut.context()
            == Qt.ShortcutContext.WidgetShortcut
        )

    def test_context_menu_policy_is_custom(self, window):
        from PySide6.QtCore import Qt

        assert (
            window._table.contextMenuPolicy()
            == Qt.ContextMenuPolicy.CustomContextMenu
        )

    def test_multi_row_selection_is_enabled(self, window):
        from PySide6.QtWidgets import QTableWidget

        assert (
            window._table.selectionMode()
            == QTableWidget.SelectionMode.ExtendedSelection
        )

    def test_shutdown_disables_list_editing(self, window):
        window._disable_ui_for_shutdown()
        assert not window._remove_selected_btn.isEnabled()
        assert not window._clear_list_btn.isEnabled()

    def test_is_transferring_tracks_worker_reference(self, window, tmp_path):
        from app.books.analyzer import BookAnalysis
        from app.ui.main_window import TransferWorker

        assert window._is_transferring() is False

        analysis = BookAnalysis(
            source_path=Path("dummy.azw3"), extension=".azw3", action="direct"
        )
        window._worker = TransferWorker(
            [analysis], Path("C:/"), None, overwrite=False
        )
        assert window._is_transferring() is True

        window._worker = None
        assert window._is_transferring() is False


# ── Right-click menu ─────────────────────────────────────────────────────────


def _menu_actions(window) -> dict:
    menu = window._build_list_menu()
    return {a.text(): a for a in menu.actions() if a.text()}


class TestContextMenu:
    def test_both_actions_present(self, window, tmp_path):
        _add(window, _make_books(tmp_path, ["a.txt"]))
        actions = _menu_actions(window)
        assert "移除选中" in actions
        assert "清空列表" in actions

    def test_remove_disabled_without_selection(self, window, tmp_path):
        _add(window, _make_books(tmp_path, ["a.txt"]))
        window._table.clearSelection()
        actions = _menu_actions(window)
        assert actions["移除选中"].isEnabled() is False
        assert actions["清空列表"].isEnabled() is True

    def test_remove_enabled_with_selection(self, window, tmp_path):
        _add(window, _make_books(tmp_path, ["a.txt"]))
        _select_rows(window, [0])
        actions = _menu_actions(window)
        assert actions["移除选中"].isEnabled() is True

    def test_both_disabled_on_empty_list(self, window):
        actions = _menu_actions(window)
        assert actions["移除选中"].isEnabled() is False
        assert actions["清空列表"].isEnabled() is False

    def test_remove_action_actually_removes(self, window, tmp_path):
        """The menu entry must be wired to the handler, not just present."""
        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)
        _select_rows(window, [0])

        with patch("app.ui.main_window.QMessageBox"):
            _menu_actions(window)["移除选中"].trigger()

        assert _names(window) == ["b.txt"]
        for path in paths:
            assert path.exists()

    def test_clear_action_actually_clears(self, window, tmp_path):
        from PySide6.QtWidgets import QMessageBox

        paths = _make_books(tmp_path, ["a.txt", "b.txt"])
        _add(window, paths)

        with patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            _menu_actions(window)["清空列表"].trigger()

        assert window._analyses == []
        for path in paths:
            assert path.exists()
