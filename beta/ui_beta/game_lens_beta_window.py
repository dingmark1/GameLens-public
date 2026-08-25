from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from beta.core_beta.window_capture import capture_window_to_jpeg
from beta.core_beta.ocr_engine_beta import recognize_window_dialog
from beta.core_beta.translator_beta import BetaTranslationError, translate_ocr_result
from beta.core_beta.window_parser import WindowParseError, parse_game_window_image
from beta.memory_beta.window_selection_state import (
    append_conversation_history,
    BetaOcrResult,
    ParsedGameWindowInfo,
    SelectedGameWindow,
    clear_conversation_history,
    BetaTranslationResult,
    clear_selected_game_window,
    get_beta_ocr_result,
    get_beta_translation_result,
    get_parsed_game_window_info,
    get_selected_game_window,
    set_beta_ocr_result,
    set_beta_translation_result,
    set_parsed_game_window_info,
    set_selected_game_window,
)
from beta.memory_beta.summary_memory_beta import (
    append_records_for_summary,
    clear_summary_cache,
    get_latest_summary,
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


class WindowOcrWorker(QObject):
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        window_info: SelectedGameWindow,
        parsed_info: ParsedGameWindowInfo,
        image_path: Path,
    ) -> None:
        super().__init__()
        self._window_info = window_info
        self._parsed_info = parsed_info
        self._image_path = image_path

    @pyqtSlot()
    def run(self) -> None:
        current_thread = QThread.currentThread()
        try:
            capture_window_to_jpeg(self._window_info.hwnd, self._image_path)
            ocr_result = recognize_window_dialog(self._image_path, self._parsed_info)
            appended_records = append_conversation_history(ocr_result)
            append_records_for_summary(appended_records)
            translation_result = translate_ocr_result(ocr_result)
            self.finished.emit(ocr_result, translation_result)
        except BetaTranslationError as exc:
            self.failed.emit(str(exc))
        except RuntimeError as exc:
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
        self._ocr_thread: QThread | None = None
        self._ocr_worker: WindowOcrWorker | None = None
        self._center_on_screen()
        self._refresh_selected_window_label()

        self.select_game_window_button.clicked.connect(self._on_select_game_window_clicked)
        self.parse_game_window_button.clicked.connect(self._on_parse_game_window_clicked)
        self.recognize_and_translate_button.clicked.connect(
            self._on_recognize_and_translate_button_clicked
        )
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
        ocr_result = get_beta_ocr_result()
        if ocr_result is not None:
            label_text += "\n已暂存 OCR 结果"
        translation_result = get_beta_translation_result()
        if translation_result is not None:
            label_text += "\n已暂存翻译结果"
        latest_summary = get_latest_summary()
        if latest_summary:
            label_text += "\n已生成摘要"
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
        set_beta_ocr_result(None)
        set_beta_translation_result(None)
        clear_conversation_history()
        clear_summary_cache()
        try:
            capture_window_to_jpeg(window_info.hwnd, self._temp_image_path)
        except RuntimeError as exc:
            clear_selected_game_window()
            set_parsed_game_window_info(None)
            set_beta_ocr_result(None)
            set_beta_translation_result(None)
            self._refresh_selected_window_label()
            QMessageBox.warning(self, "提示", str(exc))
            return

        self._refresh_selected_window_label()
        self.window_selected.emit(window_info)

    def _on_parse_game_window_clicked(self) -> None:
        if self._parse_thread is not None or self._ocr_thread is not None:
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
        self.recognize_and_translate_button.setEnabled(enabled)
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
        set_beta_ocr_result(None)
        set_beta_translation_result(None)
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

    def _on_recognize_and_translate_button_clicked(self) -> None:
        if self._parse_thread is not None or self._ocr_thread is not None:
            return

        window_info = get_selected_game_window()
        if window_info is None:
            QMessageBox.information(self, "提示", "请选择窗口")
            return

        parsed_info = get_parsed_game_window_info()
        if parsed_info is None:
            QMessageBox.information(self, "提示", "请先解析游戏窗口")
            return

        self._start_ocr_task(window_info, parsed_info)

    def _start_ocr_task(
        self,
        window_info: SelectedGameWindow,
        parsed_info: ParsedGameWindowInfo,
    ) -> None:
        if self._ocr_thread is not None:
            return

        self._set_action_buttons_enabled(False)
        self.recognize_and_translate_button.setText("识别翻译中...")
        self._ocr_thread = QThread(self)
        self._ocr_worker = WindowOcrWorker(window_info, parsed_info, self._temp_image_path)
        self._ocr_worker.moveToThread(self._ocr_thread)
        self._ocr_thread.started.connect(self._ocr_worker.run)
        self._ocr_worker.finished.connect(self._on_ocr_finished)
        self._ocr_worker.failed.connect(self._on_ocr_failed)
        self._ocr_thread.finished.connect(self._cleanup_ocr_task)
        self._ocr_thread.start()

    @pyqtSlot(object, object)
    def _on_ocr_finished(self, ocr_result: object, translation_result: object) -> None:
        if not isinstance(ocr_result, BetaOcrResult):
            self._on_ocr_failed("OCR 结果类型错误")
            return
        if not isinstance(translation_result, BetaTranslationResult):
            self._on_ocr_failed("翻译结果类型错误")
            return

        set_beta_ocr_result(ocr_result)
        set_beta_translation_result(translation_result)
        self._refresh_selected_window_label()
        QMessageBox.information(
            self,
            "提示",
            (
                "识别与翻译完成，结果已暂存。\n"
                f"OCR人名：{ocr_result.name or '(空)'}\n"
                f"OCR对话：{ocr_result.dialog or '(空)'}\n"
                f"OCR非对话文本：{ocr_result.non_dialog_text or '(空)'}\n"
                f"译名：{translation_result.name or '(空)'}\n"
                f"译对话：{translation_result.dialog or '(空)'}\n"
                f"译附加文本：{translation_result.additional_text or '(空)'}"
            ),
        )

    @pyqtSlot(str)
    def _on_ocr_failed(self, error_message: str) -> None:
        set_beta_ocr_result(None)
        set_beta_translation_result(None)
        self._refresh_selected_window_label()
        QMessageBox.warning(self, "提示", error_message)

    def _cleanup_ocr_task(self) -> None:
        if self._ocr_worker is not None:
            self._ocr_worker.deleteLater()
            self._ocr_worker = None
        if self._ocr_thread is not None:
            self._ocr_thread.deleteLater()
            self._ocr_thread = None
        self.recognize_and_translate_button.setText("识别并翻译")
        self._set_action_buttons_enabled(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._parse_thread is not None:
            self._parse_thread.quit()
            self._parse_thread.wait()
        if self._ocr_thread is not None:
            self._ocr_thread.quit()
            self._ocr_thread.wait()
        self.return_requested.emit()
        super().closeEvent(event)
