from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from memory.database import GameDatabase


class EditDialogueDialog(QDialog):
    """非模态对话编辑对话框。"""

    saved = pyqtSignal(int)

    def __init__(self, database: GameDatabase, dialogue: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._dialogue_id = int(dialogue.get("id", 0))
        self._game_id = int(dialogue.get("game_id", 0))

        self.setWindowTitle("修改对话")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self._character_name_original_input = QLineEdit(self)
        character_name_original = dialogue.get("character_name_original")
        self._character_name_original_input.setText(
            "" if character_name_original is None else str(character_name_original)
        )
        self._character_name_original_input.setReadOnly(True)

        self._dialog_text_original_input = QLineEdit(self)
        self._dialog_text_original_input.setText(str(dialogue.get("dialog_text_original", "")))

        self._dialog_text_translated_input = QLineEdit(self)
        dialog_text_translated = dialogue.get("dialog_text_translated")
        self._dialog_text_translated_input.setText(
            "" if dialog_text_translated is None else str(dialog_text_translated)
        )

        self._game_input = QLineEdit(self)
        self._game_input.setText(str(dialogue.get("game_name", "")))
        self._game_input.setReadOnly(True)

        form_layout.addRow("人物原文", self._character_name_original_input)
        form_layout.addRow("对话原文", self._dialog_text_original_input)
        form_layout.addRow("对话译文", self._dialog_text_translated_input)
        form_layout.addRow("所属游戏", self._game_input)
        layout.addLayout(form_layout)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self._button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _on_accept(self) -> None:
        if self._dialogue_id <= 0:
            QMessageBox.warning(self, "提示", "无效的对话记录")
            return
        if self._game_id <= 0:
            QMessageBox.warning(self, "提示", "所属游戏无效")
            return

        character_name_original = self._character_name_original_input.text().strip()
        dialog_text_original = self._dialog_text_original_input.text().strip()
        dialog_text_translated = self._dialog_text_translated_input.text().strip()
        if character_name_original and not self._database.character_exists(
            character_name_original,
            self._game_id,
        ):
            QMessageBox.warning(self, "提示", "人物原文不存在于当前游戏的人物列表中")
            return

        try:
            updated = self._database.update_dialogue(
                dialogue_id=self._dialogue_id,
                character_name_original=character_name_original or None,
                dialog_text_original=dialog_text_original,
                dialog_text_translated=dialog_text_translated,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return

        if not updated:
            QMessageBox.warning(self, "提示", "未找到要更新的对话记录")
            return

        self.saved.emit(self._dialogue_id)
        self.accept()
