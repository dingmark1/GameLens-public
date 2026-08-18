"""
DeepSeek 翻译模块

职责：
1. 接收 OCR 结构化结果（OcrDialogResult：name / dialog / addition）
2. 拼装系统提示词与用户数据，调用 DeepSeek Chat API
3. 解析并规范化返回，还原为同构字典
4. 当前阶段把请求/响应打印到控制台，便于验证链路

注意：
- API Key 暂时硬编码，仅用于本地测试
- addition 当前为空，但已在 prompt 与返回值中预留
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 只做类型标注用途；运行时避免导入 core.ocr_engine（避免触发 PaddleOCR 加载）。
if TYPE_CHECKING:
    from core.ocr_engine import OcrDialogResult

# 直接运行 `python src/core/translator.py` 时，把 src 目录加入 sys.path，
# 让 `from core.xxx import ...` 也能正常工作。
if __package__ in (None, ""):
    _SRC_DIR = Path(__file__).resolve().parent.parent
    if str(_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(_SRC_DIR))

import requests


# ============================================================
# 配置区（测试用硬编码，后续迁移到环境变量 / 配置文件）
# ============================================================

#  替换为真实 DeepSeek API Key
DEEPSEEK_API_KEY = "sk-5fa89a3596d543e48fd07445a467af14"

# DeepSeek 官方 OpenAI 兼容接口地址
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# 模型选择：deepseek-chat / deepseek-reasoner
DEEPSEEK_MODEL = "deepseek-chat"

# 默认翻译目标语言
DEFAULT_TARGET_LANG = "中文"

# 请求超时时间（秒）
REQUEST_TIMEOUT_SECONDS = 60


class TranslationError(RuntimeError):
    """DeepSeek 翻译过程中的统一异常类型。"""


# ============================================================
# Prompt 构建
# ============================================================

_SYSTEM_PROMPT_TEMPLATE = """
你是一个专业的游戏汉化翻译引擎。

我会发送一个 JSON 对象，包含以下字段：
- name：说话人名称（姓名、角色名或简称），需要翻译成 __TARGET_LANG__；如果是人名，请按目标语言习惯音译或意译。
- dialog：对白列表，每个元素是一句或一段角色对白，需要逐条翻译成 __TARGET_LANG__。
- addition：附加信息字典。包含若干项辅助信息。

你必须只返回一个 JSON 对象，字段与输入完全一致：
{"name": "...", "dialog": [...], "addition": {...}}

