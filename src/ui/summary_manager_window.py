from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from memory.database import GameDatabase
from ui.edit_summary_dialog import EditSummaryDialog


class SummaryManagerWindow(QWidget):
    """摘要管理面板。"""

    def __init__(self, database: GameDatabase, parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._edit_dialogs: list[EditSummaryDialog] = []

        self.setWindowTitle("摘要管理")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(1180, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self._table = QTableWidget(self)
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["ID", "游戏", "摘要内容", "覆盖范围", "创建时间"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._update_button_states)
        layout.addWidget(self._table)

        bottom_row = QHBoxLayout()

        self._count_label = QLabel("共 0 条记录", self)
        self._count_label.setStyleSheet("font-size: 11px; color: #666;")
        bottom_row.addWidget(self._count_label)

        bottom_row.addStretch(1)

        self._refresh_button = QPushButton("刷新", self)
        self._edit_button = QPushButton("修改", self)
        self._delete_button = QPushButton("删除", self)

        self._refresh_button.clicked.connect(self.refresh_summaries)
        self._edit_button.clicked.connect(self._on_edit_clicked)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        bottom_row.addWidget(self._refresh_button)
        bottom_row.addWidget(self._edit_button)
        bottom_row.addWidget(self._delete_button)
        layout.addLayout(bottom_row)

        self._update_button_states()
        self.refresh_summaries()

    def refresh_summaries(self) -> None:
        summaries = self._database.get_all_summaries_with_game_name()
        self._table.setRowCount(0)
        self._table.setRowCount(len(summaries))

        for row_index, summary in enumerate(summaries):
            start_value = summary.get("start_conversation_id")
            end_value = summary.get("end_conversation_id")
            if start_value is None and end_value is None:
                coverage_text = "早期摘要"
            else:
                start_text = "" if start_value is None else str(start_value)
                end_text = "" if end_value is None else str(end_value)
                coverage_text = f"{start_text} - {end_text}"

            values = [
                summary.get("id"),
                summary.get("game_name"),
                summary.get("content"),
                coverage_text,
                summary.get("created_at"),
            ]
            for column_index, value in enumerate(values):
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary)
                self._table.setItem(row_index, column_index, item)

        self._count_label.setText(f"共 {len(summaries)} 条记录")
        self._table.clearSelection()
        self._update_button_states()

    def _current_summary(self) -> dict[str, object] | None:
        row = self._table.currentRow()
        if row < 0:
            return None

        id_item = self._table.item(row, 0)
        if id_item is None:
            return None
        summary = id_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(summary, dict):
            return None
        return summary

    def _update_button_states(self) -> None:
        has_selection = self._table.currentRow() >= 0
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _on_edit_clicked(self) -> None:
        summary = self._current_summary()
        if summary is None:
            QMessageBox.information(self, "提示", "请先选中一条摘要记录")
            return

        dialog = EditSummaryDialog(self._database, summary, self)
        self._edit_dialogs.append(dialog)
        dialog.saved.connect(self._on_summary_saved)
        dialog.finished.connect(lambda _result, d=dialog: self._cleanup_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _cleanup_dialog(self, dialog: EditSummaryDialog) -> None:
        if dialog in self._edit_dialogs:
            self._edit_dialogs.remove(dialog)

    def _on_summary_saved(self, _summary_id: int) -> None:
        self.refresh_summaries()

    def _on_delete_clicked(self) -> None:
        summary = self._current_summary()
        if summary is None:
            QMessageBox.information(self, "提示", "请先选中一条摘要记录")
            return

        summary_id = int(summary.get("id", 0))
        preview_text = str(summary.get("content", ""))
        if len(preview_text) > 80:
            preview_text = preview_text[:80].rstrip() + "..."
        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除该条摘要吗？\n\n{preview_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._database.delete_summary(summary_id):
            self.refresh_summaries()
            return

        QMessageBox.warning(self, "提示", "删除失败，未找到对应记录")

    def closeEvent(self, event: QCloseEvent) -> None:
        for dialog in list(self._edit_dialogs):
            dialog.close()
        self._edit_dialogs.clear()
        super().closeEvent(event)
