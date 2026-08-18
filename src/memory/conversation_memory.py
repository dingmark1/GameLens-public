from __future__ import annotations

from threading import RLock
from typing import Any

from langchain_classic.memory.buffer_window import ConversationBufferWindowMemory

_MEMORY_WINDOW_SIZE = 10
_DEFAULT_NARRATOR_NAME = "旁白"

_conversation_memory = ConversationBufferWindowMemory(
    k=_MEMORY_WINDOW_SIZE,
    return_messages=True,
)
_conversation_lock = RLock()


def get_conversation_memory() -> ConversationBufferWindowMemory:
    """返回全局对话记忆实例。"""
    return _conversation_memory


def append_conversation_record(role_name: str | None, dialog_text: str) -> str:
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

    return record


def append_ocr_dialog_result(result: dict[str, Any]) -> list[str]:
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
        appended_records.append(append_conversation_record(role_name, stripped_line))

    return appended_records


def get_recent_conversation_records() -> list[str]:
    """返回窗口内最近的记录（最多 10 条）。"""
    with _conversation_lock:
        return [message.content for message in _conversation_memory.chat_memory.messages]

