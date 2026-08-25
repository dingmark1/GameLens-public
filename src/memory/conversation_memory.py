from __future__ import annotations

import json
from threading import RLock, Thread
from typing import Any, TypedDict

import requests
from langchain_classic.memory.buffer_window import ConversationBufferWindowMemory

from core.app_config import DEEPSEEK_API_KEY
from core.app_config import _MEMORY_WINDOW_MULTIPLIER
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
_SUMMARY_CONTEXT_RECORD_LIMIT = _MEMORY_WINDOW_SIZE * _MEMORY_WINDOW_MULTIPLIER
_default_database: GameDatabase | None = None
_UNSET_GAME_ID = object()


class SummaryGenerationError(RuntimeError):
    """前情回顾生成失败。"""


class CharacterExtraInfoUpdate(TypedDict):
    """经过校验、可安全写入数据库的人物补充信息。"""

    name_original: str
    extra_info: str


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

        recent_records = _temporary_summary_pending_records[:_MEMORY_WINDOW_SIZE]
        generation_token = _temporary_summary_generation_token
        _temporary_summary_generating = True

    worker = Thread(
        target=_generate_temporary_summary_task,
        args=(recent_records, generation_token),
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
    summary_context_start_id = max(
        1,
        batch_end_id - _SUMMARY_CONTEXT_RECORD_LIMIT + 1,
    )

    with _conversation_lock:
        if game_id in _summary_generating_game_ids:
            return
        _summary_generating_game_ids.add(game_id)

    worker = Thread(
        target=_generate_summary_task,
        args=(
            game_id,
            batch_start_id,
            batch_end_id,
            summary_context_start_id,
            target_database,
        ),
        daemon=True,
    )
    worker.start()


def _generate_summary_task(
    game_id: int,
    start_id: int,
    end_id: int,
    summary_context_start_id: int,
    database: GameDatabase,
) -> None:
    global _conversation_summary
    generated_summary: str | None = None
    try:
        try:
            dialogue_rows = database.get_dialogues_by_game_range(
                game_id=game_id,
                start_id=summary_context_start_id,
                end_id=end_id,
            )
            recent_records = _build_summary_records(dialogue_rows)
            related_characters = _build_related_character_information(
                dialogue_rows,
                database,
                game_id,
            )
            if len(recent_records) < _MEMORY_WINDOW_SIZE:
                print(
                    f"对话条数不足，跳过本轮前情回顾生成: game_id={game_id}, "
                    f"summary_context_start_id={summary_context_start_id}, end_id={end_id}"
                )
                return

            generated_summary = _call_summary_api(recent_records)
            database.add_summary(
                game_id=game_id,
                content=generated_summary,
                start_id=start_id,
                end_id=end_id,
            )
        except (SummaryGenerationError, ValueError) as exc:
            print(f"生成前情回顾失败，保留旧摘要并等待下次重试: {exc}")
            return

        if related_characters:
            try:
                character_updates = _call_character_relation_inference_api(
                    recent_records,
                    related_characters,
                )
                _apply_character_updates(database, game_id, character_updates)
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
    recent_records: list[str],
    generation_token: int,
) -> None:
    generated_summary: str | None = None
    global _conversation_summary, _conversation_summary_game_id, _temporary_summary_generating
    try:
        generated_summary = _call_summary_api(recent_records)
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
            _temporary_summary_generating = True
        else:
            next_batch = []
            _temporary_summary_generating = False

    if next_batch:
        worker = Thread(
            target=_generate_temporary_summary_task,
            args=(next_batch, generation_token),
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
) -> list[dict[str, str]]:
    dialogue_text = "\n".join(_build_summary_records(dialogue_rows))
    speaker_names = {
        raw_name.strip()
        for row in dialogue_rows
        if isinstance((raw_name := row.get("character_name_original")), str)
        and raw_name.strip()
    }
    related_characters: list[dict[str, str]] = []

    for character in database.list_characters_by_game(game_id):
        name_original = _normalize_optional_string(character.get("name_original"))
        name_translated = _normalize_optional_string(character.get("name_translated"))
        if not name_original:
            continue
        if (
            name_original not in speaker_names
            and name_original not in dialogue_text
            and (not name_translated or name_translated not in dialogue_text)
        ):
            continue

        related_characters.append(
            {
                "name_original": name_original,
                "name_translated": name_translated,
                "gender": _normalize_optional_string(character.get("gender")),
                "existing_extra_info": _normalize_optional_string(
                    character.get("extra_info")
                ),
            }
        )

    return related_characters


