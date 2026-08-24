from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QHeaderView, QTableWidgetItem, QMessageBox

from beta.core_beta.window_capture import enumerate_game_windows, is_window_available, is_window_minimized
from beta.memory_beta.window_selection_state import SelectedGameWindow


class WindowSelectionDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        ui_path = Path(__file__).with_name("window_selection_dialog.ui")
        uic.loadUi(str(ui_path), self)

        self._selected_window: SelectedGameWindow | None = None
        self._configure_table()
        self.refresh_windows()

        self.refresh_button.clicked.connect(self.refresh_windows)
        self.confirm_button.clicked.connect(self._on_confirm_clicked)
        self.cancel_button.clicked.connect(self.reject)
        self.window_table.itemDoubleClicked.connect(self._on_item_double_clicked)

    def _configure_table(self) -> None:
        self.window_table.setColumnCount(2)
        self.window_table.setHorizontalHeaderLabels(["窗口标题", "类名"])
        self.window_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.window_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.window_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.window_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.window_table.verticalHeader().setVisible(False)

    def refresh_windows(self) -> None:
        current_hwnd = self._current_selected_hwnd()
        windows = enumerate_game_windows(self._excluded_hwnds())
        self.window_table.setRowCount(0)
        self.window_table.setRowCount(len(windows))

        for row_index, window_info in enumerate(windows):
            title_item = QTableWidgetItem(window_info.title)
            class_item = QTableWidgetItem(window_info.class_name)
            title_item.setData(Qt.ItemDataRole.UserRole, window_info)
            class_item.setData(Qt.ItemDataRole.UserRole, window_info)
            self.window_table.setItem(row_index, 0, title_item)
            self.window_table.setItem(row_index, 1, class_item)

            if window_info.hwnd == current_hwnd:
                self.window_table.selectRow(row_index)

        self.count_label.setText(f"共 {len(windows)} 个窗口")

    def _excluded_hwnds(self) -> set[int]:
        excluded: set[int] = set()
        own_hwnd = int(self.winId())
        if own_hwnd > 0:
            excluded.add(own_hwnd)
        parent = self.parent()
        if parent is not None:
            parent_hwnd = int(parent.winId())
            if parent_hwnd > 0:
                excluded.add(parent_hwnd)
        return excluded

    def _current_selected_hwnd(self) -> int | None:
        row = self.window_table.currentRow()
        if row < 0:
            return None
        item = self.window_table.item(row, 0)
        if item is None:
            return None
        window_info = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(window_info, SelectedGameWindow):
            return window_info.hwnd
        return None

    def _current_window_info(self) -> SelectedGameWindow | None:
        row = self.window_table.currentRow()
        if row < 0:
            return None
        item = self.window_table.item(row, 0)
        if item is None:
            return None
        window_info = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(window_info, SelectedGameWindow):
            return window_info
        return None

    def _on_confirm_clicked(self) -> None:
        window_info = self._current_window_info()
        if window_info is None:
            QMessageBox.information(self, "提示", "请先选择一个窗口")
            return
        if not is_window_available(window_info.hwnd):
            QMessageBox.warning(self, "提示", "该窗口已不存在，请重新选择")
            self.refresh_windows()
            return
        if is_window_minimized(window_info.hwnd):
            QMessageBox.warning(self, "提示", "该窗口当前处于最小化状态，请先还原后再选择")
            return

        self._selected_window = window_info
        self.accept()

    def _on_item_double_clicked(self, *_args: object) -> None:
        self._on_confirm_clicked()

    @property
    def selected_window(self) -> SelectedGameWindow | None:
        return self._selected_window
