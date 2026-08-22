from __future__ import annotations

import json
from typing import Any

import requests

from core.app_config import DEEPSEEK_API_KEY


_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
_DEEPSEEK_MODEL = "deepseek-chat"
_REQUEST_TIMEOUT_SECONDS = 60

_SYSTEM_PROMPT = """
你是严谨的游戏资料检索与简介撰写助手。

收到游戏名称后，请检索并综合你掌握的公开资料，先确认游戏的准确身份，避免与同名作品混淆，再生成一段中文游戏简介。
优先通过谷歌检索外文资料，以及官方资料，如steam商店页面的简介、游戏官方网站、发行商或开发商的官方资料、知名游戏媒体的报道等。
简介要求：
1. 控制在 300 至 500 个中文字符左右。
2. 主要涵盖游戏类型、世界观或故事前提。浓缩得到前情提要或者背景设定。
3. 使用客观、连贯、易读的表述，不写广告语，不罗列条目，不包含明显剧透。
4. 不得编造无法确认的细节；资料不足或存在同名歧义时，应在简介中明确说明。
5. 只返回 JSON 对象，不要输出解释或 Markdown 代码块，格式必须为：
{"game_intro": "..."}
""".strip()


class GameIntroGenerationError(RuntimeError):
    """游戏简介生成失败。"""


def generate_game_intro(game_name: str) -> str:
    normalized_name = game_name.strip()
    if not normalized_name:
        raise ValueError("游戏名称不能为空")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"game_name": normalized_name},
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    try:
        response = requests.post(
            _DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GameIntroGenerationError(f"请求 DeepSeek 游戏简介接口失败: {exc}") from exc

    if response.status_code != 200:
        raise GameIntroGenerationError(
            f"DeepSeek 游戏简介接口返回错误 "
            f"(HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GameIntroGenerationError(
            f"DeepSeek 游戏简介响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise GameIntroGenerationError(f"DeepSeek 游戏简介内容类型错误: {content!r}")

    response_payload = _parse_json_content(content)
    game_intro = response_payload.get("game_intro", "")
    if not isinstance(game_intro, str):
        game_intro = str(game_intro)
    normalized_intro = game_intro.strip()
    if not normalized_intro:
        raise GameIntroGenerationError("DeepSeek 返回的游戏简介为空")

    return normalized_intro


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise GameIntroGenerationError("DeepSeek 返回了空内容")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GameIntroGenerationError(
            f"DeepSeek 返回的游戏简介不是有效 JSON: {content}"
        ) from exc

    if not isinstance(parsed, dict):
        raise GameIntroGenerationError("DeepSeek 返回的游戏简介必须是 JSON 对象")
    return parsed
