from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectedGameWindow:
    hwnd: int
    title: str
    class_name: str
    process_id: int


_SELECTED_GAME_WINDOW: SelectedGameWindow | None = None


def set_selected_game_window(window_info: SelectedGameWindow | None) -> None:
    global _SELECTED_GAME_WINDOW
    _SELECTED_GAME_WINDOW = window_info


def get_selected_game_window() -> SelectedGameWindow | None:
    return _SELECTED_GAME_WINDOW


def clear_selected_game_window() -> None:
    set_selected_game_window(None)
