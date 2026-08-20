from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from PyQt6 import uic
from PyQt6.QtCore import QObject, QThread, QTimer, QRect, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStyle,
    QVBoxLayout,
)

from core.ocr_engine import (
    format_dialog_result,
    prewarm_ocr_engine,
    recognize_texts,
    set_ocr_language,
)
from core.translator import (
    TranslationError,
    has_translatable_content,
    translate_dialog_result,
)
from memory.conversation_memory import (
    append_ocr_dialog_result,
    clear_conversation_memory,
    clear_conversation_summary,
    is_duplicate_ocr_dialog_result,
)
from memory.database import GameDatabase
from core.app_config import AUTO_RECOGNITION_INTERVAL_MS
from ui.character_manager_window import CharacterManagerWindow
from ui.dialogue_manager_window import DialogueManagerWindow

from ui.screen_region_selector import (
    SelectionOutlineOverlay,
    SelectionCancelButtonOverlay,
    ScreenSelectionOverlay,
    TranslationOverlay,
    capture_selection_with_mss,
    load_selection_rect_from_memory,
    reset_selection_rect_memory,
    save_selection_rect_to_memory,
)


class AddCharacterDialog(QDialog):
    """确认并写入人物信息的对话框。"""

    def __init__(
        self,
        database: GameDatabase,
        name_original: str,
        name_translated: str,
        game_id: int,
        parent: QMainWindow,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._game_id = game_id
        self.saved_name_original: str | None = None

        self.setWindowTitle("新增人物")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self._name_original_input = QLineEdit(name_original, self)
        self._name_translated_input = QLineEdit(name_translated, self)
        self._gender_input = QLineEdit(self)
        self._extra_info_input = QLineEdit(self)

        form_layout.addRow("原文名称", self._name_original_input)
        form_layout.addRow("译文名称", self._name_translated_input)
        form_layout.addRow("性别", self._gender_input)
        form_layout.addRow("补充信息", self._extra_info_input)
        layout.addLayout(form_layout)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self._button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self._on_reject)
        layout.addWidget(self._button_box)

    def _on_accept(self) -> None:
        name_original = self._name_original_input.text().strip()
        name_translated = self._name_translated_input.text().strip()
        gender = self._gender_input.text().strip()
        extra_info = self._extra_info_input.text().strip()

        if not name_original:
            QMessageBox.warning(self, "提示", "原文名称不能为空")
            return
        if not name_translated:
            QMessageBox.warning(self, "提示", "译文名称不能为空")
            return

        try:
            self._database.add_character(
                name_original=name_original,
                name_translated=name_translated,
                game_id=self._game_id,
                gender=gender or None,
                extra_info=extra_info or None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return

        self.saved_name_original = name_original
        self.accept()

    def _on_reject(self) -> None:
        self.reject()


class OcrRecognitionWorker(QObject):
    """在后台线程中执行 OCR 识别的工作对象。

    设计上把耗时的图像识别逻辑从 GUI 线程剥离出去，避免界面在识别期间出现卡顿；
    识别完成后通过信号回传结果，让主窗口更新状态和展示处理结果。
    """

    # 识别成功后直接回传结构化字典，主窗口无需再做二次拆分。
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str, str)
    translation_context_ready = pyqtSignal(str, str, list)

    def __init__(
        self,
        image_path: Path,
        skip_duplicate_check: bool,
        request_id: str,
    ) -> None:
        # image_path 指向被识别的屏幕截图文件； OCR 引擎需要在此图像基础上抽取文字。
        super().__init__()
        self._image_path = image_path
        self._skip_duplicate_check = skip_duplicate_check
        self._request_id = request_id

    @pyqtSlot()
    def run(self) -> None:
        current_thread = QThread.currentThread()
        try:
            try:
                # OCR 是典型的阻塞型操作，必须在非 GUI 线程中执行，否则会导致 UI 失去响应。
                recognized_texts = recognize_texts(self._image_path)
                # 先拿到带坐标的文本块，再在同一处完成“人名 / 对话”归一化，方便后续统一消费。
                structured_result = format_dialog_result(recognized_texts)
                if self._skip_duplicate_check and is_duplicate_ocr_dialog_result(structured_result):
                    print("[定时识别] 识别内容与历史对话重复，跳过翻译")
                    self.finished.emit(
                        {
                            "skipped": True,
                            "request_id": self._request_id,
                            "ocr_blocks": recognized_texts,
                        }
                    )
                    return
                if not has_translatable_content(structured_result):
                    print("[定时识别] OCR 结果为空，跳过翻译")
                    self.finished.emit(
                        {
                            "skipped": True,
                            "request_id": self._request_id,
                            "ocr_blocks": recognized_texts,
                        }
                    )
                    return
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                # 把错误信息以信号形式发回主窗口，方便弹出提示框并恢复 UI 状态。
                self.failed.emit(self._request_id, f"OCR 识别失败: {exc}")
                return

            try:
                raw_name = structured_result.get("name")
                raw_dialog = structured_result.get("dialog")
                if isinstance(raw_name, str):
                    normalized_name = raw_name.strip()
                else:
                    normalized_name = ""
                dialog_original = raw_dialog if isinstance(raw_dialog, list) else []
                self.translation_context_ready.emit(
                    self._request_id,
                    normalized_name,
                    list(dialog_original),
                )
                translated_result = translate_dialog_result(
                    structured_result,
                    request_id=self._request_id,
                )
            except TranslationError as exc:
                self.failed.emit(self._request_id, f"翻译失败: {exc}")
                return
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                self.failed.emit(self._request_id, f"翻译失败: {exc}")
                return

            self.finished.emit(
                {
                    "request_id": self._request_id,
                    "translation": translated_result,
                    "ocr_blocks": recognized_texts,
                    "ocr_result": structured_result,
                }
            )
        finally:
            # 线程退出指令在工作线程自身发出，避免依赖主线程事件循环转发 quit 信号。
            if current_thread is not None:
                current_thread.quit()


