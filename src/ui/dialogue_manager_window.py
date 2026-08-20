from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from memory.database import GameDatabase
from ui.edit_dialogue_dialog import EditDialogueDialog


class DialogueManagerWindow(QWidget):
    """对话管理面板。"""

    def __init__(self, database: GameDatabase, parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._edit_dialogs: list[EditDialogueDialog] = []

        self.setWindowTitle("对话管理")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(1120, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self._table = QTableWidget(self)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["ID", "人物原文", "人物译文", "对话原文", "对话译文", "所属游戏", "创建时间"]
        )
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

        self._refresh_button.clicked.connect(self.refresh_dialogues)
        self._edit_button.clicked.connect(self._on_edit_clicked)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        bottom_row.addWidget(self._refresh_button)
        bottom_row.addWidget(self._edit_button)
        bottom_row.addWidget(self._delete_button)
        layout.addLayout(bottom_row)

        self._update_button_states()
        self.refresh_dialogues()

    def refresh_dialogues(self) -> None:
        dialogues = self._database.get_all_dialogues_with_game_name()
        self._table.setRowCount(0)
        self._table.setRowCount(len(dialogues))

        for row_index, dialogue in enumerate(dialogues):
            values = [
                dialogue.get("id"),
                dialogue.get("character_name_original"),
                dialogue.get("name_translated"),
                dialogue.get("dialog_text_original"),
                dialogue.get("dialog_text_translated"),
                dialogue.get("game_name"),
                dialogue.get("created_at"),
            ]
            for column_index, value in enumerate(values):
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, dialogue)
                self._table.setItem(row_index, column_index, item)

        self._count_label.setText(f"共 {len(dialogues)} 条记录")
        self._table.clearSelection()
        self._update_button_states()

    def _current_dialogue(self) -> dict[str, object] | None:
        row = self._table.currentRow()
        if row < 0:
            return None

        id_item = self._table.item(row, 0)
        if id_item is None:
            return None
        dialogue = id_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(dialogue, dict):
            return None
        return dialogue

    def _update_button_states(self) -> None:
        has_selection = self._table.currentRow() >= 0
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _on_edit_clicked(self) -> None:
        dialogue = self._current_dialogue()
        if dialogue is None:
            QMessageBox.information(self, "提示", "请先选中一条对话记录")
            return

        dialog = EditDialogueDialog(self._database, dialogue, self)
        self._edit_dialogs.append(dialog)
        dialog.saved.connect(self._on_dialogue_saved)
        dialog.finished.connect(lambda _result, d=dialog: self._cleanup_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _cleanup_dialog(self, dialog: EditDialogueDialog) -> None:
        if dialog in self._edit_dialogs:
            self._edit_dialogs.remove(dialog)

    def _on_dialogue_saved(self, _dialogue_id: int) -> None:
        self.refresh_dialogues()

    def _on_delete_clicked(self) -> None:
        dialogue = self._current_dialogue()
        if dialogue is None:
            QMessageBox.information(self, "提示", "请先选中一条对话记录")
            return

        dialogue_id = int(dialogue.get("id", 0))
        preview_text = str(dialogue.get("dialog_text_original", ""))
        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除该条对话吗？\n\n{preview_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._database.delete_dialogue(dialogue_id):
            self.refresh_dialogues()
            return

        QMessageBox.warning(self, "提示", "删除失败，未找到对应记录")

    def closeEvent(self, event: QCloseEvent) -> None:
        for dialog in list(self._edit_dialogs):
            dialog.close()
        self._edit_dialogs.clear()
        super().closeEvent(event)
