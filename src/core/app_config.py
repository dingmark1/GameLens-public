from __future__ import annotations

from dataclasses import dataclass
from configparser import ConfigParser, Error as ConfigParserError, SectionProxy
from functools import lru_cache
from pathlib import Path


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.txt"
_CONFIG_SECTION = "app"


@dataclass(frozen=True)
class AppConfig:
    deepseek_api_key: str
    enable_ocr_preprocess: bool
    top_proximity_threshold: float
    memory_window_size: int
    auto_recognition_interval_ms: int


def _parse_bool(value: str, key: str) -> bool:
    normalized_value = value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"config.txt 中 {key} 的值必须为布尔值")


def _load_config() -> ConfigParser:
    parser = ConfigParser(interpolation=None)
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
    except OSError as exc:
        raise RuntimeError(f"读取配置文件失败: {_CONFIG_PATH}") from exc
    except ConfigParserError as exc:
        raise ValueError(f"config.txt 格式错误: {_CONFIG_PATH}") from exc

    if not parser.has_section(_CONFIG_SECTION):
        raise ValueError(f"config.txt 中缺少 [{_CONFIG_SECTION}] 配置段")

    return parser


def _require_value(section: SectionProxy, key: str) -> str:
    value = section.get(key, fallback="").strip()
    if not value:
        raise ValueError(f"config.txt 中 {key.upper()} 不能为空")
    return value


def _parse_config() -> AppConfig:
    section = _load_config()[_CONFIG_SECTION]

    return AppConfig(
        deepseek_api_key=_require_value(section, "deepseek_api_key"),
        enable_ocr_preprocess=_parse_bool(
            _require_value(section, "enable_ocr_preprocess"), "ENABLE_OCR_PREPROCESS"
        ),
        top_proximity_threshold=float(_require_value(section, "top_proximity_threshold")),
        memory_window_size=int(_require_value(section, "memory_window_size")),
        auto_recognition_interval_ms=int(
            _require_value(section, "auto_recognition_interval_ms")
        ),
    )


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return _parse_config()


APP_CONFIG = get_app_config()
DEEPSEEK_API_KEY = APP_CONFIG.deepseek_api_key
ENABLE_OCR_PREPROCESS = APP_CONFIG.enable_ocr_preprocess
TOP_PROXIMITY_THRESHOLD = APP_CONFIG.top_proximity_threshold
_MEMORY_WINDOW_SIZE = APP_CONFIG.memory_window_size
AUTO_RECOGNITION_INTERVAL_MS = APP_CONFIG.auto_recognition_interval_ms
