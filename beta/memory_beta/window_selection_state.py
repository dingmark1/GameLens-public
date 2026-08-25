from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock


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


@dataclass(frozen=True)
class BetaOcrResult:
    non_dialog_text: str
    name: str
    dialog: str


@dataclass(frozen=True)
class BetaTranslationResult:
    name: str
    dialog: str
    additional_text: str
    addition: dict[str, object]


_SELECTED_GAME_WINDOW: SelectedGameWindow | None = None
_PARSED_GAME_WINDOW_INFO: ParsedGameWindowInfo | None = None
_BETA_OCR_RESULT: BetaOcrResult | None = None
_BETA_TRANSLATION_RESULT: BetaTranslationResult | None = None
_HISTORY_RECORDS: list[str] = []
_HISTORY_LOCK = RLock()
_DEFAULT_NARRATOR_NAME = "旁白"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"
_CONFIG_SECTION = "app"


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


def set_beta_ocr_result(result: BetaOcrResult | None) -> None:
    global _BETA_OCR_RESULT
    _BETA_OCR_RESULT = result


def get_beta_ocr_result() -> BetaOcrResult | None:
    return _BETA_OCR_RESULT


def set_beta_translation_result(result: BetaTranslationResult | None) -> None:
    global _BETA_TRANSLATION_RESULT
    _BETA_TRANSLATION_RESULT = result


def get_beta_translation_result() -> BetaTranslationResult | None:
    return _BETA_TRANSLATION_RESULT


def append_conversation_history(ocr_result: BetaOcrResult) -> list[str]:
    appended_records: list[str] = []

    dialog_text = _normalize_text(ocr_result.dialog)
    speaker_name = _normalize_text(ocr_result.name)
    if dialog_text:
        speaker = speaker_name or _DEFAULT_NARRATOR_NAME
        appended_records.append(f"{speaker}：{dialog_text}")

    additional_text = _normalize_text(ocr_result.non_dialog_text)
    if additional_text:
        appended_records.append(f"额外文本：{additional_text}")

    if not appended_records:
        return []

    window_size = _load_memory_window_size()
    with _HISTORY_LOCK:
        _HISTORY_RECORDS.extend(appended_records)
        if len(_HISTORY_RECORDS) > window_size:
            del _HISTORY_RECORDS[: len(_HISTORY_RECORDS) - window_size]
    return appended_records


def get_conversation_history() -> list[str]:
    with _HISTORY_LOCK:
        return list(_HISTORY_RECORDS)


def clear_conversation_history() -> None:
    with _HISTORY_LOCK:
        _HISTORY_RECORDS.clear()


def clear_selected_game_window() -> None:
    set_selected_game_window(None)


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.strip().split())
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


@lru_cache(maxsize=1)
def _load_memory_window_size() -> int:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise RuntimeError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise ValueError(f"配置文件格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise ValueError(f"配置文件中缺少 [{_CONFIG_SECTION}] 配置段")

    raw_value = parser.get(_CONFIG_SECTION, "memory_window_size", fallback="").strip()
    if not raw_value:
        raise ValueError("配置中的 memory_window_size 不能为空")
    value = int(raw_value)
    if value <= 0:
        raise ValueError("配置中的 memory_window_size 必须为正整数")
    return value
