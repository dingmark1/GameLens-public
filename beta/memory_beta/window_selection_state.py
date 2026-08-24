from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectedGameWindow:
    hwnd: int
    title: str
    class_name: str
    process_id: int


@dataclass(frozen=True)
class ParsedGameWindowInfo:
    game_name: str
    top_bar_vertical_ratio: float
    dialog_box_x1: float
    dialog_box_x2: float
    dialog_box_y1: float
    dialog_box_y2: float


_SELECTED_GAME_WINDOW: SelectedGameWindow | None = None
_PARSED_GAME_WINDOW_INFO: ParsedGameWindowInfo | None = None


def set_selected_game_window(window_info: SelectedGameWindow | None) -> None:
    global _SELECTED_GAME_WINDOW
    _SELECTED_GAME_WINDOW = window_info


def get_selected_game_window() -> SelectedGameWindow | None:
    return _SELECTED_GAME_WINDOW


def set_parsed_game_window_info(parsed_info: ParsedGameWindowInfo | None) -> None:
    global _PARSED_GAME_WINDOW_INFO
    _PARSED_GAME_WINDOW_INFO = parsed_info


def get_parsed_game_window_info() -> ParsedGameWindowInfo | None:
    return _PARSED_GAME_WINDOW_INFO


def clear_selected_game_window() -> None:
    set_selected_game_window(None)