严格要求：
1. 只输出 JSON 本身，不要输出任何解释、前后缀或 markdown 代码块。
2. dialog 的条数和顺序必须与输入保持一致。
3. 翻译保持游戏对白风格，自然流畅，不添加原文没有的内容。
4. 若输入中某字段为空（如 name 为 null），输出中保持 null 或空字符串。
5. 提供的文本中可能出现笔误与非标准用法，请尽量理解原意并翻译，而不是逐字逐句直译，对于俚语更加不可直译。
6. 提供的文本中通常只包含一种语言，对于意义不明的语句，可以视作干扰，进行舍弃
7. 如果 addition 中包含 "history" 字段（它是一个列表，每条格式为"角色名：对话原文"）请参考这些历史对话来理解当前句子的语境，特别注意人称代词（我/你/他/她）的一致性。
"""



def _build_system_prompt(target_lang: str) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.replace("__TARGET_LANG__", target_lang)


def _build_user_prompt(result: dict[str, Any]) -> str:
    raw_name = result.get("name")
    raw_dialog = result.get("dialog")
    raw_addition = result.get("addition")

    name = raw_name if isinstance(raw_name, str) and raw_name.strip() else ""
    dialog = raw_dialog if isinstance(raw_dialog, list) else []
    addition = raw_addition if isinstance(raw_addition, dict) else {}

    payload = {
        "name": name,
        "dialog": dialog,
        "addition": addition,
    }

    return json.dumps(payload, ensure_ascii=False, indent=2)


def has_translatable_content(result: dict[str, Any]) -> bool:
    """判断 OCR 结果里是否存在可翻译内容。"""

    raw_name = result.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        return True

    raw_dialog = result.get("dialog")
    if not isinstance(raw_dialog, list):
        return False

    for line in raw_dialog:
        if line is None:
            continue
        if not isinstance(line, str):
            line = str(line)
        if line.strip():
            return True

    return False


# ============================================================
# DeepSeek API 调用
# ============================================================

def _call_deepseek_api(system_prompt: str, user_prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        # DeepSeek 支持 JSON 输出模式
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise TranslationError(f"请求 DeepSeek API 失败: {exc}") from exc

    if response.status_code != 200:
        raise TranslationError(
            f"DeepSeek API 返回错误 (HTTP {response.status_code}): {response.text}"
        )

    try:
        response_data = response.json()
        content = response_data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TranslationError(
            f"DeepSeek API 响应解析失败: {response.text}"
        ) from exc

    if not isinstance(content, str):
        raise TranslationError(f"DeepSeek API 返回的 content 不是字符串: {content!r}")

    return content


# ============================================================
# 返回结果解析与规范化
# ============================================================

def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise TranslationError("模型返回了空内容")

    # 首选：整体解析
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 容错：截取第一个 { 到最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 容错：剥离 markdown 代码块围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned_text = "\n".join(lines).strip()
        try:
            parsed = json.loads(cleaned_text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise TranslationError(f"模型返回的内容无法解析为 JSON 对象:\n{content}")


def _normalize_translation(
    parsed: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    """将模型返回的 dict 规范化为与 OcrDialogResult 一致的结构。

    某些字段缺失或类型错误时，回退到原文对应字段，保证上层不会拿到空/坏数据。
    """
    # 准备原文兜底值
    source_name = source.get("name")
    raw_source_dialog = source.get("dialog", [])
    if not isinstance(raw_source_dialog, list):
        raw_source_dialog = []
    source_dialog = list(raw_source_dialog)

    raw_source_addition = source.get("addition", {})
    if not isinstance(raw_source_addition, dict):
        raw_source_addition = {}
    source_addition = dict(raw_source_addition)

    # name
    name = parsed.get("name", source_name)
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = source_name if isinstance(source_name, str) and source_name.strip() else None

    # dialog
    dialog = parsed.get("dialog", source_dialog)
    if isinstance(dialog, list):
        normalized_lines: list[str] = []
        for line in dialog:
            if line is None:
                continue
            if not isinstance(line, str):
                line = str(line)
            line = line.strip()
            if line:
                normalized_lines.append(line)

        if normalized_lines:
            dialog = normalized_lines
        else:
            dialog = source_dialog
    else:
        dialog = source_dialog

    # addition
    addition = parsed.get("addition", source_addition)
    if not isinstance(addition, dict):
        addition = source_addition

    return {
        "name": name,
        "dialog": dialog,
        "addition": addition,
    }


# ============================================================
# 对外主入口
# ============================================================

def translate_dialog_result(
    result: OcrDialogResult,
    target_lang: str = DEFAULT_TARGET_LANG,
) -> OcrDialogResult:
    """将 OCR 结构化结果交给 DeepSeek 翻译，返回同构字典。"""
    if not isinstance(result, dict):
        raise TypeError("translate_dialog_result 需要一个 dict（OcrDialogResult）")

    if not has_translatable_content(result):
        raw_addition = result.get("addition", {})
        if not isinstance(raw_addition, dict):
            raw_addition = {}

        print("OCR 结果为空，跳过翻译")
        return {
            "name": None,
            "dialog": [],
            "addition": dict(raw_addition),
        }

    system_prompt = _build_system_prompt(target_lang)
    user_prompt = _build_user_prompt(result)

    print("========== 发送给 DeepSeek 的数据 ==========")
    print(user_prompt)
    print()

    content = _call_deepseek_api(system_prompt, user_prompt)

    print("========== DeepSeek 原始返回 ==========")
    print(content)
    print()

    parsed = _parse_json_content(content)
    translated = _normalize_translation(parsed, result)
    return translated


# ============================================================
# 测试入口
# ============================================================

def main() -> None:
    # 模拟一份 OCR 结构化结果，结构来自 core/ocr_engine.py 的 OcrDialogResult。
    # addition 当前实际为空，这里放了两个示例值用于验证"附加信息"的透传与翻译。
    sample_result = {
        "name": "Alice",
        "dialog": [
            "Where are you going?",
            "To the castle.",
            "I'll come with you.",
        ],
        "addition": {
            "emotion": "happy",
            "sound_effect": "footsteps",
        },
    }

    print("========== 原文（发送前） ==========")
    print(json.dumps(sample_result, ensure_ascii=False, indent=2))
    print()

    try:
        translated_result = translate_dialog_result(sample_result)
    except TranslationError as exc:
        print(f"\n[翻译失败] {exc}")
        return

    print("========== 翻译结果（规范化后） ==========")
    print(json.dumps(translated_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
