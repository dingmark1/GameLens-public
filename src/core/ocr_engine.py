from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from paddleocr import PaddleOCR
from PIL import Image


# OCR 引擎模块用于统一封装 PaddleOCR 的生命周期与识别流程。
# 设计目标是：
# 1. 只创建一个全局 OCR 引擎实例，避免频繁初始化带来的性能损耗；
# 2. 对多线程访问加锁，确保 UI 或后台线程不会同时修改同一个引擎对象；
# 3. 识别函数统一提取有效文本，并返回干净的字符串列表供上层业务使用。

_ocr_engine: PaddleOCR | None = None
_current_lang = "en"
# RLock 用于保护全局引擎实例与识别过程，确保多线程下不会出现竞争条件。
_ocr_lock = RLock()


def set_ocr_language(lang_code: str) -> None:
    """设置 OCR 引擎语言，并在下次访问时重新初始化引擎。"""

    global _ocr_engine, _current_lang

    with _ocr_lock:
        _current_lang = lang_code
        _ocr_engine = None


def get_ocr_engine() -> PaddleOCR:
    """返回单例 OCR 引擎对象；首次调用时才初始化 PaddleOCR。"""

    global _ocr_engine

    with _ocr_lock:
        if _ocr_engine is None:
            # PaddleOCR 的语言模型需要在 CPU 上运行，且在本项目中不需要 MKL 相关加速。
            # 这样能优先保证兼容性，减少某些环境下因底层库不匹配带来的问题。
            _ocr_engine = PaddleOCR(
                lang=_current_lang,
                device="cpu",
                enable_mkldnn=False,
            )

        return _ocr_engine


def prewarm_ocr_engine() -> None:
    """预热 OCR 模型，减少第一次识别时的启动延迟。

    这一步会创建一个最小 32x32 的白底图像并执行一次预测，
    让 PaddleOCR 完成模型加载与内部初始化，从而让正式识别更快。
    """

    with _ocr_lock:
        ocr_engine = get_ocr_engine()
        # 使用 NamedTemporaryFile 生成临时 PNG 文件，避免额外写入项目目录。
        with NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
            temp_image_path = Path(temporary_file.name)

        try:
            # 生成纯白底小图，足以触发一次最小识别调用，且不会产生明显资源占用。
            Image.new("RGB", (32, 32), "white").save(temp_image_path)
            ocr_engine.predict(str(temp_image_path))
        finally:
            # 预测结束后删除临时文件，避免在磁盘上堆积无用缓存。
            if temp_image_path.exists():
                temp_image_path.unlink()


def recognize_texts(image_path: str | Path) -> list[str]:
    """对指定图像执行 OCR，并返回识别出的非空字符串列表。

    返回值约定为：
    - 每个元素都是清理后的文本段；
    - 过滤掉空字符串和非字符串类型；
    - 若 OCR 结果中某页没有文字，则跳过该页。
    """

    with _ocr_lock:
        # 整个 predict 调用都在锁内进行，强制串行化，防止不同线程同时使用同一引擎实例。
        ocr_result = get_ocr_engine().predict(str(image_path))
    recognized_texts: list[str] = []

    # PaddleOCR 的返回结果通常是一个列表，每个元素对应一页图像的识别信息。
    for page_result in ocr_result:
        if not isinstance(page_result, dict):
            continue

        rec_texts = page_result.get("rec_texts")
        if not isinstance(rec_texts, list):
            continue

        for text in rec_texts:
            if not isinstance(text, str):
                continue

            stripped_text = text.strip()
            if stripped_text:
                recognized_texts.append(stripped_text)

    return recognized_texts