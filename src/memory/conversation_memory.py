from __future__ import annotations

import json
from threading import RLock, Thread
from typing import Any

import requests
from langchain_classic.memory.buffer_window import ConversationBufferWindowMemory

from core.app_config import DEEPSEEK_API_KEY
from core.app_config import _MEMORY_WINDOW_SIZE
from memory.database import GameDatabase

_DEFAULT_NARRATOR_NAME = "旁白"
_SUMMARY_API_URL = "https://api.deepseek.com/chat/completions"
_SUMMARY_MODEL = "deepseek-chat"
_SUMMARY_TIMEOUT_SECONDS = 60

_conversation_memory = ConversationBufferWindowMemory(
    k=_MEMORY_WINDOW_SIZE,
    return_messages=True,
)
_conversation_lock = RLock()
_conversation_summary = ""
_conversation_summary_game_id: int | None = None
_summary_generating_game_ids: set[int] = set()
_MAX_SQLITE_INTEGER = 9223372036854775807
_default_database: GameDatabase | None = None
_UNSET_GAME_ID = object()


class SummaryGenerationError(RuntimeError):
    """前情回顾生成失败。"""


def get_conversation_memory() -> ConversationBufferWindowMemory:
    """返回全局对话记忆实例。"""
    return _conversation_memory


def _get_database(database: GameDatabase | None = None) -> GameDatabase:
    if isinstance(database, GameDatabase):
        return database

    global _default_database
    with _conversation_lock:
        if _default_database is None:
            _default_database = GameDatabase()
        return _default_database


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _extract_dialog_text(record: str) -> str:
    for separator in ("：", ":"):
        if separator in record:
            return record.split(separator, 1)[1]

    return record


def _record_exists(dialog_text: str) -> bool:
    normalized_dialog_text = _normalize_text(dialog_text)
    if not normalized_dialog_text:
        return False

    with _conversation_lock:
        for record in _conversation_memory.chat_memory.messages:
            if _normalize_text(_extract_dialog_text(record.content)) == normalized_dialog_text:
                return True

    return False


def is_duplicate_ocr_dialog_result(result: dict[str, Any]) -> bool:
    """判断 OCR 结果是否与历史对话重复。"""

    raw_dialog = result.get("dialog", [])
    if not isinstance(raw_dialog, list):
        raise TypeError("OCR 结果中的 dialog 必须为 list")

    dialog_texts: list[str] = []
    for line in raw_dialog:
        if not isinstance(line, str):
            line = str(line)
        normalized_line = _normalize_text(line)
        if normalized_line:
            dialog_texts.append(normalized_line)

    if not dialog_texts:
        return False

    return all(_record_exists(dialog_text) for dialog_text in dialog_texts)


def append_conversation_record(
    role_name: str | None,
    dialog_text: str,
    game_id: int | None = None,
    database: GameDatabase | None = None,
) -> str:
    """追加一条对话记录，格式为“角色名：对话原文”。

    当 role_name 为空时，自动回退为“旁白”。
    """
    text = dialog_text.strip()
    if not text:
        raise ValueError("dialog_text 不能为空")

    speaker = role_name.strip() if isinstance(role_name, str) else ""
    normalized_speaker = speaker or _DEFAULT_NARRATOR_NAME
    record = f"{normalized_speaker}：{text}"

    with _conversation_lock:
        _conversation_memory.chat_memory.add_user_message(record)
        messages = _conversation_memory.chat_memory.messages
        if len(messages) > _MEMORY_WINDOW_SIZE:
            del messages[: len(messages) - _MEMORY_WINDOW_SIZE]

    _on_record_appended(game_id=game_id, database=database)
    return record


def append_ocr_dialog_result(
    result: dict[str, Any],
    game_id: int | None = None,
    database: GameDatabase | None = None,
) -> list[str]:
    """把 OCR 结构化结果追加到对话历史。"""
    role_name = result.get("name")
    raw_dialog = result.get("dialog", [])
    if not isinstance(raw_dialog, list):
        raise TypeError("OCR 结果中的 dialog 必须为 list")

    appended_records: list[str] = []
    for line in raw_dialog:
        if not isinstance(line, str):
            line = str(line)
        stripped_line = line.strip()
        if not stripped_line:
            continue
        appended_records.append(
            append_conversation_record(
                role_name=role_name,
                dialog_text=stripped_line,
                game_id=game_id,
                database=database,
            )
        )

    return appended_records


def get_recent_conversation_records() -> list[str]:
    """返回窗口内最近的记录（最多 _MEMORY_WINDOW_SIZE 条）。"""
    with _conversation_lock:
        return [message.content for message in _conversation_memory.chat_memory.messages]


def get_conversation_summary() -> str:
    """返回当前前情回顾。"""
    with _conversation_lock:
        return _conversation_summary


def set_conversation_summary(
    content: str,
    game_id: int | None | object = _UNSET_GAME_ID,
) -> None:
    """设置当前前情回顾。"""
    summary_text = content.strip() if isinstance(content, str) else str(content).strip()
    with _conversation_lock:
        global _conversation_summary, _conversation_summary_game_id
        _conversation_summary = summary_text
        if game_id is not _UNSET_GAME_ID:
            _conversation_summary_game_id = (
                game_id if isinstance(game_id, int) and game_id > 0 else None
            )


def clear_conversation_memory() -> None:
    """清空对话历史。"""
    with _conversation_lock:
        _conversation_memory.chat_memory.clear()


def clear_conversation_summary() -> None:
    """清空前情回顾。"""
    set_conversation_summary("", game_id=None)


