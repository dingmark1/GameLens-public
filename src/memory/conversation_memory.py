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
_temporary_summary_pending_records: list[str] = []
_temporary_summary_generating = False
_temporary_summary_generation_token = 0
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

    _on_record_appended(record=record, game_id=game_id, database=database)
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


def load_recent_conversation_records_from_database(
    game_id: int,
    database: GameDatabase | None = None,
) -> list[str]:
    """从数据库读取最近的对话，并重建到内存缓存中。"""

    if game_id <= 0:
        raise ValueError("game_id 必须为正整数")

    target_database = _get_database(database)
    dialogue_rows = target_database.get_dialogues_by_game_range(
        game_id=game_id,
        start_id=1,
        end_id=_MAX_SQLITE_INTEGER,
    )
    recent_rows = dialogue_rows[-_MEMORY_WINDOW_SIZE:]
    recent_records = _build_summary_records(recent_rows)

    clear_conversation_memory()
    with _conversation_lock:
        for record in recent_records:
            _conversation_memory.chat_memory.add_user_message(record)

    return recent_records


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
    global _conversation_summary, _conversation_summary_game_id
    summary_text = content.strip() if isinstance(content, str) else str(content).strip()
    with _conversation_lock:
        _conversation_summary = summary_text
        if game_id is not _UNSET_GAME_ID:
            _conversation_summary_game_id = (
                game_id if isinstance(game_id, int) and game_id > 0 else None
            )


def clear_conversation_memory() -> None:
    """清空对话历史。"""
    global _temporary_summary_generating, _temporary_summary_generation_token
    with _conversation_lock:
        _conversation_memory.chat_memory.clear()
        _temporary_summary_pending_records.clear()
        _temporary_summary_generating = False
        _temporary_summary_generation_token += 1


def clear_conversation_summary() -> None:
    """清空前情回顾。"""
    set_conversation_summary("", game_id=None)


def _on_record_appended(
    record: str,
    game_id: int | None,
    database: GameDatabase | None = None,
) -> None:
    if isinstance(game_id, int) and game_id > 0:
        _trigger_summary_generation_if_needed(game_id=game_id, database=database)
        return

    _trigger_temporary_summary_generation_if_needed(record=record)


def _trigger_temporary_summary_generation_if_needed(record: str) -> None:
    global _temporary_summary_generating, _temporary_summary_generation_token
    with _conversation_lock:
        _temporary_summary_pending_records.append(record)
        if _temporary_summary_generating:
            return
        if len(_temporary_summary_pending_records) < _MEMORY_WINDOW_SIZE:
            return

        previous_summary = _conversation_summary
        recent_records = _temporary_summary_pending_records[:_MEMORY_WINDOW_SIZE]
        generation_token = _temporary_summary_generation_token
        _temporary_summary_generating = True

    worker = Thread(
        target=_generate_temporary_summary_task,
        args=(previous_summary, recent_records, generation_token),
        daemon=True,
    )
    worker.start()


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
        related_character = _build_related_character_information(
            dialogue_rows,
            database,
            game_id,
        )
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
    try:
        addition_text = _call_character_relation_inference_api(
            recent_records,
            related_character,
        )
        _apply_summary_addition(database, game_id, addition_text)
    except (SummaryGenerationError, ValueError) as exc:
        print(f"人物关系推断失败，本轮仅保存摘要: {exc}")
    finally:
        with _conversation_lock:
            _summary_generating_game_ids.discard(game_id)

    if generated_summary is None:
        return

    with _conversation_lock:
        if _conversation_summary_game_id == game_id:
            _conversation_summary = generated_summary

    _trigger_summary_generation_if_needed(game_id=game_id, database=database)


def _generate_temporary_summary_task(
    previous_summary: str,
    recent_records: list[str],
    generation_token: int,
) -> None:
    generated_summary: str | None = None
    global _conversation_summary, _conversation_summary_game_id, _temporary_summary_generating
    try:
        generated_summary = _call_summary_api(previous_summary, recent_records)
    except SummaryGenerationError as exc:
        print(f"生成临时前情回顾失败，保留旧摘要并等待下次重试: {exc}")
        with _conversation_lock:
            if generation_token == _temporary_summary_generation_token:
                _temporary_summary_generating = False
        return

    with _conversation_lock:
        if generation_token != _temporary_summary_generation_token:
            return

        _conversation_summary = generated_summary
        _conversation_summary_game_id = None
        del _temporary_summary_pending_records[:_MEMORY_WINDOW_SIZE]

        if len(_temporary_summary_pending_records) >= _MEMORY_WINDOW_SIZE:
            next_batch = _temporary_summary_pending_records[:_MEMORY_WINDOW_SIZE]
            previous_summary = _conversation_summary
            _temporary_summary_generating = True
        else:
            next_batch = []
            _temporary_summary_generating = False

    if next_batch:
        worker = Thread(
            target=_generate_temporary_summary_task,
            args=(previous_summary, next_batch, generation_token),
            daemon=True,
        )
        worker.start()


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


def _build_related_character_information(
    dialogue_rows: list[dict[str, object]],
    database: GameDatabase,
    game_id: int,
) -> list[str]:
    seen_names: set[str] = set()
    related_character_information: list[str] = []

    for row in dialogue_rows:
        raw_name = row.get("character_name_original")
        if not isinstance(raw_name, str):
            continue
        name_original = raw_name.strip()
        if not name_original or name_original in seen_names:
            continue
        seen_names.add(name_original)

        character = database.get_character_by_name_original(name_original, game_id)
        if character is None:
            continue

        related_character_information.append(
            _format_character_information(character)
        )

    return related_character_information


