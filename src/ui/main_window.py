from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, QRect, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.ocr_engine import (
    format_dialog_result,
    prewarm_ocr_engine,
    recognize_texts,
    set_ocr_language,
)
from core.translator import TranslationError, translate_dialog_result

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


class OcrRecognitionWorker(QObject):
    """在后台线程中执行 OCR 识别的工作对象。

    设计上把耗时的图像识别逻辑从 GUI 线程剥离出去，避免界面在识别期间出现卡顿；
    识别完成后通过信号回传结果，让主窗口更新状态和展示处理结果。
    """

    # 识别成功后直接回传结构化字典，主窗口无需再做二次拆分。
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, image_path: Path) -> None:
        # image_path 指向被识别的屏幕截图文件； OCR 引擎需要在此图像基础上抽取文字。
        super().__init__()
        self._image_path = image_path

    @pyqtSlot()
    def run(self) -> None:
        try:
            # OCR 是典型的阻塞型操作，必须在非 GUI 线程中执行，否则会导致 UI 失去响应。
            recognized_texts = recognize_texts(self._image_path)
            # 先拿到带坐标的文本块，再在同一处完成“人名 / 对话”归一化，方便后续统一消费。
            structured_result = format_dialog_result(recognized_texts)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            # 把错误信息以信号形式发回主窗口，方便弹出提示框并恢复 UI 状态。
            self.failed.emit(f"OCR 识别失败: {exc}")
            return

        try:
            translated_result = translate_dialog_result(structured_result)
        except TranslationError as exc:
            self.failed.emit(f"翻译失败: {exc}")
            return
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            self.failed.emit(f"翻译失败: {exc}")
            return

        self.finished.emit(
            {
                "translation": translated_result,
                "ocr_blocks": recognized_texts,
            }
        )


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
    def __init__(self) -> None:
        # 先调用父类构造函数，完成 QMainWindow 的底层初始化，包含 Qt 对象树、事件循环等基础设施。
        super().__init__()

        # 设置窗口标题，显示在窗口顶部标题栏中，便于用户识别当前应用。
        self.setWindowTitle("GameLens")

        # 设置窗口的初始大小：宽 400 像素、高 300 像素；这是一种稳定的默认布局值，适合后续按钮和中间内容区展示。
        self.resize(400, 300)

        # 将窗口放置到当前屏幕的中心位置，避免首次启动时出现在偏离视线的区域。
        self._center_on_screen()

        # 构造主内容区域和垂直布局；所有按钮都放在这个容器中，形成简洁的控制台式界面。
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 组合1：框选按钮固定大小，水平居中，不随窗口缩放。
        button_group_layout = QHBoxLayout()
        button_group_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.select_screen_region_button = QPushButton("框选屏幕区域", self)
        self.select_screen_region_button.clicked.connect(
            self._on_select_screen_region_button_clicked
        )
        self.select_screen_region_button.setFixedSize(240, 40)
        button_group_layout.addWidget(
            self.select_screen_region_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        # “识别并翻译框选区域文字”按钮会从进程内缓存中读取上一轮选择结果，并进一步进入 OCR 流程。
        self.recognize_selected_region_text_button = QPushButton(
            "识别并翻译框选区域文字",
            self,
        )
        self.recognize_selected_region_text_button.clicked.connect(
            self._on_recognize_selected_region_text_button_clicked
        )
        self.recognize_selected_region_text_button.setFixedSize(240, 40)
        layout.addLayout(button_group_layout)

        recognize_button_row = QHBoxLayout()
        recognize_button_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recognize_button_row.addWidget(
            self.recognize_selected_region_text_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addLayout(recognize_button_row)

        # 组合2：语言选择框与勾选框固定大小，并左右组合排列。
        combo_group_layout = QHBoxLayout()
        combo_group_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        combo_group_layout.setSpacing(12)

        self.language_combo_box = QComboBox(self)
        self.language_combo_box.addItem("英语", "en")
        self.language_combo_box.addItem("日语", "japan")
        self.language_combo_box.setCurrentIndex(0)
        self.language_combo_box.currentIndexChanged.connect(
            self._on_language_combo_box_changed
        )
        self.language_combo_box.setFixedSize(120, 32)

        self.auto_recognition_checkbox = QCheckBox("循环识别", self)
        self.auto_recognition_checkbox.setChecked(False)
        self.auto_recognition_checkbox.stateChanged.connect(
            self._on_auto_recognition_checkbox_state_changed
        )
        combo_group_layout.addWidget(self.language_combo_box, alignment=Qt.AlignmentFlag.AlignCenter)
        combo_group_layout.addWidget(self.auto_recognition_checkbox, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(combo_group_layout)

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
        self._current_ocr_screenshot_path: Path | None = None
        # 自动识别状态：一个控制循环开关，一个防止任务重入。
        self._is_auto_recognizing = False
        self._is_recognition_running = False
        self._auto_recognition_enabled = False
        self._auto_recognition_timer = QTimer(self)
        self._auto_recognition_timer.setInterval(4000)
        self._auto_recognition_timer.timeout.connect(self._on_auto_recognition_timer_timeout)

        # 根据初始状态更新按钮启用状态，并在后台启动 OCR 预热。
        set_ocr_language("en")
        self._auto_recognition_enabled = self.auto_recognition_checkbox.isChecked()
        self._update_button_states()
        self._start_ocr_prewarm()

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
        # 主窗口本身不需要参与区域选择，因此先隐藏，避免干扰用户在全屏覆盖层上的拖拽。
        self.hide()
        # 使用 singleShot 延迟到下一事件循环，让窗口先完成隐藏并让覆盖层在新事件中创建，保证显示顺序稳定。
        QTimer.singleShot(0, self._start_screen_region_selection)

    def _start_screen_region_selection(self) -> None:
        # 若存在旧的选择轮廓，先关闭它，避免残留框线与新选区重叠。
        if self._selection_outline_overlay is not None:
            self._selection_outline_overlay.close()
            self._selection_outline_overlay = None
        self._hide_selection_cancel_button_overlay()

        if self._translation_overlay is not None:
            self._translation_overlay.close()
            self._translation_overlay = None

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

        print("框选区域已保存")
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

    def _on_auto_recognition_checkbox_state_changed(self, _state: int) -> None:
        self._auto_recognition_enabled = self.auto_recognition_checkbox.isChecked()

    def _on_language_combo_box_changed(self, _index: int) -> None:
        selected_lang = self.language_combo_box.currentData()
        if not isinstance(selected_lang, str):
            return

        self._wait_for_ocr_thread_if_needed()
        set_ocr_language(selected_lang)

    def _start_auto_recognition(self) -> None:
        if not self._auto_recognition_enabled:
            self._perform_single_recognition()
            return

        self._is_auto_recognizing = True
        self._auto_recognition_timer.start()
        print("[定时识别] 自动识别已启动，每 4 秒执行一次")
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
        self.auto_recognition_checkbox.setEnabled(True)
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

        self._ocr_thread = QThread(self)
        self._ocr_worker = OcrRecognitionWorker(screenshot_path)
        self._ocr_worker.moveToThread(self._ocr_thread)

        # 当线程启动后，执行 OCR 工作对象的 run()；识别结束后触发回调并关闭线程。
        self._ocr_thread.started.connect(self._ocr_worker.run)
        self._ocr_worker.finished.connect(self._on_ocr_recognition_finished)
        self._ocr_worker.failed.connect(self._on_ocr_recognition_failed)
        self._ocr_worker.finished.connect(self._ocr_thread.quit)
        self._ocr_worker.failed.connect(self._ocr_thread.quit)
        self._ocr_thread.finished.connect(self._cleanup_ocr_recognition_thread)

        self._ocr_thread.start()
        self._update_button_states()

    def _on_ocr_recognition_finished(self, result_payload: dict) -> None:
        self._is_recognition_running = False
        self._show_translation_overlay(result_payload)

    def _on_ocr_recognition_failed(self, error_message: str) -> None:
        self._is_recognition_running = False
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

            if self._translation_overlay is not None:
                self._translation_overlay.close()
                self._translation_overlay = None

            screenshot_path = self._current_ocr_screenshot_path
            self._translation_overlay = TranslationOverlay(
                selection_rect,
                screenshot_path,
                translated_result,
            )
            self._translation_overlay.show()
            if self._selection_outline_overlay is not None:
                self._selection_outline_overlay.raise_()
            if self._selection_cancel_button_overlay is not None:
                self._selection_cancel_button_overlay.raise_()
        finally:
            self._cleanup_current_ocr_screenshot_path()

    def _hide_translation_overlay(self) -> None:
        if self._translation_overlay is None:
            return

        self._translation_overlay.close()
        self._translation_overlay = None

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
            self.auto_recognition_checkbox.setEnabled(False)
            self.language_combo_box.setEnabled(False)
            self.recognize_selected_region_text_button.setText("停止识别")
            return

        self.select_screen_region_button.setEnabled(is_idle)
        self.recognize_selected_region_text_button.setEnabled(is_idle)
        self.auto_recognition_checkbox.setEnabled(is_idle)
        self.language_combo_box.setEnabled(is_idle and self._ocr_thread is None)
        self.recognize_selected_region_text_button.setText("识别并翻译框选区域文字")

    def _show_window_in_front(self) -> None:
        # 重新展示主窗口时，确保它位于其他顶层窗口前方，并主动获取焦点，方便继续操作。
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        # 关闭窗口前优雅退出后台 OCR 线程和预热线程，防止退出时出现悬挂线程或资源未释放问题。
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

        super().closeEvent(event)