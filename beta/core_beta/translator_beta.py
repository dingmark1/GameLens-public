from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
import json
from pathlib import Path

import requests

from beta.memory_beta.window_selection_state import (
    BetaOcrResult,
    BetaTranslationResult,
    get_conversation_history,
)


_TRANSLATE_API_URL = "https://api.deepseek.com/chat/completions"
_TRANSLATE_MODEL = "deepseek-chat"
_REQUEST_TIMEOUT_SECONDS = 60
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"
_CONFIG_SECTION = "app"

_SYSTEM_PROMPT = (
    "你是游戏文本翻译助手。"
    "请把输入 JSON 里的文本翻译成中文，保持字段结构不变。"
    "必须只输出 JSON 对象，字段为 name、dialog、additional_text、addition。"
    "addition 字段保持对象结构，若输入为空对象则输出空对象。"
)


class BetaTranslationError(RuntimeError):
    """beta 翻译失败。"""


def translate_ocr_result(ocr_result: BetaOcrResult) -> BetaTranslationResult:
    api_key = _load_deepseek_api_key()
    history_records = get_conversation_history()
    user_payload = {
        "name": ocr_result.name,
        "dialog": ocr_result.dialog,
        "additional_text": ocr_result.non_dialog_text,
        "addition": {"history": history_records},
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _TRANSLATE_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
        "thinking": {"type": "disabled"},
    }

    print("========== 发送给 DeepSeek 的数据 ==========")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    try:
        response = requests.post(
            _TRANSLATE_API_URL,
            headers=headers,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise BetaTranslationError(f"请求 DeepSeek 翻译接口失败: {exc}") from exc

    if response.status_code != 200:
        raise BetaTranslationError(
            f"DeepSeek 翻译接口返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BetaTranslationError(
            f"DeepSeek 翻译响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise BetaTranslationError(f"DeepSeek 翻译内容类型错误: {content!r}")

    print("========== DeepSeek 原始返回 ==========")
    print(content)

    return _parse_translation_content(content)


def _parse_translation_content(content: str) -> BetaTranslationResult:
    text = content.strip()
    if not text:
        raise BetaTranslationError("DeepSeek 翻译返回空内容")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BetaTranslationError(f"DeepSeek 翻译返回非 JSON: {content}") from exc

    if not isinstance(payload, dict):
        raise BetaTranslationError("DeepSeek 翻译返回必须是 JSON 对象")

    name = _normalize_text(payload.get("name", ""))
    dialog = _normalize_text(payload.get("dialog", ""))
    additional_text = _normalize_text(payload.get("additional_text", ""))
    addition_value = payload.get("addition", {})
    if not isinstance(addition_value, dict):
        raise BetaTranslationError("DeepSeek 翻译返回的 addition 字段必须是对象")

    return BetaTranslationResult(
        name=name,
        dialog=dialog,
        additional_text=additional_text,
        addition=addition_value,
    )


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _load_deepseek_api_key() -> str:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise BetaTranslationError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise BetaTranslationError(f"配置文件格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise BetaTranslationError(f"配置文件中缺少 [{_CONFIG_SECTION}] 配置段")

    api_key = parser.get(_CONFIG_SECTION, "deepseek_api_key", fallback="").strip()
    if not api_key:
        raise BetaTranslationError("配置中的 deepseek_api_key 不能为空")
    return api_key