def _normalize_optional_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _call_summary_api(recent_records: list[str]) -> str:
    summary_prompt = _build_summary_prompt(recent_records)
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
                    "你是游戏剧情整理助手。请用第三人称生成简洁连贯的近期剧情梗概，"
                    "重点描述 latest_records 内的最新进展，并允许遗忘过旧剧情。"
                    "控制在200字以内。"
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
    recent_records: list[str],
) -> str:
    payload = {
        "memory_window_size": _MEMORY_WINDOW_SIZE,
        "memory_window_multiplier": _MEMORY_WINDOW_MULTIPLIER,
        "recent_records": recent_records,
        "latest_records": recent_records[-_MEMORY_WINDOW_SIZE:],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _call_character_relation_inference_api(
    recent_records: list[str],
    related_characters: list[dict[str, str]],
) -> list[CharacterExtraInfoUpdate]:
    relation_prompt = _build_character_relation_prompt(
        recent_records,
        related_characters,
    )
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
                    "你会接收 recent_records 与 characters。"
                    "请分别分析每个人物的关系、性格、行为倾向和已确认经历，"
                    "并将 existing_extra_info 与近期剧情整合成去重后的完整补充信息。"
                    "只返回有充分剧情依据且需要更新的人物；每个人物最多返回一次。"
                    "name_original 必须逐字复制 characters 中的原名，不得创造人物。"
                    "extra_info 只写该人物的信息内容，不要以该人物自己的原名或译名开头，"
                    "允许删除重复、过时或价值较低的旧描述。"
                    "只输出 JSON，格式为："
                    "{\"characters\":[{\"name_original\":\"原名\","
                    "\"extra_info\":\"整合后的完整补充信息\"}]}。"
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
    return _parse_character_updates(payload_data, related_characters)


def _build_character_relation_prompt(
    recent_records: list[str],
    related_characters: list[dict[str, str]],
) -> str:
    payload = {
        "recent_records": recent_records[-_MEMORY_WINDOW_SIZE:],
        "characters": related_characters,
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


def _parse_character_updates(
    payload: dict[str, Any],
    related_characters: list[dict[str, str]],
) -> list[CharacterExtraInfoUpdate]:
    raw_updates = payload.get("characters")
    if not isinstance(raw_updates, list):
        raise SummaryGenerationError("人物关系响应中的 characters 必须为列表")

    allowed_characters = {
        character["name_original"]: character
        for character in related_characters
        if character.get("name_original")
    }
    seen_names: set[str] = set()
    updates: list[CharacterExtraInfoUpdate] = []
    for index, raw_update in enumerate(raw_updates):
        if not isinstance(raw_update, dict):
            raise SummaryGenerationError(
                f"人物关系响应中的 characters[{index}] 必须为对象"
            )

        name_original = raw_update.get("name_original")
        extra_info = raw_update.get("extra_info")
        if not isinstance(name_original, str) or not name_original.strip():
            raise SummaryGenerationError(
                f"人物关系响应中的 characters[{index}].name_original 必须为非空字符串"
            )
        if not isinstance(extra_info, str) or not extra_info.strip():
            raise SummaryGenerationError(
                f"人物关系响应中的 characters[{index}].extra_info 必须为非空字符串"
            )

        normalized_name = name_original.strip()
        if normalized_name not in allowed_characters:
            raise SummaryGenerationError(
                f"人物关系响应包含未知人物原名: {normalized_name}"
            )
        if normalized_name in seen_names:
            raise SummaryGenerationError(
                f"人物关系响应重复包含人物: {normalized_name}"
            )

        normalized_extra_info = " ".join(extra_info.strip().split())
        character = allowed_characters[normalized_name]
        own_names = {
            normalized_name,
            character.get("name_translated", "").strip(),
        }
        if any(
            own_name and _starts_with_character_name(normalized_extra_info, own_name)
            for own_name in own_names
        ):
            raise SummaryGenerationError(
                f"人物“{normalized_name}”的补充信息不应以自身姓名开头"
            )

        seen_names.add(normalized_name)
        updates.append(
            {
                "name_original": normalized_name,
                "extra_info": normalized_extra_info,
            }
        )

    return updates


def _starts_with_character_name(extra_info: str, character_name: str) -> bool:
    if not extra_info.startswith(character_name):
        return False
    suffix = extra_info[len(character_name) :].lstrip()
    return not suffix or suffix[0] in "，,：:；;。的"


def _apply_character_updates(
    database: GameDatabase,
    game_id: int,
    updates: list[CharacterExtraInfoUpdate],
) -> None:
    for update in updates:
        name_original = update["name_original"]
        character = database.get_character_by_name_original(name_original, game_id)
        if character is None:
            raise ValueError(
                f"人物“{name_original}”不属于 game_id={game_id}，无法更新补充信息"
            )

        current_name_translated = character.get("name_translated")
        current_gender = character.get("gender")
        updated = database.update_character(
            character_id=int(character["id"]),
            name_translated=(
                current_name_translated.strip()
                if isinstance(current_name_translated, str)
                else ""
            ),
            gender=current_gender if isinstance(current_gender, str) else None,
            extra_info=update["extra_info"],
        )
        if not updated:
            raise ValueError(
                f"更新人物补充信息失败: game_id={game_id}, "
                f"name_original={name_original}"
            )