class OcrPrewarmWorker(QObject):
    """启动程序时预热 OCR 引擎，减少首次识别时的等待成本。

    PaddleOCR 的首次预测通常会执行较重的初始化和模型加载，因此在主窗口显示前，
    该线程会以后台方式提前加载并执行一次极小样本，以让后续正式识别更顺畅。
    """

    finished = pyqtSignal()
    failed = pyqtSignal(str)

    @pyqtSlot()
    def run(self) -> None:
        try:
            prewarm_ocr_engine()
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            self.failed.emit(f"OCR 预热失败: {exc}")
            return

        self.finished.emit()


class MainWindow(QMainWindow):
    """GameLens 的主界面窗口，负责统一协调：

    - 选择待识别的屏幕区域；
    - 触发 OCR 识别；
    - 显示和更新界面状态；
    - 在后台线程中完成耗时任务，保证交互流畅。
    """
    clear_memory_requested = pyqtSignal()
    clear_summary_requested = pyqtSignal()

    def __init__(self) -> None:
        # 先调用父类构造函数，完成 QMainWindow 的底层初始化，包含 Qt 对象树、事件循环等基础设施。
        super().__init__()

        ui_path = Path(__file__).with_name("main_window.ui")
        uic.loadUi(str(ui_path), self)

        # 将窗口放置到当前屏幕的中心位置，避免首次启动时出现在偏离视线的区域。
        self._center_on_screen()

        self._game_database = GameDatabase()
        self._initialize_game_combo_box_data()
        self._initialize_combo_box_data()
        self._connect_ui_signals()
        self._character_manager_window: CharacterManagerWindow | None = None
        self._dialogue_manager_window: DialogueManagerWindow | None = None
        self._active_add_character_dialog: AddCharacterDialog | None = None
        self._pending_add_character_prompts: list[dict[str, object]] = []
        self._active_add_character_prompt_data: dict[str, object] | None = None
        self._is_shutting_down = False
        self.current_game_id: int | None = None
        self.pending_translations: dict[str, dict[str, object]] = {}
        self._on_game_combo_box_changed(self.game_combo_box.currentIndex())

        # UI 状态机：idle 表示可接收用户操作，selecting/recognizing 表示当前正处于前台交互或后台识别流程。
        self._ui_state = "idle"
        # 屏幕框选覆盖层对象：它在用户拖拽时临时显示并负责采集坐标；完成后由上层销毁。
        self._selection_overlay: ScreenSelectionOverlay | None = None
        # 已确认的选择框轮廓覆盖层：用于在主界面重新显示，提示用户当前选区位置。
        self._selection_outline_overlay: SelectionOutlineOverlay | None = None
        # 位于绿色虚线框右上角的取消按钮覆盖层。
        self._selection_cancel_button_overlay: SelectionCancelButtonOverlay | None = None
        # 翻译显示覆盖层：在选区上方展示 DeepSeek 的翻译结果。
        self._translation_overlay: TranslationOverlay | None = None
        # OCR 识别线程和工作对象：将重计算任务搬离 UI 线程。
        self._ocr_thread: QThread | None = None
        self._ocr_worker: OcrRecognitionWorker | None = None
        # OCR 预热线程：在应用启动后就开始加载模型，确保后续第一次识别更快。
        self._ocr_prewarm_thread: QThread | None = None
        self._ocr_prewarm_worker: OcrPrewarmWorker | None = None
        self._pending_ocr_dialog_result: dict[str, Any] | None = None
        self._current_ocr_screenshot_path: Path | None = None
        # 自动识别状态：一个控制循环开关，一个防止任务重入。
        self._is_auto_recognizing = False
        self._is_recognition_running = False
        self._auto_recognition_enabled = False
        self._auto_recognition_timer = QTimer(self)
        self._auto_recognition_timer.setInterval(AUTO_RECOGNITION_INTERVAL_MS)
        self._auto_recognition_timer.timeout.connect(self._on_auto_recognition_timer_timeout)
        self.clear_memory_requested.connect(self._clear_memory_history)
        self.clear_summary_requested.connect(self._clear_summary_history)

        # 根据初始状态更新按钮启用状态，并在后台启动 OCR 预热。
        set_ocr_language("en")
        mode_is_loop = self.recognition_mode_combo_box.currentData()
        self._auto_recognition_enabled = bool(mode_is_loop)
        self._update_button_states()
        self._start_ocr_prewarm()

    def _initialize_combo_box_data(self) -> None:
        self.recognition_mode_combo_box.clear()
        self.recognition_mode_combo_box.addItem("单次识别", False)
        self.recognition_mode_combo_box.addItem("循环识别", True)
        self.recognition_mode_combo_box.setCurrentIndex(0)

        self.language_combo_box.clear()
        self.language_combo_box.addItem("英语", "en")
        self.language_combo_box.addItem("日语", "japan")
        self.language_combo_box.setCurrentIndex(0)

    def _initialize_game_combo_box_data(self) -> None:
        self.game_add_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder)
        )
        self.game_delete_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self._reload_game_combo_box()

    def _reload_game_combo_box(self, selected_game_name: str | None = None) -> None:
        self.game_combo_box.clear()
        self.game_combo_box.addItem("未选择游戏", None)

        for game in self._game_database.list_games():
            game_name = game.get("game_name")
            if isinstance(game_name, str) and game_name.strip():
                self.game_combo_box.addItem(game_name, game.get("id"))

        if selected_game_name is None:
            self.game_combo_box.setCurrentIndex(0)
            return

        index = self.game_combo_box.findText(
            selected_game_name,
            Qt.MatchFlag.MatchExactly,
        )
        if index >= 0:
            self.game_combo_box.setCurrentIndex(index)
            return

        self.game_combo_box.setCurrentIndex(0)

    def _connect_ui_signals(self) -> None:
        self.select_screen_region_button.clicked.connect(
            self._on_select_screen_region_button_clicked
        )
        self.recognize_selected_region_text_button.clicked.connect(
            self._on_recognize_selected_region_text_button_clicked
        )
        self.recognition_mode_combo_box.currentIndexChanged.connect(
            self._on_recognition_mode_combo_box_changed
        )
        self.language_combo_box.currentIndexChanged.connect(
            self._on_language_combo_box_changed
        )
        self.game_combo_box.currentIndexChanged.connect(
            self._on_game_combo_box_changed
        )
        self.game_add_button.clicked.connect(self._on_game_add_button_clicked)
        self.game_delete_button.clicked.connect(self._on_game_delete_button_clicked)
        self.clear_memory_button.clicked.connect(self._on_clear_memory_button_clicked)
        self.clear_summary_button.clicked.connect(self._on_clear_summary_button_clicked)
        self.character_manager_button.clicked.connect(
            self._on_character_manager_button_clicked
        )
        self.dialogue_manager_button.clicked.connect(
            self._on_dialogue_manager_button_clicked
        )

    def _on_game_combo_box_changed(self, _index: int) -> None:
        game_id = self.game_combo_box.currentData()
        if isinstance(game_id, int) and game_id > 0:
            self.current_game_id = game_id
            print(f"当前游戏已切换为 ID={game_id}")
        else:
            self.current_game_id = None
            print("当前处于临时模式（未选择游戏）")
        self.pending_translations.clear()

    def _on_game_add_button_clicked(self) -> None:
        dialog = QInputDialog(self)
        dialog.setWindowTitle("添加游戏")
        dialog.setLabelText("请输入游戏名称：")
        dialog.setOkButtonText("确认")
        dialog.setCancelButtonText("取消")
        dialog.setTextValue("")

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        game_name = dialog.textValue().strip()
        if not game_name:
            QMessageBox.warning(self, "提示", "游戏名称不能为空")
            return

        try:
            self._game_database.add_game(game_name)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            self._reload_game_combo_box(selected_game_name=game_name)
            return

        self._reload_game_combo_box(selected_game_name=game_name)
        print(f"已添加游戏：{game_name}")

    def _on_game_delete_button_clicked(self) -> None:
        current_index = self.game_combo_box.currentIndex()
        if current_index <= 0:
            QMessageBox.information(self, "提示", "“未选择游戏”不能被删除")
            return

        game_name = self.game_combo_box.currentText().strip()
        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除游戏“{game_name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        deleted_count = self._game_database.delete_game(game_name)
        if deleted_count <= 0:
            QMessageBox.warning(self, "提示", f"未找到游戏：{game_name}")
            self._reload_game_combo_box()
            return

        self._reload_game_combo_box()
        self.game_combo_box.setCurrentIndex(0)
        print(f"已删除游戏：{game_name}，删除数量={deleted_count}")

    def _center_on_screen(self) -> None:
        # 获取当前窗口附着的屏幕对象；在多显示器环境下，screen() 返回的是该窗口当前所在的那块屏幕。
        screen = self.screen()
        if screen is None:
            # 极少数情况下（例如无可用屏幕信息）拿不到 screen，
            # 此时保留 Qt 默认的初始位置，不强行硬编码窗口坐标。
            return

        # frameGeometry() 返回的是窗口的外部几何尺寸，包含标题栏与边框。
        # 在做居中时使用它能更准确地对齐视觉中心，而不是只看可绘制区域。
        frame_geometry = self.frameGeometry()

        # 将窗口外框中心对齐到屏幕的 availableGeometry 中心，
        # 这样任务栏等系统占用区域不会被误算进窗口居中位置。
        frame_geometry.moveCenter(screen.availableGeometry().center())

        # 再把窗口左上角移动到计算出的坐标，使窗口真正居中显示。
        self.move(frame_geometry.topLeft())

    def _on_select_screen_region_button_clicked(self) -> None:
        # 只有在空闲态下才允许启动新的框选操作，避免重复打开多个覆盖层造成状态混乱。
        if self._ui_state != "idle":
            print(f"当前状态为 {self._ui_state}，忽略新的框选请求")
            return

        print("框选屏幕区域按钮被点击了")
        self._set_ui_state("selecting")
        # 不直接 hide 主窗口，避免 Windows 在重新 show 时出现短暂白底闪烁；
        # 改为临时透明化，保持窗口原生句柄连续存在，减少重绘抖动。
        self.setWindowOpacity(0.0)
        self.setEnabled(False)
        # 使用 singleShot 延迟到下一事件循环，让主窗口透明化状态先生效，并在新事件中创建覆盖层，保证显示顺序稳定。
        QTimer.singleShot(0, self._start_screen_region_selection)

    def _start_screen_region_selection(self) -> None:
        # 若存在旧的选择轮廓，先关闭它，避免残留框线与新选区重叠。
        if self._selection_outline_overlay is not None:
            self._selection_outline_overlay.close()
            self._selection_outline_overlay = None
        self._hide_selection_cancel_button_overlay()

        if self._translation_overlay is not None:
            self._translation_overlay.hide()

        try:
            # 创建覆盖整个虚拟屏幕的透明选择层，用户在其中拖拽鼠标即可选区。
            self._selection_overlay = ScreenSelectionOverlay()
        except RuntimeError as exc:
            # 若当前环境没有可用屏幕或其他初始化失败，恢复 UI 并提示用户。
            self._set_ui_state("idle")
            QMessageBox.critical(self, "错误", str(exc))
            self._show_window_in_front()
            return

        # 把框选过程中的完成与取消信号绑定回主窗口的处理函数。
        self._selection_overlay.selection_completed.connect(
            self._on_screen_region_selection_completed
        )
        self._selection_overlay.selection_cancelled.connect(
            self._on_screen_region_selection_cancelled
        )
        self._selection_overlay.show()

    def _on_screen_region_selection_completed(self, selection_rect: QRect) -> None:
        # 选择完毕后清空临时覆盖层引用，避免持有已关闭对象导致内存或状态异常。
        self._selection_overlay = None

        try:
            # 仅保存框选坐标，不再写入任何截图日志文件。
            save_selection_rect_to_memory(selection_rect)
        except RuntimeError as exc:
            QMessageBox.critical(self, "错误", str(exc))
            self._set_ui_state("idle")
            self._show_window_in_front()
            return

        # 选区成功后，在屏幕上叠加一条绿色虚线框，提醒用户当前区域边界。
        self._selection_outline_overlay = SelectionOutlineOverlay(selection_rect)
        self._selection_outline_overlay.show()
        self._show_selection_cancel_button_overlay(selection_rect)

        left = selection_rect.left()
        top = selection_rect.top()
        right = selection_rect.right()
        bottom = selection_rect.bottom()
        width = selection_rect.width()
        height = selection_rect.height()
        print(
            f"框选区域已保存，左上角=({left}, {top})，右下角=({right}, {bottom})，"
            f"宽={width}，高={height}"
        )
        self._set_ui_state("idle")
        self._show_window_in_front()

    def _on_screen_region_selection_cancelled(self) -> None:
        # 用户按 Esc 或拖选面积过小时，视为取消当前操作并恢复到可交互状态。
        self._selection_overlay = None
        self._hide_selection_cancel_button_overlay()
        self._set_ui_state("idle")
        self._show_window_in_front()

    def _on_cancel_selection_button_clicked(self) -> None:
        self._hide_selection_cancel_button_overlay()
        self._hide_selection_outline_overlay()
        self._hide_translation_overlay()
        reset_selection_rect_memory()
        print("已取消框选区域，内存缓存已重置为未选择状态")

    def _show_selection_cancel_button_overlay(self, selection_rect: QRect) -> None:
        if self._selection_cancel_button_overlay is not None:
            self._selection_cancel_button_overlay.close()

        self._selection_cancel_button_overlay = SelectionCancelButtonOverlay(selection_rect)
        self._selection_cancel_button_overlay.cancel_requested.connect(
            self._on_cancel_selection_button_clicked
        )
        self._selection_cancel_button_overlay.show()
        self._selection_cancel_button_overlay.raise_()

    def _hide_selection_cancel_button_overlay(self) -> None:
        if self._selection_cancel_button_overlay is None:
            return

        self._selection_cancel_button_overlay.close()
        self._selection_cancel_button_overlay = None

    def _hide_selection_outline_overlay(self) -> None:
        if self._selection_outline_overlay is None:
            return

        self._selection_outline_overlay.close()
        self._selection_outline_overlay = None

    def _on_recognize_selected_region_text_button_clicked(self) -> None:
        if self._is_auto_recognizing:
            self._stop_auto_recognition()
            return

        # 只有在空闲态下才允许启动自动识别，避免与框选流程冲突。
        if self._ui_state != "idle":
            print(f"当前状态为 {self._ui_state}，忽略自动识别启动请求")
            return

        selection_rect = load_selection_rect_from_memory()
        if selection_rect is None:
            QMessageBox.warning(self, "提示", "未选择区域")
            return

        self._start_auto_recognition()

    def _on_recognition_mode_combo_box_changed(self, _index: int) -> None:
        mode_is_loop = self.recognition_mode_combo_box.currentData()
        if not isinstance(mode_is_loop, bool):
            return

        self._auto_recognition_enabled = mode_is_loop

    def _on_language_combo_box_changed(self, _index: int) -> None:
        selected_lang = self.language_combo_box.currentData()
        if not isinstance(selected_lang, str):
            return

        self._wait_for_ocr_thread_if_needed()
        set_ocr_language(selected_lang)

    def _on_clear_memory_button_clicked(self) -> None:
        self.clear_memory_requested.emit()

    def _on_clear_summary_button_clicked(self) -> None:
        self.clear_summary_requested.emit()

    def _on_character_manager_button_clicked(self) -> None:
        if self._character_manager_window is None:
            self._character_manager_window = CharacterManagerWindow(self._game_database)
            self._character_manager_window.destroyed.connect(
                self._on_character_manager_window_destroyed
            )

        self._character_manager_window.refresh_characters()
        self._character_manager_window.show()
        self._character_manager_window.raise_()
        self._character_manager_window.activateWindow()

    def _on_character_manager_window_destroyed(self, *_args: object) -> None:
        self._character_manager_window = None

    def _on_dialogue_manager_button_clicked(self) -> None:
        if self._dialogue_manager_window is None:
            self._dialogue_manager_window = DialogueManagerWindow(self._game_database)
            self._dialogue_manager_window.destroyed.connect(
                self._on_dialogue_manager_window_destroyed
            )

        self._dialogue_manager_window.refresh_dialogues()
        self._dialogue_manager_window.show()
        self._dialogue_manager_window.raise_()
        self._dialogue_manager_window.activateWindow()

    def _on_dialogue_manager_window_destroyed(self, *_args: object) -> None:
        self._dialogue_manager_window = None

    def _clear_memory_history(self) -> None:
        clear_conversation_memory()
        self._pending_ocr_dialog_result = None
        print("对话记忆已清空")

    def _clear_summary_history(self) -> None:
        clear_conversation_summary()
        print("前情回顾已清空")

    def _start_auto_recognition(self) -> None:
        if not self._auto_recognition_enabled:
            self._perform_single_recognition()
            return

        self._is_auto_recognizing = True
        self._auto_recognition_timer.start()
        print("[定时识别] 自动识别已启动")
        self._update_button_states()
        self._perform_single_recognition()

    def _stop_auto_recognition(self) -> None:
        self._auto_recognition_timer.stop()
        self._is_auto_recognizing = False

        if self._ocr_thread is not None:
            print("[定时识别] 正在等待当前 OCR 任务完成...")
            self._ocr_thread.quit()
            self._ocr_thread.wait()
            self._is_recognition_running = False

        print("[定时识别] 自动识别已停止")
        self.recognition_mode_combo_box.setEnabled(True)
        self._update_button_states()

    def _wait_for_ocr_thread_if_needed(self) -> None:
        if self._ocr_thread is None:
            return

        self._ocr_thread.wait()
        self._is_recognition_running = False

    def _on_auto_recognition_timer_timeout(self) -> None:
        if self._is_recognition_running:
            print("[定时识别] 上一次识别未完成，跳过本次")
            return

        self._perform_single_recognition()

    def _perform_single_recognition(self) -> None:
        if self._is_recognition_running:
            print("[定时识别] 上一次识别未完成，跳过本次")
            return

        self._is_recognition_running = True
        selection_rect = load_selection_rect_from_memory()
        if selection_rect is None:
            print("[定时识别] 未找到已框选区域，跳过本次识别")
            self._is_recognition_running = False
            return

        translation_overlay = self._translation_overlay
        should_restore_translation_overlay = (
            translation_overlay is not None and translation_overlay.isVisible()
        )

        if should_restore_translation_overlay and translation_overlay is not None:
            translation_overlay.hide()

        try:
            try:
                temp_screenshot_path = capture_selection_with_mss(
                    selection_rect,
                    translation_overlay=translation_overlay,
                )
            finally:
                if should_restore_translation_overlay and translation_overlay is not None:
                    translation_overlay.show()
            self._current_ocr_screenshot_path = temp_screenshot_path
            self._start_ocr_recognition(temp_screenshot_path)
        except RuntimeError as exc:
            print(f"[定时识别] 识别任务启动失败: {exc}")
            self._cleanup_current_ocr_screenshot_path()
            self._is_recognition_running = False

    def _start_ocr_recognition(self, screenshot_path: Path) -> None:
        # 一次只允许一个 OCR 线程运行，避免多个同时识别任务争抢同一状态与 UI 状态。
        if self._ocr_thread is not None:
            raise RuntimeError("OCR 识别线程仍在运行，无法启动新任务")

        pending_ocr_dialog_result = self._pending_ocr_dialog_result
        if pending_ocr_dialog_result is not None:
            try:
                append_ocr_dialog_result(pending_ocr_dialog_result)
            except (RuntimeError, TypeError, ValueError) as exc:
                self._pending_ocr_dialog_result = pending_ocr_dialog_result
                raise RuntimeError(f"追加上一轮原文到记忆失败: {exc}") from exc
            self._pending_ocr_dialog_result = None

        request_id = uuid.uuid4().hex
        if self.current_game_id is not None:
            self.pending_translations[request_id] = {
                "name_original": "",
                "dialog_original": [],
                "game_id": self.current_game_id,
            }
        self._ocr_thread = QThread(self)
        self._ocr_worker = OcrRecognitionWorker(
            screenshot_path,
            self._is_auto_recognizing,
            request_id,
        )
        self._ocr_worker.moveToThread(self._ocr_thread)

        # 当线程启动后，执行 OCR 工作对象的 run()；识别结束后触发回调并关闭线程。
        self._ocr_thread.started.connect(self._ocr_worker.run)
        self._ocr_worker.translation_context_ready.connect(
            self._on_translation_context_ready
        )
        self._ocr_worker.finished.connect(self._on_ocr_recognition_finished)
        self._ocr_worker.failed.connect(self._on_ocr_recognition_failed)
        self._ocr_thread.finished.connect(self._cleanup_ocr_recognition_thread)

        self._ocr_thread.start()
        self._update_button_states()

    def _on_ocr_recognition_finished(self, result_payload: dict) -> None:
        self._is_recognition_running = False
        request_id = result_payload.get("request_id")
        if result_payload.get("skipped"):
            if isinstance(request_id, str) and request_id:
                self.pending_translations.pop(request_id, None)
            self._cleanup_current_ocr_screenshot_path()
            return
        self._store_pending_dialogues(result_payload)
        ocr_result = result_payload.get("ocr_result")
        if isinstance(ocr_result, dict):
            self._pending_ocr_dialog_result = ocr_result
        self._show_translation_overlay(result_payload)

    def _store_pending_dialogues(self, result_payload: dict) -> None:
        request_id = result_payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return

        pending_entry = self.pending_translations.pop(request_id, None)
        if not isinstance(pending_entry, dict):
            return

        game_id = pending_entry.get("game_id")
        name_original = pending_entry.get("name_original")
        dialog_original = pending_entry.get("dialog_original")
        translation = result_payload.get("translation")
        if not isinstance(game_id, int) or game_id <= 0:
            return
        if not isinstance(translation, dict):
            return

        original_dialogs = dialog_original if isinstance(dialog_original, list) else []
        if not original_dialogs:
            ocr_result = result_payload.get("ocr_result")
            if isinstance(ocr_result, dict):
                raw_ocr_dialog = ocr_result.get("dialog")
                if isinstance(raw_ocr_dialog, list):
                    original_dialogs = raw_ocr_dialog
                if not isinstance(name_original, str) or not name_original.strip():
                    raw_ocr_name = ocr_result.get("name")
                    if isinstance(raw_ocr_name, str):
                        name_original = raw_ocr_name.strip()

        translated_dialogs_raw = translation.get("dialog")
        translated_dialogs = (
            translated_dialogs_raw if isinstance(translated_dialogs_raw, list) else []
        )
        if not original_dialogs and not translated_dialogs:
            return

        normalized_name_original = (
            name_original.strip()
            if isinstance(name_original, str) and name_original.strip()
            else None
        )
        translated_name_raw = translation.get("name")
        translated_name = (
            translated_name_raw.strip()
            if isinstance(translated_name_raw, str) and translated_name_raw.strip()
            else ""
        )

        max_count = max(len(original_dialogs), len(translated_dialogs))
        for index in range(max_count):
            original_text = ""
            translated_text = ""

            if index < len(original_dialogs):
                raw_original_text = original_dialogs[index]
                if isinstance(raw_original_text, str):
                    original_text = raw_original_text.strip()
                elif raw_original_text is not None:
                    original_text = str(raw_original_text).strip()

            if index < len(translated_dialogs):
                raw_translated_text = translated_dialogs[index]
                if isinstance(raw_translated_text, str):
                    translated_text = raw_translated_text.strip()
                elif raw_translated_text is not None:
                    translated_text = str(raw_translated_text).strip()

            self._pending_add_character_prompts.append(
                {
                    "game_id": game_id,
                    "name_original": normalized_name_original,
                    "name_translated": translated_name,
                    "dialog_text_original": original_text,
                    "dialog_text_translated": translated_text,
                }
            )
        self._process_pending_dialogue_storage()

    @pyqtSlot(str, str, list)
    def _on_translation_context_ready(
        self,
        request_id: str,
        name_original: str,
        dialog_original: list,
    ) -> None:
        pending_entry = self.pending_translations.get(request_id)
        if not isinstance(pending_entry, dict):
            return

        normalized_name = name_original.strip()
        if normalized_name:
            pending_entry["name_original"] = normalized_name

        if isinstance(dialog_original, list):
            pending_entry["dialog_original"] = list(dialog_original)

    def _process_pending_dialogue_storage(self) -> None:
        if self._active_add_character_dialog is not None:
            return

        while self._pending_add_character_prompts:
            prompt_data = self._pending_add_character_prompts.pop(0)
            game_id = prompt_data.get("game_id")
            if not isinstance(game_id, int) or game_id <= 0:
                continue

            name_original_value = prompt_data.get("name_original")
            name_original = (
                name_original_value.strip()
                if isinstance(name_original_value, str) and name_original_value.strip()
                else None
            )
            dialog_text_original = str(prompt_data.get("dialog_text_original", ""))
            dialog_text_translated = str(prompt_data.get("dialog_text_translated", ""))

            if name_original is None:
                try:
                    self._game_database.add_dialogue(
                        game_id=game_id,
                        character_name_original=None,
                        dialog_text_original=dialog_text_original,
                        dialog_text_translated=dialog_text_translated,
                    )
                except ValueError as exc:
                    print(f"保存旁白对话失败: {exc}")
                continue

            if self._game_database.character_exists(name_original, game_id):
                try:
                    self._game_database.add_dialogue(
                        game_id=game_id,
                        character_name_original=name_original,
                        dialog_text_original=dialog_text_original,
                        dialog_text_translated=dialog_text_translated,
                    )
                except ValueError as exc:
                    print(f"保存角色对话失败: {exc}")
                continue

            self._active_add_character_prompt_data = {
                "game_id": game_id,
                "name_original": name_original,
                "name_translated": prompt_data.get("name_translated", ""),
                "dialog_text_original": dialog_text_original,
                "dialog_text_translated": dialog_text_translated,
            }
            self._show_add_character_prompt(self._active_add_character_prompt_data)
            return

    def _show_add_character_prompt(self, prompt_data: dict[str, object]) -> None:
        name_original = str(prompt_data.get("name_original", ""))
        name_translated = str(prompt_data.get("name_translated", ""))
        game_id = int(prompt_data.get("game_id", 0))

        dialog = AddCharacterDialog(
            database=self._game_database,
            name_original=name_original,
            name_translated=name_translated,
            game_id=game_id,
            parent=self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(self._on_add_character_dialog_finished)
        self._active_add_character_dialog = dialog
        dialog.open()

    def _on_add_character_dialog_finished(self, _result: int) -> None:
        prompt_data = self._active_add_character_prompt_data
        active_dialog = self._active_add_character_dialog
        self._active_add_character_prompt_data = None
        self._active_add_character_dialog = None
        if _result == int(QDialog.DialogCode.Accepted) and isinstance(prompt_data, dict):
            game_id = prompt_data.get("game_id")
            if isinstance(game_id, int) and game_id > 0:
                selected_name_original = prompt_data.get("name_original")
                if isinstance(active_dialog, AddCharacterDialog):
                    saved_name_original = active_dialog.saved_name_original
                    if isinstance(saved_name_original, str) and saved_name_original.strip():
                        selected_name_original = saved_name_original.strip()
                try:
                    self._game_database.add_dialogue(
                        game_id=game_id,
                        character_name_original=(
                            selected_name_original
                            if isinstance(selected_name_original, str)
                            else None
                        ),
                        dialog_text_original=str(prompt_data.get("dialog_text_original", "")),
                        dialog_text_translated=str(
                            prompt_data.get("dialog_text_translated", "")
                        ),
                    )
                except ValueError as exc:
                    print(f"保存对话失败: {exc}")

        if self._is_shutting_down:
            return
        self._process_pending_dialogue_storage()

    def _on_ocr_recognition_failed(self, request_id: str, error_message: str) -> None:
        self._is_recognition_running = False
        if request_id:
            self.pending_translations.pop(request_id, None)
        print(error_message)
        self._cleanup_current_ocr_screenshot_path()

    def _show_translation_overlay(self, result_payload: dict) -> None:
        try:
            translated_result = result_payload.get("translation")
            ocr_blocks = result_payload.get("ocr_blocks")

            if not isinstance(translated_result, dict):
                print("翻译结果缺失，无法显示覆盖层")
                return

            if not isinstance(ocr_blocks, list):
                print("OCR 坐标信息缺失，无法显示覆盖层")
                return

            selection_rect = load_selection_rect_from_memory()
            if selection_rect is None:
                print("未找到已框选区域，无法显示翻译覆盖层")
                return

            screenshot_path = self._current_ocr_screenshot_path
            if self._translation_overlay is None:
                self._translation_overlay = TranslationOverlay(
                    selection_rect,
                    screenshot_path,
                    translated_result,
                )
            else:
                self._translation_overlay.update_content(
                    selection_rect,
                    screenshot_path,
                    translated_result,
                )
            self._translation_overlay.show()
            self._translation_overlay.raise_()
            if self._selection_outline_overlay is not None:
                self._selection_outline_overlay.raise_()
            if self._selection_cancel_button_overlay is not None:
                self._selection_cancel_button_overlay.raise_()
        finally:
            self._cleanup_current_ocr_screenshot_path()

    def _hide_translation_overlay(self) -> None:
        if self._translation_overlay is None:
            return

        self._translation_overlay.hide()

    def _cleanup_ocr_recognition_thread(self) -> None:
        # 线程结束后清理 worker 和线程对象，避免 Qt 对象树中残留无效对象。
        if self._ocr_worker is not None:
            self._ocr_worker.deleteLater()
            self._ocr_worker = None

        if self._ocr_thread is not None:
            self._ocr_thread.deleteLater()
            self._ocr_thread = None

        self._update_button_states()

    def _cleanup_current_ocr_screenshot_path(self) -> None:
        screenshot_path = self._current_ocr_screenshot_path
        self._current_ocr_screenshot_path = None
        if screenshot_path is None or not screenshot_path.exists():
            return

        screenshot_path.unlink()

    def _start_ocr_prewarm(self) -> None:
        # 程序初始化时触发一次预热，早准备 OCR 模型，以减少首次真实识别的延迟。
        self._ocr_prewarm_thread = QThread(self)
        self._ocr_prewarm_worker = OcrPrewarmWorker()
        self._ocr_prewarm_worker.moveToThread(self._ocr_prewarm_thread)

        self._ocr_prewarm_thread.started.connect(self._ocr_prewarm_worker.run)
        self._ocr_prewarm_worker.finished.connect(self._ocr_prewarm_thread.quit)
        self._ocr_prewarm_worker.failed.connect(self._on_ocr_prewarm_failed)
        self._ocr_prewarm_worker.failed.connect(self._ocr_prewarm_thread.quit)
        self._ocr_prewarm_thread.finished.connect(self._cleanup_ocr_prewarm_thread)

        self._ocr_prewarm_thread.start()

    def _on_ocr_prewarm_failed(self, error_message: str) -> None:
        # 预热失败并不一定代表主功能不可用；通常只记录日志，避免影响用户正常启动。
        print(error_message)

    def _cleanup_ocr_prewarm_thread(self) -> None:
        # 清理 OCR 预热线程对象，避免后台任务残留导致资源泄漏。
        if self._ocr_prewarm_worker is not None:
            self._ocr_prewarm_worker.deleteLater()
            self._ocr_prewarm_worker = None

        if self._ocr_prewarm_thread is not None:
            self._ocr_prewarm_thread.deleteLater()
            self._ocr_prewarm_thread = None

    def _set_ui_state(self, state: str) -> None:
        # 状态切换是整个 UI 控制的核心：
        # - 空闲时允许用户点击按钮；
        # - 选择或识别时禁用交互，以防止重复操作和冲突。
        self._ui_state = state
        self._update_button_states()

    def _update_button_states(self) -> None:
        is_idle = self._ui_state == "idle"
        if self._is_auto_recognizing:
            self.select_screen_region_button.setEnabled(False)
            self.recognize_selected_region_text_button.setEnabled(True)
            self.recognition_mode_combo_box.setEnabled(False)
            self.language_combo_box.setEnabled(False)
            self.game_combo_box.setEnabled(False)
            self.game_add_button.setEnabled(False)
            self.game_delete_button.setEnabled(False)
            self.character_manager_button.setEnabled(False)
            self.dialogue_manager_button.setEnabled(False)
            self.recognize_selected_region_text_button.setText("停止识别")
            return

        self.select_screen_region_button.setEnabled(is_idle)
        self.recognize_selected_region_text_button.setEnabled(is_idle)
        self.recognition_mode_combo_box.setEnabled(is_idle)
        self.language_combo_box.setEnabled(is_idle and self._ocr_thread is None)
        self.game_combo_box.setEnabled(is_idle)
        self.game_add_button.setEnabled(is_idle)
        self.game_delete_button.setEnabled(is_idle)
        self.character_manager_button.setEnabled(is_idle)
        self.dialogue_manager_button.setEnabled(is_idle)
        self.recognize_selected_region_text_button.setText("识别并翻译框选区域文字")

    def _show_window_in_front(self) -> None:
        # 重新展示主窗口时，确保它位于其他顶层窗口前方，并主动获取焦点，方便继续操作。
        self.setWindowOpacity(1.0)
        self.setEnabled(True)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        # 关闭窗口前优雅退出后台 OCR 线程和预热线程，防止退出时出现悬挂线程或资源未释放问题。
        self._is_shutting_down = True
        self._auto_recognition_timer.stop()
        self._hide_selection_cancel_button_overlay()
        self._hide_selection_outline_overlay()
        if self._ocr_thread is not None:
            self._ocr_thread.quit()
            self._ocr_thread.wait()

        if self._ocr_prewarm_thread is not None:
            self._ocr_prewarm_thread.quit()
            self._ocr_prewarm_thread.wait()

        if self._translation_overlay is not None:
            self._translation_overlay.close()
            self._translation_overlay = None

        if self._character_manager_window is not None:
            self._character_manager_window.close()
            self._character_manager_window = None

        if self._dialogue_manager_window is not None:
            self._dialogue_manager_window.close()
            self._dialogue_manager_window = None

        if self._active_add_character_dialog is not None:
            self._active_add_character_dialog.close()
            self._active_add_character_dialog = None
        self._active_add_character_prompt_data = None
        self._pending_add_character_prompts.clear()

        self._game_database.close()
        super().closeEvent(event)