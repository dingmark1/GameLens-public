from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TypedDict

import cv2
import paddle
from paddleocr import PaddleOCR
import numpy as np
from PIL import Image, ImageOps

from core.app_config import ENABLE_OCR_PREPROCESS, TOP_PROXIMITY_THRESHOLD

# PaddleOCR 识别封装模块。
# 这里统一管理引擎初始化、图像预处理、切片识别和结果整理，避免上层业务直接接触底层细节。
# 核心原则：
# 1. 全局单例 OCR 引擎，减少重复初始化开销；
# 2. 识别过程加锁，避免多线程同时操作同一个引擎；
# 3. 预处理只做轻量增强，避免过度改变截图内容。

_ocr_engine: PaddleOCR | None = None
_current_lang = "en"
_ocr_device = "cpu"
_prewarm_device_logged = False
ENABLE_OCR_SLICE = True  # 是否启用大图切片识别，默认开启以改善稀疏小字检测效果。
OCR_SLICE_MIN_LONG_EDGE = 1000  # 长边不足该值时跳过切片，减少不必要的额外开销。
OCR_SLICE_MIN_SHORT_EDGE = 400  # 短边不足该值时跳过切片，避免在小图上过度分片。
# RLock 用于保护全局引擎实例与识别过程，确保多线程下不会出现竞争条件。
_ocr_lock = RLock()


class OcrTextBlock(TypedDict):
    # 单条 OCR 结果同时保留文字与纵向位置，供上层判断“人名行”还是“对白行”。
    text: str
    x: float
    y: float
    width: float
    height: float
    y_ratio: float


class OcrDialogResult(TypedDict):
    # 结构化输出直接面向翻译阶段，name 与 dialog 分离，addition 预留扩展字段。
    name: str | None
    dialog: list[str]
    addition: dict[str, object]


def set_ocr_language(lang_code: str) -> None:
    """设置 OCR 引擎语言，并在下次访问时重新初始化引擎。"""

    global _ocr_engine, _current_lang, _prewarm_device_logged

    with _ocr_lock:
        _current_lang = lang_code
        _ocr_engine = None
        _prewarm_device_logged = False


def _resolve_ocr_device() -> str:
    """根据当前环境选择 OCR 设备。"""

    if paddle.device.is_compiled_with_cuda():
        return "gpu:0"

    return "cpu"


def get_ocr_engine() -> PaddleOCR:
    """返回单例 OCR 引擎对象；首次调用时才初始化 PaddleOCR。"""

    global _ocr_engine, _ocr_device

    with _ocr_lock:
        if _ocr_engine is None:
            _ocr_device = _resolve_ocr_device()
            _ocr_engine = PaddleOCR(
                lang=_current_lang,
                device=_ocr_device,
                use_textline_orientation=False,  # 替代 use_angle_cls
                det_db_unclip_ratio=1.0,
                det_db_box_thresh=0.3,
                det_db_thresh=0.3,
            )

        return _ocr_engine


def prewarm_ocr_engine() -> None:
    """预热 OCR 模型，减少第一次识别时的启动延迟。

    这一步会创建一个最小 32x32 的白底图像并执行一次预测，
    让 PaddleOCR 完成模型加载与内部初始化，从而让正式识别更快。
    """

    global _prewarm_device_logged

    with _ocr_lock:
        ocr_engine = get_ocr_engine()
        if not _prewarm_device_logged:
            print(f"[OCR] 当前使用设备: {_ocr_device}")
            _prewarm_device_logged = True
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


