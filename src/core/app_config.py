from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"


@dataclass(frozen=True)
class AppConfig:
    deepseek_api_key: str
    enable_ocr_preprocess: bool
    top_proximity_threshold: float
    memory_window_size: int


def _parse_bool(value: str, key: str) -> bool:
    normalized_value = value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"config.txt 中 {key} 的值必须为布尔值")


def _parse_config() -> AppConfig:
    raw_values: dict[str, str] = {}

    try:
        config_text = _CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"读取配置文件失败: {_CONFIG_PATH}") from exc

    for line in config_text.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue

        if "=" not in stripped_line:
            raise ValueError(f"config.txt 配置行格式错误: {line}")

        key, value = stripped_line.split("=", 1)
        raw_values[key.strip()] = value.strip()

    deepseek_api_key = raw_values.get("DEEPSEEK_API_KEY", "")
    if not deepseek_api_key:
        raise ValueError("config.txt 中 DEEPSEEK_API_KEY 不能为空")

    enable_ocr_preprocess_raw = raw_values.get("ENABLE_OCR_PREPROCESS", "")
    if not enable_ocr_preprocess_raw:
        raise ValueError("config.txt 中 ENABLE_OCR_PREPROCESS 不能为空")

    top_proximity_threshold_raw = raw_values.get("TOP_PROXIMITY_THRESHOLD", "")
    if not top_proximity_threshold_raw:
        raise ValueError("config.txt 中 TOP_PROXIMITY_THRESHOLD 不能为空")

    memory_window_size_raw = raw_values.get("_MEMORY_WINDOW_SIZE", "")
    if not memory_window_size_raw:
        raise ValueError("config.txt 中 _MEMORY_WINDOW_SIZE 不能为空")

    return AppConfig(
        deepseek_api_key=deepseek_api_key,
        enable_ocr_preprocess=_parse_bool(
            enable_ocr_preprocess_raw, "ENABLE_OCR_PREPROCESS"
        ),
        top_proximity_threshold=float(top_proximity_threshold_raw),
        memory_window_size=int(memory_window_size_raw),
    )


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return _parse_config()


APP_CONFIG = get_app_config()
DEEPSEEK_API_KEY = APP_CONFIG.deepseek_api_key
ENABLE_OCR_PREPROCESS = APP_CONFIG.enable_ocr_preprocess
TOP_PROXIMITY_THRESHOLD = APP_CONFIG.top_proximity_threshold
_MEMORY_WINDOW_SIZE = APP_CONFIG.memory_window_size
