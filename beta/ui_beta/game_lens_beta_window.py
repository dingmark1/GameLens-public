from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from beta.core_beta.window_capture import capture_window_to_jpeg
from beta.memory_beta.window_selection_state import (
    SelectedGameWindow,
    clear_selected_game_window,
    get_selected_game_window,
    set_selected_game_window,
)
from beta.ui_beta.window_selection_dialog import WindowSelectionDialog


class GameLensBetaWindow(QWidget):
    """实验版入口窗口。"""

    return_requested = pyqtSignal()
    window_selected = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        ui_path = Path(__file__).with_name("game_lens_beta_window.ui")
        uic.loadUi(str(ui_path), self)

        self.setWindowFlag(Qt.WindowType.Window, True)
        self._temp_image_path = Path(__file__).resolve().parents[1] / "temp.jpg"
        self._center_on_screen()
        self._refresh_selected_window_label()

        self.select_game_window_button.clicked.connect(self._on_select_game_window_clicked)
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

    def _refresh_selected_window_label(self) -> None:
        window_info = get_selected_game_window()
        if window_info is None:
            self.selected_window_label.setText("尚未选择游戏窗口")
            return

        self.selected_window_label.setText(
            f"已选择：{window_info.title}\n类名：{window_info.class_name}\nPID：{window_info.process_id}"
        )

    def _on_select_game_window_clicked(self) -> None:
        dialog = WindowSelectionDialog(self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        window_info = dialog.selected_window
        if not isinstance(window_info, SelectedGameWindow):
            return

        set_selected_game_window(window_info)
        try:
            capture_window_to_jpeg(window_info.hwnd, self._temp_image_path)
        except RuntimeError as exc:
            clear_selected_game_window()
            self._refresh_selected_window_label()
            QMessageBox.warning(self, "提示", str(exc))
            return

        self._refresh_selected_window_label()
        self.window_selected.emit(window_info)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.return_requested.emit()
        super().closeEvent(event)