def _extract_box_metrics(text_box: object) -> tuple[float, float, float, float] | None:
    """提取文本框的左上角和宽高。"""

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

    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    return min_x, min_y, max_x - min_x, max_y - min_y


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
    """对输入图像做 OCR 前预处理。

    这里只保留灰度化和双边滤波，不再做直方图均衡化，避免过度拉伸截图中的噪点。
    """

    input_path = Path(image_path)
    if not ENABLE_OCR_PREPROCESS:
        return input_path

    with Image.open(input_path) as image:
        # 先转灰度，减少颜色信息对 OCR 的干扰。
        grayscale_image = ImageOps.grayscale(image)
        # 再做轻度去噪，保留笔画边缘。
        grayscale_array = np.array(grayscale_image)
        denoised_array = cv2.bilateralFilter(grayscale_array, d=9, sigmaColor=75, sigmaSpace=75)
        denoised_image = Image.fromarray(denoised_array)

        with NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
            processed_path = Path(temporary_file.name)

        denoised_image.save(processed_path)

    return processed_path


def _build_slice_config(image_width: int, image_height: int) -> dict[str, int] | None:
    """按图像尺寸动态生成 PaddleOCR 的 slice 参数；小图返回 None。"""

    if not ENABLE_OCR_SLICE:
        return None

    long_edge = max(image_width, image_height)
    short_edge = min(image_width, image_height)
    if long_edge < OCR_SLICE_MIN_LONG_EDGE or short_edge < OCR_SLICE_MIN_SHORT_EDGE:
        return None

    # 步长设为 1/2 到 2/3 的切片大小，保证 1/3 到 1/2 的重叠
    horizontal_stride = min(800, max(300, image_width // 3))
    vertical_stride = min(600, max(200, image_height // 3))
    # 固定阈值更稳定，对截屏场景 30-50px 通常合适，或者自适应
    merge_x_thres = 40
    merge_y_thres = 40
    # merge_x_thres = min(64, max(16, horizontal_stride // 30))
    # merge_y_thres = min(64, max(16, vertical_stride // 30))
    

    return {
        "horizontal_stride": horizontal_stride,
        "vertical_stride": vertical_stride,
        "merge_x_thres": merge_x_thres,
        "merge_y_thres": merge_y_thres,
    }


def recognize_texts(image_path: str | Path) -> list[OcrTextBlock]:
    """对指定图像执行 OCR，并返回带坐标信息的文本块列表。

    返回值约定为：
    - 每个元素包含清理后的文本段及其位置信息；
    - 过滤掉空字符串和非字符串类型；
    - 若 OCR 结果中某页没有文字，则跳过该页。
    """
    processed_image_path = _preprocess_image(image_path)
    should_cleanup = ENABLE_OCR_PREPROCESS

    try:
        with Image.open(processed_image_path) as image:
            image_width = int(image.width)
            image_height = float(image.height)
        if image_height <= 0:
            image_height = 1.0
        if image_width <= 0:
            image_width = 1

        slice_config = _build_slice_config(image_width=image_width, image_height=int(image_height))

        with _ocr_lock:
            # 整个 predict 调用都在锁内进行，强制串行化，防止不同线程同时使用同一引擎实例。
            ocr_engine = get_ocr_engine()
            if slice_config is None:
                ocr_result = ocr_engine.predict(str(processed_image_path))
            else:
                ocr_result = ocr_engine.predict(str(processed_image_path), slice=slice_config)

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
                    metrics = _extract_box_metrics(text_box) if text_box is not None else None
                    if metrics is None:
                        x_value = math.inf
                        y_value = math.inf
                        width_value = math.inf
                        height_value = math.inf
                        y_ratio = math.inf
                    else:
                        x_value, y_value, width_value, height_value = metrics
                        y_ratio = y_value / image_height

                    recognized_texts.append(
                        {
                            "text": stripped_text,
                            "x": x_value,
                            "y": y_value,
                            "width": width_value,
                            "height": height_value,
                            "y_ratio": y_ratio,
                        }
                    )
    finally:
        if should_cleanup and processed_image_path.exists():
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
            "x": float(block["x"]),
            "y": float(block["y"]),
            "width": float(block["width"]),
            "height": float(block["height"]),
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

    first_y_ratio = first_block["y_ratio"]
    # 第一行不含冒号时，利用纵向位置判断其是否贴近区域顶部；贴近顶部通常是人名。
    has_top_name = math.isfinite(first_y_ratio) and first_y_ratio < TOP_PROXIMITY_THRESHOLD

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