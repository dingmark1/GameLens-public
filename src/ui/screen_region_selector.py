from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import QWidget
import mss
import mss.tools
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
SCREENSHOT_DIR = PROJECT_ROOT / "logs" / "screenshots"
TEMP_SCREENSHOT_PATH = PROJECT_ROOT / "temp_screenshot.png"

# 该模块负责“框选屏幕区域”的全部交互与数据处理：
# 1. 通过全屏透明覆盖层让用户按住鼠标拖出矩形区域；
# 2. 把 Qt 逻辑坐标转换到实际显示器坐标；
# 3. 保存截图与配置，供后续 OCR 识别使用。
# 该逻辑必须同时兼容多显示器环境和高 DPI 缩放场景。


class _ScreenMapping(TypedDict):
    logical_geometry: QRect
    monitor: dict[str, int]
    scale_x: float
    scale_y: float


class ScreenSelectionOverlay(QWidget):
    """全屏透明框选覆盖层。用户在此窗口中拖动鼠标即可选择截图区域。"""

    selection_completed = pyqtSignal(QRect)
    selection_cancelled = pyqtSignal()

    def __init__(self) -> None:
        # 覆盖所有屏幕的虚拟几何区域，保证拖选能跨越多屏工作区。
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._virtual_geometry = _get_virtual_geometry()
        self._start_point: QPoint | None = None
        self._end_point: QPoint | None = None

        # 覆盖层尺寸与屏幕总范围一致，且设置为无边框、置顶、半透明背景。
        self.setGeometry(self._virtual_geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Escape 用于取消框选，和鼠标右键/单击等情况保持一致。
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.selection_cancelled.emit()
            return

        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 仅处理左键拖拽，避免误触发截图选择；右键和中键保留 Qt 默认行为。
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        self._start_point = event.globalPosition().toPoint()
        self._end_point = self._start_point
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # 在拖拽过程中实时更新终点，界面会重绘当前矩形框。
        if self._start_point is None:
            super().mouseMoveEvent(event)
            return

        self._end_point = event.globalPosition().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # 松开左键完成框选，随后验证矩形是否合法，并发出完成信号。
        if event.button() != Qt.MouseButton.LeftButton or self._start_point is None:
            super().mouseReleaseEvent(event)
            return

        self._end_point = event.globalPosition().toPoint()
        selection_rect = QRect(self._start_point, self._end_point).normalized()

        self.close()

        # 面积过小的框选通常表示误操作，直接视为取消。
        if selection_rect.width() <= 1 or selection_rect.height() <= 1:
            self.selection_cancelled.emit()
            return

        self.selection_completed.emit(selection_rect)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event

        painter = QPainter(self)
        # 整体应用半透明黑色蒙层，突出“待选区域”和“已选区”的视觉层次。
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        selection_rect = self._current_local_selection_rect()
        if selection_rect is None:
            return

        # 用“清除”混合模式将当前选区挖空，形成明亮的拖拽高亮区域。
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(selection_rect, Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        pen = QPen(QColor(0, 170, 255), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(selection_rect.adjusted(0, 0, -1, -1))

    def _current_local_selection_rect(self) -> QRect | None:
        # 把全局坐标转换为当前覆盖层本地坐标，从而计算真实绘制矩形。
        if self._start_point is None or self._end_point is None:
            return None

        start_point = self.mapFromGlobal(self._start_point)
        end_point = self.mapFromGlobal(self._end_point)
        return QRect(start_point, end_point).normalized()


class SelectionOutlineOverlay(QWidget):
    """用于在选择完成后显示已选区域边框的轻量覆盖层。"""

    def __init__(self, selection_rect: QRect) -> None:
        # 这个覆盖层只负责绘制边框，不接收鼠标事件，避免干扰用户后续操作。
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._selection_rect = QRect(0, 0, selection_rect.width(), selection_rect.height())

        self.setGeometry(selection_rect)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event

        painter = QPainter(self)
        pen = QPen(QColor(0, 255, 0), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(self._selection_rect.adjusted(0, 0, -1, -1))


def save_selection_screenshot(selection_rect: QRect) -> Path:
    # 先确保截图目录存在，再按时间戳生成唯一文件名，便于后续追踪和回放。
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    screenshot = _capture_selection(selection_rect)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"screen_region_{timestamp}.png"

    if not screenshot.save(str(screenshot_path), "PNG"):
        raise RuntimeError(f"无法保存截图到 {screenshot_path}")

    return screenshot_path


def save_selection_config(selection_rect: QRect, screenshot_path: Path) -> None:
    # 将矩形坐标和截图文件路径持久化到 JSON 配置，供后续识别流程直接读取。
    config_data = _load_config()
    normalized_rect = selection_rect.normalized()

    config_data["selection_region"] = {
        "top_left": {
            "x": normalized_rect.topLeft().x(),
            "y": normalized_rect.topLeft().y(),
        },
        "bottom_right": {
            "x": normalized_rect.bottomRight().x(),
            "y": normalized_rect.bottomRight().y(),
        },
        "width": normalized_rect.width(),
        "height": normalized_rect.height(),
        "screenshot_path": str(screenshot_path),
    }

    _write_config(config_data)


def reset_selection_config() -> None:
    # 启动时清空缓存区域，确保后续识别不会误用旧坐标。
    try:
        config_data = _load_config()
    except RuntimeError as exc:
        print(f"配置文件已损坏，重建 selection_region: {exc}")
        config_data = {}

    config_data["selection_region"] = _default_selection_region()
    _write_config(config_data)


def load_selection_rect_from_config() -> QRect | None:
    # 读取已持久化的区域信息，并将 JSON 坐标恢复成 QRect 用于后续识别动作。
    try:
        config_data = _load_config()
    except RuntimeError as exc:
        print(f"读取框选区域配置失败: {exc}")
        return None

    selection_region = config_data.get("selection_region")
    if not isinstance(selection_region, dict):
        print("selection_region 配置不存在或格式错误")
        return None

    top_left = selection_region.get("top_left")
    bottom_right = selection_region.get("bottom_right")
    if not isinstance(top_left, dict) or not isinstance(bottom_right, dict):
        print("selection_region.top_left 或 selection_region.bottom_right 格式错误")
        return None

    left = _get_int_value(top_left, "x")
    top = _get_int_value(top_left, "y")
    right = _get_int_value(bottom_right, "x")
    bottom = _get_int_value(bottom_right, "y")
    if None in (left, top, right, bottom):
        print("selection_region 坐标字段类型错误")
        return None

    if -1 in (left, top, right, bottom):
        return None

    return QRect(QPoint(left, top), QPoint(right, bottom)).normalized()


def capture_selection_with_mss(selection_rect: QRect) -> Path:
    # 该函数以 mss 为底层抓屏，能更稳定地处理多屏拼接和缩放比例问题。
    normalized_rect = selection_rect.normalized()
    output_path = TEMP_SCREENSHOT_PATH
    with mss.MSS() as screenshotter:
        mappings = _build_screen_mappings(screenshotter)
        fragments: list[tuple[Image.Image, QRect]] = []

        for mapping in mappings:
            logical_geometry = mapping["logical_geometry"]
            logical_intersection = normalized_rect.intersected(logical_geometry)
            if logical_intersection.isEmpty():
                continue

            monitor_rect = _logical_rect_to_monitor_rect(logical_intersection, mapping)
            screenshot = screenshotter.grab(monitor_rect)
            fragment_image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            fragments.append((fragment_image, _monitor_dict_to_qrect(monitor_rect)))

        if not fragments:
            raise RuntimeError("未获取到可用截图内容，请检查框选区域是否在可见屏幕内")

        union_rect = fragments[0][1]
        for _, fragment_rect in fragments[1:]:
            union_rect = union_rect.united(fragment_rect)

        stitched_image = Image.new("RGB", (union_rect.width(), union_rect.height()))
        for fragment_image, fragment_rect in fragments:
            stitched_image.paste(
                fragment_image,
                (
                    fragment_rect.left() - union_rect.left(),
                    fragment_rect.top() - union_rect.top(),
                ),
            )

        stitched_image.save(output_path)

    return output_path


def _load_config() -> dict[str, object]:
    # 配置文件保存在项目根目录的 config/config.json，记录最后一次选择的区域。
    if not CONFIG_PATH.exists():
        return {"selection_region": _default_selection_region()}

    try:
        config_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{CONFIG_PATH} 不是合法的 JSON 文件") from exc

    if not isinstance(config_data, dict):
        raise RuntimeError(f"{CONFIG_PATH} 的根节点必须是 JSON 对象")

    return config_data


def _write_config(config_data: dict[str, object]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _default_selection_region() -> dict[str, object]:
    # 默认值使用 -1 作为“未设置”的占位符，便于识别尚未执行过框选。
    return {
        "top_left": {
            "x": -1,
            "y": -1,
        },
        "bottom_right": {
            "x": -1,
            "y": -1,
        },
        "width": -1,
        "height": -1,
        "screenshot_path": "",
    }


def _get_int_value(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    if not isinstance(value, int):
        return None

    return value


def _build_screen_mappings(screenshotter: mss.MSS) -> list[_ScreenMapping]:
    # Qt 的 screens() 返回逻辑显示器对象，而 mss 的 monitors() 返回物理屏幕信息。
    # 这一步把两套坐标系的屏幕映射起来，后续抓图时才不会出现跨屏偏移。
    qt_screens = QGuiApplication.screens()
    mss_monitors = screenshotter.monitors[1:]
    mappings: list[_ScreenMapping] = []
    used_monitor_indices: set[int] = set()

    for qt_screen in qt_screens:
        logical_geometry = qt_screen.geometry()
        expected_width = logical_geometry.width() * qt_screen.devicePixelRatio()
        expected_height = logical_geometry.height() * qt_screen.devicePixelRatio()

        best_index = -1
        best_score = float("inf")
        for monitor_index, monitor in enumerate(mss_monitors):
            if monitor_index in used_monitor_indices:
                continue

            width_diff = abs(monitor["width"] - expected_width)
            height_diff = abs(monitor["height"] - expected_height)
            score = width_diff + height_diff
            if score < best_score:
                best_score = score
                best_index = monitor_index

        if best_index == -1:
            continue

        used_monitor_indices.add(best_index)
        monitor = mss_monitors[best_index]
        scale_x = monitor["width"] / logical_geometry.width()
        scale_y = monitor["height"] / logical_geometry.height()

        mappings.append(
            {
                "logical_geometry": logical_geometry,
                "monitor": monitor,
                "scale_x": scale_x,
                "scale_y": scale_y,
            }
        )

    return mappings


def _logical_rect_to_monitor_rect(
    logical_rect: QRect,
    mapping: _ScreenMapping,
) -> dict[str, int]:
    # 把 Qt 的逻辑坐标（基于窗口、桌面坐标系）转换成 mss 物理屏幕坐标。
    logical_geometry = mapping["logical_geometry"]
    monitor = mapping["monitor"]
    scale_x = mapping["scale_x"]
    scale_y = mapping["scale_y"]

    local_left = logical_rect.left() - logical_geometry.left()
    local_top = logical_rect.top() - logical_geometry.top()
    local_right_exclusive = logical_rect.right() + 1 - logical_geometry.left()
    local_bottom_exclusive = logical_rect.bottom() + 1 - logical_geometry.top()

    physical_left = monitor["left"] + round(local_left * scale_x)
    physical_top = monitor["top"] + round(local_top * scale_y)
    physical_right_exclusive = monitor["left"] + round(local_right_exclusive * scale_x)
    physical_bottom_exclusive = monitor["top"] + round(local_bottom_exclusive * scale_y)

    physical_width = max(1, physical_right_exclusive - physical_left)
    physical_height = max(1, physical_bottom_exclusive - physical_top)

    return {
        "left": physical_left,
        "top": physical_top,
        "width": physical_width,
        "height": physical_height,
    }


def _monitor_dict_to_qrect(monitor: dict[str, int]) -> QRect:
    # mss 返回的监视器字典中包含 left/top/width/height，转成 QRect 方便后续合并矩形。
    return QRect(
        monitor["left"],
        monitor["top"],
        monitor["width"],
        monitor["height"],
    )


def _capture_selection(selection_rect: QRect) -> QPixmap:
    # 通过 QScreen.grabWindow 组合截图，优先保证与用户拖拽区域一致的可见内容。
    normalized_rect = selection_rect.normalized()
    screenshot = QPixmap(normalized_rect.size())
    screenshot.fill(Qt.GlobalColor.transparent)

    painter = QPainter(screenshot)
    for screen in QGuiApplication.screens():
        screen_geometry = screen.geometry()
        intersected_rect = normalized_rect.intersected(screen_geometry)
        if intersected_rect.isEmpty():
            continue

        screen_fragment = screen.grabWindow(
            0,
            intersected_rect.x() - screen_geometry.x(),
            intersected_rect.y() - screen_geometry.y(),
            intersected_rect.width(),
            intersected_rect.height(),
        )
        painter.drawPixmap(
            intersected_rect.x() - normalized_rect.x(),
            intersected_rect.y() - normalized_rect.y(),
            screen_fragment,
        )

    painter.end()
    return screenshot


def _get_virtual_geometry() -> QRect:
    # 合并所有屏幕的几何信息，形成一个“虚拟屏幕”，让覆盖层能够覆盖全屏幕区域。
    screens = QGuiApplication.screens()
    if not screens:
        raise RuntimeError("当前没有可用的屏幕，无法执行框选")

    virtual_geometry = QRect(screens[0].geometry())
    for screen in screens[1:]:
        virtual_geometry = virtual_geometry.united(screen.geometry())

    return virtual_geometry
