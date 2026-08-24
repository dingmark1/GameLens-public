from __future__ import annotations

import base64
from configparser import ConfigParser, Error as ConfigParserError
import json
from pathlib import Path
from typing import Any

import requests

from beta.memory_beta.window_selection_state import ParsedGameWindowInfo


_VISION_API_URL = "https://api.deepseek.com/chat/completions"
_VISION_MODEL = "deepseek-v4-flash-vision-exp"
_REQUEST_TIMEOUT_SECONDS = 60
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"
_CONFIG_SECTION = "app"

_VISION_PROMPT = (
    "你将收到一张 galgame 游戏窗口截图。"
    "请分析并严格输出 JSON 对象，字段必须完整："
    '{"game_name":"字符串","top_bar_vertical_ratio":0.0,"dialog_box":{"x1":0.0,"x2":0.0,"y1":0.0,"y2":0.0}}。'
    "含义："
    "game_name 为游戏名；"
    "top_bar_vertical_ratio 为顶部窗口栏在图片竖直方向占比（0~1）；"
    "dialog_box 为对话框在图片中的相对位置坐标（0~1）。"
    "不要输出任何额外文本。"
)


class WindowParseError(RuntimeError):
    """解析游戏窗口失败。"""


def parse_game_window_image(image_path: Path) -> ParsedGameWindowInfo:
    if not image_path.exists():
        raise WindowParseError(f"截图文件不存在: {image_path}")

    image_data = image_path.read_bytes()
    if not image_data:
        raise WindowParseError("截图文件为空，无法解析")
    encoded_image = base64.b64encode(image_data).decode("utf-8")

    api_key = _load_deepseek_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded_image}",
                            "detail": "original",
                        },
                    },
                ],
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    debug_payload = {
        "model": _VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,<len={len(encoded_image)}>",
                            "detail": "original",
                        },
                    },
                ],
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
        "image_path": str(image_path),
        "image_bytes": len(image_data),
    }
    print("========== 发送给 DeepSeek 的数据 ==========")
    print(json.dumps(debug_payload, ensure_ascii=False, indent=2))

    try:
        response = requests.post(
            _VISION_API_URL,
            headers=headers,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise WindowParseError(f"请求 DeepSeek 窗口解析接口失败: {exc}") from exc

    if response.status_code != 200:
        raise WindowParseError(
            f"DeepSeek 窗口解析接口返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise WindowParseError(
            f"DeepSeek 窗口解析响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise WindowParseError(f"DeepSeek 窗口解析内容类型错误: {content!r}")

    print("========== DeepSeek 原始返回 ==========")
    print(content)

    return _parse_content_json(content)


def _parse_content_json(content: str) -> ParsedGameWindowInfo:
    text = content.strip()
    if not text:
        raise WindowParseError("DeepSeek 窗口解析返回空内容")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WindowParseError(f"DeepSeek 窗口解析返回非 JSON: {content}") from exc

    if not isinstance(payload, dict):
        raise WindowParseError("DeepSeek 窗口解析返回必须是 JSON 对象")

    game_name_value = payload.get("game_name", "")
    if not isinstance(game_name_value, str):
        game_name_value = str(game_name_value)
    game_name = game_name_value.strip()
    if not game_name:
        raise WindowParseError("DeepSeek 窗口解析未返回有效 game_name")

    top_ratio = _to_ratio(payload.get("top_bar_vertical_ratio"), "top_bar_vertical_ratio")

    dialog_box = payload.get("dialog_box")
    if not isinstance(dialog_box, dict):
        raise WindowParseError("DeepSeek 窗口解析的 dialog_box 字段必须是对象")

    x1 = _to_ratio(dialog_box.get("x1"), "dialog_box.x1")
    x2 = _to_ratio(dialog_box.get("x2"), "dialog_box.x2")
    y1 = _to_ratio(dialog_box.get("y1"), "dialog_box.y1")
    y2 = _to_ratio(dialog_box.get("y2"), "dialog_box.y2")

    if x1 >= x2:
        raise WindowParseError("DeepSeek 窗口解析返回无效区间: x1 必须小于 x2")
    if y1 >= y2:
        raise WindowParseError("DeepSeek 窗口解析返回无效区间: y1 必须小于 y2")

    return ParsedGameWindowInfo(
        game_name=game_name,
        top_bar_vertical_ratio=top_ratio,
        dialog_box_x1=x1,
        dialog_box_x2=x2,
        dialog_box_y1=y1,
        dialog_box_y2=y2,
    )


def _to_ratio(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise WindowParseError(f"DeepSeek 窗口解析字段无效: {field_name}")
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise WindowParseError(f"DeepSeek 窗口解析字段无效: {field_name}") from exc
    if ratio < 0.0 or ratio > 1.0:
        raise WindowParseError(f"DeepSeek 窗口解析字段越界: {field_name}={ratio}")
    return ratio


def _load_deepseek_api_key() -> str:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise WindowParseError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise WindowParseError(f"配置文件格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise WindowParseError(f"配置文件中缺少 [{_CONFIG_SECTION}] 配置段")

    api_key = parser.get(_CONFIG_SECTION, "deepseek_api_key", fallback="").strip()
    if not api_key:
        raise WindowParseError("配置中的 deepseek_api_key 不能为空")
    return api_key