def _on_record_appended(
    game_id: int | None,
    database: GameDatabase | None = None,
) -> None:
    if not isinstance(game_id, int) or game_id <= 0:
        return
    _trigger_summary_generation_if_needed(game_id=game_id, database=database)


def _trigger_summary_generation_if_needed(
    game_id: int,
    database: GameDatabase | None = None,
) -> None:
    if game_id <= 0:
        return

    target_database = _get_database(database)
    with _conversation_lock:
        if game_id in _summary_generating_game_ids:
            return

    latest_summary_record = target_database.get_latest_summary_record(game_id)
    latest_summary_end_id = (
        latest_summary_record.get("end_conversation_id")
        if isinstance(latest_summary_record, dict)
        else None
    )
    previous_summary = (
        str(latest_summary_record.get("content", ""))
        if isinstance(latest_summary_record, dict)
        else ""
    )
    start_id = (
        int(latest_summary_end_id) + 1
        if isinstance(latest_summary_end_id, int) and latest_summary_end_id > 0
        else 1
    )
    unsummarized_dialogues = target_database.get_dialogues_by_game_range(
        game_id=game_id,
        start_id=start_id,
        end_id=_MAX_SQLITE_INTEGER,
    )
    if len(unsummarized_dialogues) < _MEMORY_WINDOW_SIZE:
        return

    batch_dialogues = unsummarized_dialogues[:_MEMORY_WINDOW_SIZE]
    batch_start_id = int(batch_dialogues[0]["id"])
    batch_end_id = int(batch_dialogues[-1]["id"])

    with _conversation_lock:
        if game_id in _summary_generating_game_ids:
            return
        _summary_generating_game_ids.add(game_id)

    worker = Thread(
        target=_generate_summary_task,
        args=(
            game_id,
            previous_summary,
            batch_start_id,
            batch_end_id,
            target_database,
        ),
        daemon=True,
    )
    worker.start()


def _generate_summary_task(
    game_id: int,
    previous_summary: str,
    start_id: int,
    end_id: int,
    database: GameDatabase,
) -> None:
    generated_summary: str | None = None
    try:
        dialogue_rows = database.get_dialogues_by_game_range(
            game_id=game_id,
            start_id=start_id,
            end_id=end_id,
        )
        recent_records = _build_summary_records(dialogue_rows)
        if len(recent_records) < _MEMORY_WINDOW_SIZE:
            print(
                f"对话条数不足，跳过本轮前情回顾生成: game_id={game_id}, "
                f"start_id={start_id}, end_id={end_id}"
            )
            return

        generated_summary = _call_summary_api(previous_summary, recent_records)
        database.add_summary(
            game_id=game_id,
            content=generated_summary,
            start_id=start_id,
            end_id=end_id,
        )
    except (SummaryGenerationError, ValueError) as exc:
        print(f"生成前情回顾失败，保留旧摘要并等待下次重试: {exc}")
        return
    finally:
        with _conversation_lock:
            _summary_generating_game_ids.discard(game_id)

    if generated_summary is None:
        return

    with _conversation_lock:
        if _conversation_summary_game_id == game_id:
            _conversation_summary = generated_summary

    _trigger_summary_generation_if_needed(game_id=game_id, database=database)


def _build_summary_records(dialogue_rows: list[dict[str, object]]) -> list[str]:
    records: list[str] = []
    for row in dialogue_rows:
        raw_dialog_text = row.get("dialog_text_original", "")
        dialog_text = (
            raw_dialog_text.strip()
            if isinstance(raw_dialog_text, str)
            else str(raw_dialog_text).strip()
        )
        if not dialog_text:
            continue

        raw_name = row.get("character_name_original")
        speaker = (
            raw_name.strip()
            if isinstance(raw_name, str) and raw_name.strip()
            else _DEFAULT_NARRATOR_NAME
        )
        records.append(f"{speaker}：{dialog_text}")
    return records


def _call_summary_api(previous_summary: str, recent_records: list[str]) -> str:
    summary_prompt = _build_summary_prompt(previous_summary, recent_records)
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _SUMMARY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是游戏剧情整理助手。请用第三人称生成简洁连贯的前情回顾，"
                    "继承旧摘要脉络并融入新对话信息，控制在200字以内。"
                    "只输出 JSON：{\"summary\":\"...\"}。"
                ),
            },
            {"role": "user", "content": summary_prompt},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    try:
        response = requests.post(
            _SUMMARY_API_URL,
            headers=headers,
            json=payload,
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise SummaryGenerationError(f"请求 DeepSeek 摘要接口失败: {exc}") from exc

    if response.status_code != 200:
        raise SummaryGenerationError(
            f"DeepSeek 摘要接口返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SummaryGenerationError(
            f"DeepSeek 摘要响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise SummaryGenerationError(f"DeepSeek 摘要内容类型错误: {content!r}")

    summary_text = _extract_summary_text(content)
    if not summary_text:
        raise SummaryGenerationError("摘要结果为空")

    if len(summary_text) > 200:
        summary_text = summary_text[:200].rstrip()

    return summary_text


def _build_summary_prompt(previous_summary: str, recent_records: list[str]) -> str:
    payload = {
        "previous_summary": previous_summary,
        "recent_records": recent_records[-_MEMORY_WINDOW_SIZE:],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_summary_text(content: str) -> str:
    text = content.strip()
    if not text:
        return ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return ""
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return ""

    if not isinstance(parsed, dict):
        return ""

    summary_text = parsed.get("summary", "")
    if not isinstance(summary_text, str):
        summary_text = str(summary_text)

    return " ".join(summary_text.strip().split())
