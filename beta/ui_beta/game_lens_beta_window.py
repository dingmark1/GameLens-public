from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QWidget


class GameLensBetaWindow(QWidget):
    """实验版入口窗口。"""

    return_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        ui_path = Path(__file__).with_name("game_lens_beta_window.ui")
        uic.loadUi(str(ui_path), self)

        self.setWindowFlag(Qt.WindowType.Window, True)
        self._center_on_screen()

        self.return_classic_mode_button.clicked.connect(
            self._on_return_classic_mode_clicked
        )

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return

        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(screen.availableGeometry().center())
        self.move(frame_geometry.topLeft())

    def _on_return_classic_mode_clicked(self) -> None:
        self.return_requested.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.return_requested.emit()
        super().closeEvent(event)
