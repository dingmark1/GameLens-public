from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TypedDict

from paddleocr import PaddleOCR
from PIL import Image, ImageOps


# OCR 引擎模块用于统一封装 PaddleOCR 的生命周期与识别流程。
# 设计目标是：
# 1. 只创建一个全局 OCR 引擎实例，避免频繁初始化带来的性能损耗；
# 2. 对多线程访问加锁，确保 UI 或后台线程不会同时修改同一个引擎对象；
# 3. 识别函数统一提取有效文本，并返回干净的字符串列表供上层业务使用。

_ocr_engine: PaddleOCR | None = None
_current_lang = "en"
# RLock 用于保护全局引擎实例与识别过程，确保多线程下不会出现竞争条件。
_ocr_lock = RLock()


class OcrTextBlock(TypedDict):
    # 单条 OCR 结果同时保留文字与纵向位置，供上层判断“人名行”还是“对白行”。
    text: str
    y: float
    y_ratio: float


class OcrDialogResult(TypedDict):
    # 结构化输出直接面向翻译阶段，name 与 dialog 分离，addition 预留扩展字段。
    name: str | None
    dialog: list[str]
    addition: dict[str, object]


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


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    return None


def _extract_text_box(page_result: dict, text_index: int) -> object | None:
    # PaddleOCR 不同版本的返回键名可能略有差异，这里按常见字段顺序兼容读取。
    for key in ("rec_polys", "rec_boxes", "dt_polys", "dt_boxes"):
        text_boxes = page_result.get(key)
        if isinstance(text_boxes, list) and text_index < len(text_boxes):
            return text_boxes[text_index]

    return None


def _extract_top_left(text_box: object) -> tuple[float, float] | None:
    # 统一把多边形/矩形框转换为左上角坐标，便于按从上到下的顺序排序。
    if hasattr(text_box, "tolist"):
        text_box = text_box.tolist()

    if not isinstance(text_box, (list, tuple)):
        return None

    points: list[tuple[float, float]] = []
    for point in text_box:
        if hasattr(point, "tolist"):
            point = point.tolist()

        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue

        x_value = _to_float(point[0])
        y_value = _to_float(point[1])
        if x_value is None or y_value is None:
            continue

        points.append((x_value, y_value))

    if not points:
        return None

    return min(point[0] for point in points), min(point[1] for point in points)


def _join_dialog_lines(text_lines: list[str]) -> str:
    """将 OCR 逐行结果拼成一句话。"""

    if not text_lines:
        return ""

    merged_text = text_lines[0].strip()
    for line in text_lines[1:]:
        current_text = line.strip()
        if not current_text:
            continue

        if merged_text:
            previous_char = merged_text[-1]
            first_char = current_text[0]
            if (
                previous_char.isascii()
                and previous_char.isalnum()
                and first_char.isascii()
                and first_char.isalnum()
            ):
                merged_text += " "

        merged_text += current_text

    return merged_text


def _preprocess_image(image_path: str | Path) -> Path:
    """对输入图像做 OCR 前预处理。"""

    input_path = Path(image_path)
    with Image.open(input_path) as image:
        grayscale_image = ImageOps.grayscale(image)
        equalized_image = ImageOps.equalize(grayscale_image)

        with NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
            processed_path = Path(temporary_file.name)

        equalized_image.save(processed_path)

    return processed_path


def recognize_texts(image_path: str | Path) -> list[OcrTextBlock]:
    """对指定图像执行 OCR，并返回带坐标信息的文本块列表。

    返回值约定为：
    - 每个元素包含清理后的文本段及其位置信息；
    - 过滤掉空字符串和非字符串类型；
    - 若 OCR 结果中某页没有文字，则跳过该页。
    """
    processed_image_path = _preprocess_image(image_path)

    try:
        with Image.open(processed_image_path) as image:
            image_height = float(image.height)
        if image_height <= 0:
            image_height = 1.0

        with _ocr_lock:
            # 整个 predict 调用都在锁内进行，强制串行化，防止不同线程同时使用同一引擎实例。
            ocr_result = get_ocr_engine().predict(str(processed_image_path))

        recognized_texts: list[dict[str, object]] = []

        # PaddleOCR 的返回结果通常是一个列表，每个元素对应一页图像的识别信息。
        for page_result in ocr_result:
            if not isinstance(page_result, dict):
                continue

            rec_texts = page_result.get("rec_texts")
            if not isinstance(rec_texts, list):
                continue

            for text_index, text in enumerate(rec_texts):
                if not isinstance(text, str):
                    continue

                stripped_text = text.strip()
                if stripped_text:
                    text_box = _extract_text_box(page_result, text_index)
                    top_left = _extract_top_left(text_box) if text_box is not None else None
                    if top_left is None:
                        x_value = math.inf
                        y_value = math.inf
                        y_ratio = math.inf
                    else:
                        x_value, y_value = top_left
                        y_ratio = y_value / image_height

                    recognized_texts.append(
                        {
                            "text": stripped_text,
                            "x": x_value,
                            "y": y_value,
                            "y_ratio": y_ratio,
                        }
                    )
    finally:
        if processed_image_path.exists():
            processed_image_path.unlink()

    recognized_texts.sort(
        key=lambda block: (
            # 先按 y，再按 x，尽量还原屏幕文本的阅读顺序。
            block["y"],
            block["x"],
        )
    )

    return [
        {
            "text": str(block["text"]),
            "y": float(block["y"]),
            "y_ratio": float(block["y_ratio"]),
        }
        for block in recognized_texts
    ]


def format_dialog_result(text_blocks: list[OcrTextBlock]) -> OcrDialogResult:
    """根据 OCR 文本块生成结构化的人名/对话结果。"""

    if not text_blocks:
        return {"name": None, "dialog": [], "addition": {}}

    first_block = text_blocks[0]
    first_text = first_block["text"]

    half_colon_index = first_text.find(":")
    full_colon_index = first_text.find("：")

    colon_indices = [index for index in (half_colon_index, full_colon_index) if index >= 0]
    if colon_indices:
        # 同一行里已经出现“人名：对白”时，直接按冒号切分，减少对坐标的依赖。
        split_index = min(colon_indices)
        possible_name = first_text[:split_index].strip()
        possible_dialog = first_text[split_index + 1 :].strip()

        dialog_lines = [possible_dialog] if possible_dialog else []
        dialog_lines.extend(block["text"] for block in text_blocks[1:])
        dialog = [_join_dialog_lines(dialog_lines)] if dialog_lines else []

        return {
            "name": possible_name or None,
            "dialog": dialog,
            "addition": {},
        }

    first_y_ratio = first_block["y_ratio"]
    # 第一行不含冒号时，利用纵向位置判断其是否贴近区域顶部；贴近顶部通常是人名。
    has_top_name = math.isfinite(first_y_ratio) and first_y_ratio < 0.1

    if has_top_name:
        dialog_lines = [block["text"] for block in text_blocks[1:]]
        return {
            "name": first_text,
            "dialog": [_join_dialog_lines(dialog_lines)] if dialog_lines else [],
            "addition": {},
        }

    return {
        "name": None,
        "dialog": [_join_dialog_lines([block["text"] for block in text_blocks])],
        "addition": {},
    }