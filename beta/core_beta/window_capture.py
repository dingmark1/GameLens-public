from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Iterable

from PIL import Image
import win32con
import win32gui
import win32ui
import win32process

from beta.memory_beta.window_selection_state import SelectedGameWindow


_SYSTEM_CLASS_NAMES = {
    "Shell_TrayWnd",
    "Progman",
    "WorkerW",
    "DV2ControlHost",
    "Shell_SecondaryTrayWnd",
}
_USER32 = ctypes.windll.user32


def enumerate_game_windows(exclude_hwnds: Iterable[int] = ()) -> list[SelectedGameWindow]:
    excluded = {int(hwnd) for hwnd in exclude_hwnds if int(hwnd) > 0}
    windows: list[SelectedGameWindow] = []

    def _collect(hwnd: int, _extra: object) -> None:
        if hwnd in excluded:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return

        class_name = win32gui.GetClassName(hwnd).strip()
        if not class_name or class_name in _SYSTEM_CLASS_NAMES:
            return

        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        windows.append(
            SelectedGameWindow(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                process_id=int(process_id),
            )
        )

    win32gui.EnumWindows(_collect, None)
    windows.sort(key=lambda item: (item.title.casefold(), item.class_name.casefold(), item.hwnd))
    return windows


def is_window_available(hwnd: int) -> bool:
    return bool(hwnd) and win32gui.IsWindow(hwnd)


def is_window_minimized(hwnd: int) -> bool:
    return bool(hwnd) and win32gui.IsIconic(hwnd)


def capture_window_to_jpeg(hwnd: int, output_path: Path) -> Path:
    if not is_window_available(hwnd):
        raise RuntimeError("窗口已关闭，请重新选择")
    if is_window_minimized(hwnd):
        raise RuntimeError("窗口已最小化，请先还原窗口后再截图")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)

    image = _capture_via_print_window(hwnd, width, height)
    if image is None:
        image = _capture_via_desktop_blt(left, top, width, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG", quality=95)
    return output_path


def _capture_via_print_window(hwnd: int, width: int, height: int) -> Image.Image | None:
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None

    src_dc = None
    mem_dc = None
    bmp = None
    try:
        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bmp)

        rendered = _USER32.PrintWindow(int(hwnd), int(mem_dc.GetSafeHdc()), 1)
        if rendered != 1:
            return None

        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)
        if not bmp_bits or all(byte_value == 0 for byte_value in bmp_bits):
            return None

        return Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits,
            "raw",
            "BGRX",
            0,
            1,
        )
    finally:
        if bmp is not None:
            win32gui.DeleteObject(bmp.GetHandle())
        if mem_dc is not None:
            mem_dc.DeleteDC()
        if src_dc is not None:
            src_dc.DeleteDC()
        if hwnd_dc:
            win32gui.ReleaseDC(hwnd, hwnd_dc)


def _capture_via_desktop_blt(
    left: int,
    top: int,
    width: int,
    height: int,
) -> Image.Image:
    desktop_hwnd = win32gui.GetDesktopWindow()
    desktop_dc_handle = win32gui.GetWindowDC(desktop_hwnd)
    if not desktop_dc_handle:
        raise RuntimeError("无法获取桌面设备上下文")

    src_dc = None
    mem_dc = None
    bmp = None
    try:
        src_dc = win32ui.CreateDCFromHandle(desktop_dc_handle)
        mem_dc = src_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bmp)

        mem_dc.BitBlt((0, 0), (width, height), src_dc, (left, top), win32con.SRCCOPY)

        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)
        if not bmp_bits:
            raise RuntimeError("桌面截图返回空图像数据")

        return Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits,
            "raw",
            "BGRX",
            0,
            1,
        )
    except Exception as exc:
        raise RuntimeError(f"窗口截图失败: {exc}") from exc
    finally:
        if bmp is not None:
            win32gui.DeleteObject(bmp.GetHandle())
        if mem_dc is not None:
            mem_dc.DeleteDC()
        if src_dc is not None:
            src_dc.DeleteDC()
        if desktop_dc_handle:
            win32gui.ReleaseDC(desktop_hwnd, desktop_dc_handle)
