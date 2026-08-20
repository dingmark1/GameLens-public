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


class EditCharacterDialog(QDialog):
    """非模态人物编辑对话框。"""

    saved = pyqtSignal(int)

    def __init__(self, database: GameDatabase, character: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._character_id = int(character.get("id", 0))

        self.setWindowTitle("修改人物")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self._name_original_input = QLineEdit(self)
        self._name_original_input.setText(str(character.get("name_original", "")))
        self._name_original_input.setReadOnly(True)

        self._name_translated_input = QLineEdit(self)
        self._name_translated_input.setText(str(character.get("name_translated", "")))

        self._gender_input = QLineEdit(self)
        gender_value = character.get("gender")
        self._gender_input.setText("" if gender_value is None else str(gender_value))

        self._extra_info_input = QLineEdit(self)
        extra_info_value = character.get("extra_info")
        self._extra_info_input.setText("" if extra_info_value is None else str(extra_info_value))

        self._game_input = QLineEdit(self)
        self._game_input.setText(str(character.get("game_name", "")))
        self._game_input.setReadOnly(True)

        form_layout.addRow("原文名", self._name_original_input)
        form_layout.addRow("译文名", self._name_translated_input)
        form_layout.addRow("性别", self._gender_input)
        form_layout.addRow("补充信息", self._extra_info_input)
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
        if self._character_id <= 0:
            QMessageBox.warning(self, "提示", "无效的人物记录")
            return

        name_translated = self._name_translated_input.text().strip()
        gender = self._gender_input.text().strip()
        extra_info = self._extra_info_input.text().strip()
        if not name_translated:
            QMessageBox.warning(self, "提示", "译文名不能为空")
            return

        try:
            updated = self._database.update_character(
                self._character_id,
                name_translated,
                gender or None,
                extra_info or None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return

        if not updated:
            QMessageBox.warning(self, "提示", "未找到要更新的人物记录")
            return

        self.saved.emit(self._character_id)
        self.accept()