from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from beta.core_beta.window_capture import capture_window_to_jpeg
from beta.core_beta.window_parser import WindowParseError, parse_game_window_image
from beta.memory_beta.window_selection_state import (
    ParsedGameWindowInfo,
    SelectedGameWindow,
    clear_selected_game_window,
    get_parsed_game_window_info,
    get_selected_game_window,
    set_parsed_game_window_info,
    set_selected_game_window,
)
from beta.ui_beta.window_selection_dialog import WindowSelectionDialog


class WindowParseWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, image_path: Path) -> None:
        super().__init__()
        self._image_path = image_path

    @pyqtSlot()
    def run(self) -> None:
        current_thread = QThread.currentThread()
        try:
            parsed_info = parse_game_window_image(self._image_path)
            self.finished.emit(parsed_info)
        except WindowParseError as exc:
            self.failed.emit(str(exc))
        finally:
            if current_thread is not None:
                current_thread.quit()


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
        self._parse_thread: QThread | None = None
        self._parse_worker: WindowParseWorker | None = None
        self._center_on_screen()
        self._refresh_selected_window_label()

        self.select_game_window_button.clicked.connect(self._on_select_game_window_clicked)
        self.parse_game_window_button.clicked.connect(self._on_parse_game_window_clicked)
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

        label_text = (
            f"已选择：{window_info.title}\n类名：{window_info.class_name}\nPID：{window_info.process_id}"
        )
        parsed_info = get_parsed_game_window_info()
        if parsed_info is not None:
            label_text += f"\n解析暂存：{parsed_info.game_name}"
        self.selected_window_label.setText(label_text)

    def _on_select_game_window_clicked(self) -> None:
        dialog = WindowSelectionDialog(self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        window_info = dialog.selected_window
        if not isinstance(window_info, SelectedGameWindow):
            return

        set_selected_game_window(window_info)
        set_parsed_game_window_info(None)
        try:
            capture_window_to_jpeg(window_info.hwnd, self._temp_image_path)
        except RuntimeError as exc:
            clear_selected_game_window()
            set_parsed_game_window_info(None)
            self._refresh_selected_window_label()
            QMessageBox.warning(self, "提示", str(exc))
            return

        self._refresh_selected_window_label()
        self.window_selected.emit(window_info)

    def _on_parse_game_window_clicked(self) -> None:
        if self._parse_thread is not None:
            return

        window_info = get_selected_game_window()
        if window_info is None:
            QMessageBox.information(self, "提示", "请选择窗口")
            return

        if not self._temp_image_path.exists():
            QMessageBox.warning(self, "提示", "未找到窗口截图，请重新选择窗口后再解析")
            return

        self._start_parse_task()

    def _start_parse_task(self) -> None:
        if self._parse_thread is not None:
            return

        self._set_action_buttons_enabled(False)
        self.parse_game_window_button.setText("解析中...")
        self._parse_thread = QThread(self)
        self._parse_worker = WindowParseWorker(self._temp_image_path)
        self._parse_worker.moveToThread(self._parse_thread)
        self._parse_thread.started.connect(self._parse_worker.run)
        self._parse_worker.finished.connect(self._on_parse_finished)
        self._parse_worker.failed.connect(self._on_parse_failed)
        self._parse_thread.finished.connect(self._cleanup_parse_task)
        self._parse_thread.start()

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        self.select_game_window_button.setEnabled(enabled)
        self.parse_game_window_button.setEnabled(enabled)
        self.return_classic_mode_button.setEnabled(enabled)

    @pyqtSlot(object)
    def _on_parse_finished(self, parsed_info: object) -> None:
        if not isinstance(parsed_info, ParsedGameWindowInfo):
            self._on_parse_failed("窗口解析结果类型错误")
            return
        set_parsed_game_window_info(parsed_info)
        self._refresh_selected_window_label()
        QMessageBox.information(
            self,
            "提示",
            f"窗口解析完成，已暂存结果：{parsed_info.game_name}",
        )

    @pyqtSlot(str)
    def _on_parse_failed(self, error_message: str) -> None:
        set_parsed_game_window_info(None)
        self._refresh_selected_window_label()
        QMessageBox.warning(self, "提示", error_message)

    def _cleanup_parse_task(self) -> None:
        if self._parse_worker is not None:
            self._parse_worker.deleteLater()
            self._parse_worker = None
        if self._parse_thread is not None:
            self._parse_thread.deleteLater()
            self._parse_thread = None
        self.parse_game_window_button.setText("解析游戏窗口")
        self._set_action_buttons_enabled(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._parse_thread is not None:
            self._parse_thread.quit()
            self._parse_thread.wait()
        self.return_requested.emit()
        super().closeEvent(event)
