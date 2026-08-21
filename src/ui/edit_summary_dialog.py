from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from memory.database import GameDatabase


class EditSummaryDialog(QDialog):
    """非模态摘要编辑对话框。"""

    saved = pyqtSignal(int)

    def __init__(self, database: GameDatabase, summary: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self._database = database
        self._summary_id = int(summary.get("id", 0))

        self.setWindowTitle("修改摘要")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self._game_input = QLineEdit(self)
        self._game_input.setText(str(summary.get("game_name", "")))
        self._game_input.setReadOnly(True)

        start_value = summary.get("start_conversation_id")
        end_value = summary.get("end_conversation_id")
        if start_value is None and end_value is None:
            coverage_text = "早期摘要"
        else:
            start_text = "" if start_value is None else str(start_value)
            end_text = "" if end_value is None else str(end_value)
            coverage_text = f"{start_text} - {end_text}"

        self._coverage_input = QLineEdit(self)
        self._coverage_input.setText(coverage_text)
        self._coverage_input.setReadOnly(True)

        self._content_input = QTextEdit(self)
        self._content_input.setPlainText(str(summary.get("content", "")))

        form_layout.addRow("游戏", self._game_input)
        form_layout.addRow("覆盖范围", self._coverage_input)
        form_layout.addRow("摘要内容", self._content_input)
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
        if self._summary_id <= 0:
            QMessageBox.warning(self, "提示", "无效的摘要记录")
            return

        content = self._content_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "摘要内容不能为空")
            return

        try:
            updated = self._database.update_summary(self._summary_id, content)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return

        if not updated:
            QMessageBox.warning(self, "提示", "未找到要更新的摘要记录")
            return

        self.saved.emit(self._summary_id)
        self.accept()
