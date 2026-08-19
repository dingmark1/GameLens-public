from __future__ import annotations

import json
from threading import RLock, Thread
from typing import Any

import requests
from langchain_classic.memory.buffer_window import ConversationBufferWindowMemory

from core.app_config import DEEPSEEK_API_KEY
from core.app_config import _MEMORY_WINDOW_SIZE

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
_summary_counter = 0
_is_summary_generating = False
_summary_batch_size_in_flight = 0


class SummaryGenerationError(RuntimeError):
    """前情回顾生成失败。"""


def get_conversation_memory() -> ConversationBufferWindowMemory:
    """返回全局对话记忆实例。"""
    return _conversation_memory


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

    _on_record_appended()
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
    """返回窗口内最近的记录（最多 _MEMORY_WINDOW_SIZE 条）。"""
    with _conversation_lock:
        return [message.content for message in _conversation_memory.chat_memory.messages]


def get_conversation_summary() -> str:
    """返回当前前情回顾。"""
    with _conversation_lock:
        return _conversation_summary


def clear_conversation_memory() -> None:
    """清空对话历史，并重置摘要计数器。"""
    with _conversation_lock:
        _conversation_memory.chat_memory.clear()
        global _summary_counter, _summary_batch_size_in_flight
        _summary_counter = 0
        _summary_batch_size_in_flight = 0


def clear_conversation_summary() -> None:
    """清空前情回顾，并重置摘要计数器。"""
    with _conversation_lock:
        global _conversation_summary, _summary_counter, _summary_batch_size_in_flight
        _conversation_summary = ""
        _summary_counter = 0
        _summary_batch_size_in_flight = 0


def _on_record_appended() -> None:
    global _summary_counter
    with _conversation_lock:
        _summary_counter += 1
    _trigger_summary_generation_if_needed()


def _trigger_summary_generation_if_needed() -> None:
    global _is_summary_generating, _summary_counter, _summary_batch_size_in_flight
    with _conversation_lock:
        if _summary_counter < _MEMORY_WINDOW_SIZE:
            return
        if _is_summary_generating:
            return

        current_summary = _conversation_summary
        recent_records = [
            message.content for message in _conversation_memory.chat_memory.messages
        ]
        _summary_counter -= _MEMORY_WINDOW_SIZE
        _summary_batch_size_in_flight = _MEMORY_WINDOW_SIZE
        _is_summary_generating = True

    worker = Thread(
        target=_generate_summary_task,
        args=(current_summary, recent_records),
        daemon=True,
    )
    worker.start()


def _generate_summary_task(previous_summary: str, recent_records: list[str]) -> None:
    global _conversation_summary, _summary_counter, _is_summary_generating
    global _summary_batch_size_in_flight
    try:
        generated_summary = _call_summary_api(previous_summary, recent_records)
    except SummaryGenerationError as exc:
        with _conversation_lock:
            print(f"生成前情回顾失败，保留旧摘要并等待下次重试: {exc}")
            _summary_counter += _summary_batch_size_in_flight
            _summary_batch_size_in_flight = 0
            _is_summary_generating = False
        return

    with _conversation_lock:
        _conversation_summary = generated_summary
        _summary_batch_size_in_flight = 0
        _is_summary_generating = False

    _trigger_summary_generation_if_needed()


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
