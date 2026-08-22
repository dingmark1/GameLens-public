from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.game_intro_generator import (
    GameIntroGenerationError,
    generate_game_intro,
)
from memory.database import GameDatabase


class GameIntroGenerationWorker(QObject):
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, game_name: str) -> None:
        super().__init__()
        self._game_name = game_name

    @pyqtSlot()
    def run(self) -> None:
        current_thread = QThread.currentThread()
        try:
            game_intro = generate_game_intro(self._game_name)
        except (GameIntroGenerationError, ValueError) as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(self._game_name, game_intro)
        finally:
            if current_thread is not None:
                current_thread.quit()


class GameIntroWindow(QWidget):
    """当前游戏的简介查看与维护窗口。"""

    def __init__(
        self,
        database: GameDatabase,
        game_name: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._database = database
        self._game_name = game_name
        self._generation_thread: QThread | None = None
        self._generation_worker: GameIntroGenerationWorker | None = None
        self._pending_game_name: str | None = None
        self._is_shutting_down = False

        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(720, 480)

        layout = QVBoxLayout(self)
        self._intro_editor = QTextEdit(self)
        self._intro_editor.setPlaceholderText("当前游戏暂无简介，可点击“生成”获取简介。")
        layout.addWidget(self._intro_editor)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._generate_button = QPushButton("生成", self)
        self._edit_button = QPushButton("修改", self)
        self._delete_button = QPushButton("删除", self)
        button_row.addWidget(self._generate_button)
        button_row.addWidget(self._edit_button)
        button_row.addWidget(self._delete_button)
        layout.addLayout(button_row)

        self._generate_button.clicked.connect(self._on_generate_clicked)
        self._edit_button.clicked.connect(self._on_edit_clicked)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        self.set_game(game_name)

    def set_game(self, game_name: str) -> None:
        normalized_name = game_name.strip()
        if not normalized_name:
            raise ValueError("游戏名称不能为空")
        if self._generation_thread is not None:
            self._pending_game_name = normalized_name
            return

        self._pending_game_name = None
        self._game_name = normalized_name
        self.setWindowTitle(f"游戏简介 - {normalized_name}")
        self.refresh_game_intro()

    def refresh_game_intro(self) -> None:
        game_intro = self._database.get_game_intro_by_game_name(self._game_name)
        if game_intro is None:
            self._intro_editor.clear()
        else:
            self._intro_editor.setPlainText(str(game_intro.get("game_intro", "")))
        self._update_button_states()

    def _update_button_states(self) -> None:
        is_generating = self._generation_thread is not None
        has_intro = (
            self._database.get_game_intro_by_game_name(self._game_name) is not None
        )
        self._intro_editor.setEnabled(not is_generating)
        self._generate_button.setEnabled(not is_generating)
        self._edit_button.setEnabled(not is_generating and has_intro)
        self._delete_button.setEnabled(not is_generating and has_intro)
        self._generate_button.setText("生成中..." if is_generating else "生成")

    def _on_generate_clicked(self) -> None:
        if self._generation_thread is not None:
            return

        if self._database.get_game_intro_by_game_name(self._game_name) is not None:
            confirm = QMessageBox.question(
                self,
                "确认重新生成",
                "当前游戏已有简介，重新生成后将替换原内容。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._generation_thread = QThread(self)
        self._generation_worker = GameIntroGenerationWorker(self._game_name)
        self._generation_worker.moveToThread(self._generation_thread)
        self._generation_thread.started.connect(self._generation_worker.run)
        self._generation_worker.finished.connect(self._on_generation_finished)
        self._generation_worker.failed.connect(self._on_generation_failed)
        self._generation_thread.finished.connect(self._cleanup_generation_thread)
        self._generation_thread.start()
        self._update_button_states()

    @pyqtSlot(str, str)
    def _on_generation_finished(self, game_name: str, game_intro: str) -> None:
        try:
            existing_intro = self._database.get_game_intro_by_game_name(game_name)
            if existing_intro is None:
                self._database.add_game_intro(game_name, game_intro)
            else:
                self._database.update_game_intro(game_name, game_intro)
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return

        if game_name == self._game_name:
            self._intro_editor.setPlainText(game_intro)

    @pyqtSlot(str)
    def _on_generation_failed(self, error_message: str) -> None:
        QMessageBox.critical(self, "生成失败", error_message)

    def _cleanup_generation_thread(self) -> None:
        if self._generation_worker is not None:
            self._generation_worker.deleteLater()
            self._generation_worker = None
        if self._generation_thread is not None:
            self._generation_thread.deleteLater()
            self._generation_thread = None
        pending_game_name = self._pending_game_name
        self._pending_game_name = None
        if pending_game_name is not None:
            self.set_game(pending_game_name)
        else:
            self._update_button_states()

    def _on_edit_clicked(self) -> None:
        game_intro = self._intro_editor.toPlainText().strip()
        if not game_intro:
            QMessageBox.warning(self, "提示", "游戏简介不能为空")
            return

        if self._database.update_game_intro(self._game_name, game_intro):
            QMessageBox.information(self, "提示", "游戏简介已修改")
            self.refresh_game_intro()
            return

        QMessageBox.warning(self, "提示", "修改失败，未找到对应的游戏简介")

    def _on_delete_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除游戏“{self._game_name}”的简介吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._database.delete_game_intro(self._game_name) > 0:
            self.refresh_game_intro()
            return

        QMessageBox.warning(self, "提示", "删除失败，未找到对应的游戏简介")

    def shutdown(self) -> None:
        self._is_shutting_down = True
        if self._generation_thread is not None:
            if self._generation_worker is not None:
                self._generation_worker.finished.disconnect(
                    self._on_generation_finished
                )
                self._generation_worker.failed.disconnect(self._on_generation_failed)
            self._generation_thread.quit()
            self._generation_thread.wait()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._generation_thread is not None and not self._is_shutting_down:
            QMessageBox.information(self, "提示", "游戏简介正在生成，请稍后再关闭窗口")
            event.ignore()
            return
        super().closeEvent(event)
