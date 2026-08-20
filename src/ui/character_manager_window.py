from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from memory.database import GameDatabase
from ui.edit_character_dialog import EditCharacterDialog


class CharacterManagerWindow(QWidget):
    """人物管理面板。"""

    def __init__(self, database: GameDatabase, parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._edit_dialogs: list[EditCharacterDialog] = []

        self.setWindowTitle("人物管理")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(860, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)

        self._table = QTableWidget(self)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["ID", "原文名", "译文名", "性别", "游戏", "补充信息"])
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

        self._refresh_button.clicked.connect(self.refresh_characters)
        self._edit_button.clicked.connect(self._on_edit_clicked)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        bottom_row.addWidget(self._refresh_button)
        bottom_row.addWidget(self._edit_button)
        bottom_row.addWidget(self._delete_button)
        layout.addLayout(bottom_row)

        self._update_button_states()
        self.refresh_characters()

    def refresh_characters(self) -> None:
        characters = self._database.get_all_characters_with_game_name()
        self._table.setRowCount(0)
        self._table.setRowCount(len(characters))

        for row_index, character in enumerate(characters):
            values = [
                character.get("id"),
                character.get("name_original"),
                character.get("name_translated"),
                character.get("gender"),
                character.get("game_name"),
                character.get("extra_info"),
            ]
            for column_index, value in enumerate(values):
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, character.get("id"))
                self._table.setItem(row_index, column_index, item)

        self._count_label.setText(f"共 {len(characters)} 条记录")
        self._table.clearSelection()
        self._update_button_states()

    def _current_character(self) -> dict[str, object] | None:
        row = self._table.currentRow()
        if row < 0:
            return None

        id_item = self._table.item(row, 0)
        name_original_item = self._table.item(row, 1)
        name_translated_item = self._table.item(row, 2)
        gender_item = self._table.item(row, 3)
        game_item = self._table.item(row, 4)
        extra_info_item = self._table.item(row, 5)

        if (
            id_item is None
            or name_original_item is None
            or name_translated_item is None
            or gender_item is None
            or game_item is None
            or extra_info_item is None
        ):
            return None

        character_id_text = id_item.text().strip()
        if not character_id_text.isdigit():
            return None

        character_id = int(character_id_text)
        return {
            "id": character_id,
            "name_original": name_original_item.text(),
            "name_translated": name_translated_item.text(),
            "game_name": game_item.text(),
            "gender": gender_item.text() or None,
            "extra_info": extra_info_item.text() or None,
        }

    def _update_button_states(self) -> None:
        has_selection = self._table.currentRow() >= 0
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _on_edit_clicked(self) -> None:
        character = self._current_character()
        if character is None:
            QMessageBox.information(self, "提示", "请先选中一条人物记录")
            return

        dialog = EditCharacterDialog(self._database, character, self)
        self._edit_dialogs.append(dialog)
        dialog.saved.connect(self._on_character_saved)
        dialog.finished.connect(lambda _result, d=dialog: self._cleanup_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _cleanup_dialog(self, dialog: EditCharacterDialog) -> None:
        if dialog in self._edit_dialogs:
            self._edit_dialogs.remove(dialog)

    def _on_character_saved(self, _character_id: int) -> None:
        self.refresh_characters()

    def _on_delete_clicked(self) -> None:
        character = self._current_character()
        if character is None:
            QMessageBox.information(self, "提示", "请先选中一条人物记录")
            return

        character_id = int(character["id"])
        character_name = str(character.get("name_original", ""))
        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除人物“{character_name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._database.delete_character(character_id):
            self.refresh_characters()
            return

        QMessageBox.warning(self, "提示", "删除失败，未找到对应记录")

    def closeEvent(self, event: QCloseEvent) -> None:
        for dialog in list(self._edit_dialogs):
            dialog.close()
        self._edit_dialogs.clear()
        super().closeEvent(event)