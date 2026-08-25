from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
import json
from pathlib import Path
from threading import RLock, Thread

import requests


_SUMMARY_API_URL = "https://api.deepseek.com/chat/completions"
_SUMMARY_MODEL = "deepseek-chat"
_SUMMARY_TIMEOUT_SECONDS = 60
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"
_CONFIG_SECTION = "app"
_EXTRA_TEXT_PREFIX = "额外文本："

_summary_lock = RLock()
_summary_generating = False
_dialogue_counter = 0
_dialogue_records: list[str] = []
_latest_summary = ""


class BetaSummaryError(RuntimeError):
    """beta 摘要生成失败。"""


def append_records_for_summary(records: list[str]) -> None:
    """将新增记录送入摘要计数器，并按窗口大小触发摘要生成。"""
    if not records:
        return

    window_size = _load_memory_window_size()
    if window_size <= 0:
        return

    new_dialogues: list[str] = []
    for record in records:
        normalized_record = _normalize_text(record)
        if not normalized_record:
            continue
        if normalized_record.startswith(_EXTRA_TEXT_PREFIX):
            continue
        new_dialogues.append(normalized_record)

    if not new_dialogues:
        return

    with _summary_lock:
        global _dialogue_counter
        _dialogue_records.extend(new_dialogues)
        if len(_dialogue_records) > window_size:
            del _dialogue_records[: len(_dialogue_records) - window_size]

        _dialogue_counter += len(new_dialogues)
        if _summary_generating:
            return
        if _dialogue_counter < window_size:
            return

        _dialogue_counter -= window_size
        recent_records = _dialogue_records[-window_size:]
        _set_generating_locked(True)

    _start_summary_worker(recent_records)


def get_latest_summary() -> str:
    with _summary_lock:
        return _latest_summary


def clear_summary_cache() -> None:
    with _summary_lock:
        global _dialogue_counter, _summary_generating, _latest_summary
        _dialogue_counter = 0
        _summary_generating = False
        _latest_summary = ""
        _dialogue_records.clear()


def _start_summary_worker(recent_records: list[str]) -> None:
    worker = Thread(
        target=_generate_summary_task,
        args=(recent_records,),
        daemon=True,
    )
    worker.start()


def _generate_summary_task(recent_records: list[str]) -> None:
    generated_summary: str | None = None
    next_records: list[str] = []
    try:
        generated_summary = _call_summary_api(recent_records)
    except BetaSummaryError as exc:
        print(f"beta 摘要生成失败，保留旧摘要: {exc}")
    finally:
        with _summary_lock:
            global _summary_generating, _latest_summary, _dialogue_counter
            if generated_summary is not None:
                _latest_summary = generated_summary
            _summary_generating = False

            window_size = _load_memory_window_size()
            can_continue = (
                _dialogue_counter >= window_size and len(_dialogue_records) >= window_size
            )
            if can_continue:
                _dialogue_counter -= window_size
                next_records = _dialogue_records[-window_size:]
                _summary_generating = True
            else:
                next_records = []

    if next_records:
        _start_summary_worker(next_records)


def _call_summary_api(recent_records: list[str]) -> str:
    api_key = _load_deepseek_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    prompt = _build_summary_prompt(recent_records)
    payload = {
        "model": _SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": "你是游戏剧情整理助手。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "thinking": {"type": "disabled"},
    }

    try:
        response = requests.post(
            _SUMMARY_API_URL,
            headers=headers,
            json=payload,
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise BetaSummaryError(f"请求 DeepSeek 摘要接口失败: {exc}") from exc

    if response.status_code != 200:
        raise BetaSummaryError(
            f"DeepSeek 摘要接口返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BetaSummaryError(
            f"DeepSeek 摘要响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise BetaSummaryError(f"DeepSeek 摘要内容类型错误: {content!r}")

    summary = _normalize_text(content)
    if not summary:
        raise BetaSummaryError("DeepSeek 摘要结果为空")
    return summary


def _build_summary_prompt(recent_records: list[str]) -> str:
    payload = {
        "instruction": "为我生成简短摘要。",
        "recent_records": recent_records[-_load_memory_window_size():],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.strip().split())
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _load_deepseek_api_key() -> str:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise BetaSummaryError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise BetaSummaryError(f"配置文件格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise BetaSummaryError(f"配置文件中缺少 [{_CONFIG_SECTION}] 配置段")

    api_key = parser.get(_CONFIG_SECTION, "deepseek_api_key", fallback="").strip()
    if not api_key:
        raise BetaSummaryError("配置中的 deepseek_api_key 不能为空")
    return api_key


def _load_memory_window_size() -> int:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise BetaSummaryError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise BetaSummaryError(f"配置文件格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise BetaSummaryError(f"配置文件中缺少 [{_CONFIG_SECTION}] 配置段")

    raw_value = parser.get(_CONFIG_SECTION, "memory_window_size", fallback="").strip()
    if not raw_value:
        raise BetaSummaryError("配置中的 memory_window_size 不能为空")
    value = int(raw_value)
    if value <= 0:
        raise BetaSummaryError("配置中的 memory_window_size 必须为正整数")
    return value


def _set_generating_locked(value: bool) -> None:
    global _summary_generating
    _summary_generating = value