def _format_character_information(character: dict[str, object]) -> str:
    name_original_value = character.get("name_original")
    name_translated_value = character.get("name_translated")
    gender_value = character.get("gender")
    extra_info_value = character.get("extra_info")

    name_original = (
        name_original_value.strip()
        if isinstance(name_original_value, str)
        else ""
    )
    name_translated = (
        name_translated_value.strip()
        if isinstance(name_translated_value, str)
        else ""
    )
    gender = gender_value.strip() if isinstance(gender_value, str) else ""
    extra_info = (
        extra_info_value.strip()
        if isinstance(extra_info_value, str)
        else ""
    )
    return f"原名：{name_original}，译名：{name_translated}，性别：{gender}，补充信息：{extra_info}"


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

    payload_data = _extract_json_payload(content)
    summary_text = payload_data.get("summary", "")
    if not isinstance(summary_text, str):
        summary_text = str(summary_text)
    summary_text = " ".join(summary_text.strip().split())
    if not summary_text:
        raise SummaryGenerationError("摘要结果为空")

    if len(summary_text) > 200:
        summary_text = summary_text[:200].rstrip()

    return summary_text


def _build_summary_prompt(
    previous_summary: str,
    recent_records: list[str],
) -> str:
    payload = {
        "previous_summary": previous_summary,
        "recent_records": recent_records[-_MEMORY_WINDOW_SIZE:],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _call_character_relation_inference_api(
    recent_records: list[str],
    related_character: list[str],
) -> str:
    relation_prompt = _build_character_relation_prompt(recent_records, related_character)
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
                    "你是游戏人物关系分析助手。"
                    "你会接收 recent_records 与 related_character。"
                    "请基于最近剧情推断人物关系，并生成可用于更新人物补充信息的结果。"
                    "addition 字段格式为：\"人名原名：附加信息；人名原名：附加信息\"。"
                    "只输出 JSON：{\"addition\":\"...\"}。"
                ),
            },
            {"role": "user", "content": relation_prompt},
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
        raise SummaryGenerationError(f"请求 DeepSeek 人物关系接口失败: {exc}") from exc

    if response.status_code != 200:
        raise SummaryGenerationError(
            f"DeepSeek 人物关系接口返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SummaryGenerationError(
            f"DeepSeek 人物关系响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise SummaryGenerationError(f"DeepSeek 人物关系内容类型错误: {content!r}")

    payload_data = _extract_json_payload(content)
    addition_value = payload_data.get("addition", "")
    if isinstance(addition_value, dict):
        return _format_addition_dict(addition_value)
    if not isinstance(addition_value, str):
        return str(addition_value).strip()
    return addition_value.strip()


def _build_character_relation_prompt(
    recent_records: list[str],
    related_character: list[str],
) -> str:
    payload = {
        "recent_records": recent_records[-_MEMORY_WINDOW_SIZE:],
        "related_character": related_character,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_json_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


def _format_addition_dict(addition: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in addition.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        if isinstance(value, str):
            normalized_value = value.strip()
        else:
            normalized_value = str(value).strip()
        parts.append(f"{normalized_key}：{normalized_value}")
    return "；".join(parts)


def _parse_addition_updates(addition_text: str) -> list[tuple[str, str]]:
    normalized_text = addition_text.strip()
    if not normalized_text:
        return []

    segments = [normalized_text]
    for separator in ("；", ";", "\n"):
        if separator in normalized_text:
            segments = [segment.strip() for segment in normalized_text.split(separator)]
            break

    updates: list[tuple[str, str]] = []
    for segment in segments:
        if not segment:
            continue
        separator_index = segment.find("：")
        if separator_index == -1:
            separator_index = segment.find(":")
        if separator_index <= 0:
            continue
        name_original = segment[:separator_index].strip()
        extra_info = segment[separator_index + 1 :].strip()
        if not name_original or not extra_info:
            continue
        updates.append((name_original, extra_info))
    return updates


def _merge_extra_info(existing: str | None, new_info: str) -> str:
    normalized_new_info = new_info.strip()
    if not normalized_new_info:
        return (existing or "").strip()

    normalized_existing = existing.strip() if isinstance(existing, str) else ""
    if not normalized_existing:
        return normalized_new_info
    if normalized_new_info in normalized_existing:
        return normalized_existing
    return f"{normalized_existing}；{normalized_new_info}"


def _apply_summary_addition(
    database: GameDatabase,
    game_id: int,
    addition_text: str,
) -> None:
    updates = _parse_addition_updates(addition_text)
    if not updates:
        return

    for name_original, extra_info in updates:
        character = database.get_character_by_name_original(name_original, game_id)
        if character is None:
            continue

        current_name_translated = character.get("name_translated")
        current_gender = character.get("gender")
        current_extra_info = character.get("extra_info")
        updated = database.update_character(
            character_id=int(character["id"]),
            name_translated=(
                current_name_translated.strip()
                if isinstance(current_name_translated, str)
                else ""
            ),
            gender=current_gender if isinstance(current_gender, str) else None,
            extra_info=_merge_extra_info(
                current_extra_info if isinstance(current_extra_info, str) else None,
                extra_info,
            ),
        )
        if not updated:
            print(f"更新人物补充信息失败: game_id={game_id}, name_original={name_original}")
