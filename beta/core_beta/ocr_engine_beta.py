from __future__ import annotations

import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TypedDict

import cv2
import numpy as np
import paddle
from paddleocr import PaddleOCR
from PIL import Image, ImageOps

from beta.memory_beta.window_selection_state import BetaOcrResult, ParsedGameWindowInfo


class OcrTextBlock(TypedDict):
    text: str
    x: float
    y: float
    width: float
    height: float


_ocr_engine: PaddleOCR | None = None
_ocr_lock = RLock()
_ocr_device = "cpu"


def _resolve_ocr_device() -> str:
    if paddle.device.is_compiled_with_cuda():
        return "gpu:0"
    return "cpu"


def get_ocr_engine() -> PaddleOCR:
    global _ocr_engine, _ocr_device
    with _ocr_lock:
        if _ocr_engine is None:
            _ocr_device = _resolve_ocr_device()
            _ocr_engine = PaddleOCR(
                lang="en",
                device=_ocr_device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                det_db_unclip_ratio=1.0,
                det_db_box_thresh=0.8,
                det_db_thresh=0.5,
            )
        return _ocr_engine


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_text_box(page_result: dict, text_index: int) -> object | None:
    for key in ("rec_polys", "rec_boxes", "dt_polys", "dt_boxes"):
        text_boxes = page_result.get(key)
        if isinstance(text_boxes, list) and text_index < len(text_boxes):
            return text_boxes[text_index]
    return None


def _extract_box_metrics(text_box: object) -> tuple[float, float, float, float] | None:
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


def _preprocess_image(image_path: str | Path) -> Path:
    with Image.open(image_path) as image:
        grayscale_image = ImageOps.grayscale(image)
        grayscale_array = np.array(grayscale_image)
        denoised_array = cv2.bilateralFilter(grayscale_array, d=7, sigmaColor=35, sigmaSpace=35)
        denoised_image = Image.fromarray(denoised_array)
        with NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
            processed_path = Path(temporary_file.name)
        denoised_image.save(processed_path)
    return processed_path


def recognize_texts(image_path: str | Path) -> list[OcrTextBlock]:
    processed_image_path = _preprocess_image(image_path)
    try:
        with _ocr_lock:
            ocr_result = get_ocr_engine().predict(str(processed_image_path))
    finally:
        if processed_image_path.exists():
            processed_image_path.unlink()

    if not isinstance(ocr_result, list):
        return []

    blocks: list[OcrTextBlock] = []
    for page_result in ocr_result:
        if not isinstance(page_result, dict):
            continue
        rec_texts = page_result.get("rec_texts")
        if not isinstance(rec_texts, list):
            continue
        for text_index, text in enumerate(rec_texts):
            if not isinstance(text, str):
                continue
            normalized_text = text.strip()
            if not normalized_text:
                continue
            text_box = _extract_text_box(page_result, text_index)
            metrics = _extract_box_metrics(text_box) if text_box is not None else None
            if metrics is None:
                x_value = math.inf
                y_value = math.inf
                width_value = math.inf
                height_value = math.inf
            else:
                x_value, y_value, width_value, height_value = metrics
            blocks.append(
                {
                    "text": normalized_text,
                    "x": float(x_value),
                    "y": float(y_value),
                    "width": float(width_value),
                    "height": float(height_value),
                }
            )

    blocks.sort(key=lambda block: (block["y"], block["x"]))
    return blocks


def _join_dialog_lines(text_lines: list[str]) -> str:
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


def recognize_window_dialog(
    image_path: str | Path,
    parsed_window_info: ParsedGameWindowInfo,
) -> BetaOcrResult:
    source_path = Path(image_path)
    if not source_path.exists():
        raise RuntimeError(f"截图文件不存在: {source_path}")

    with Image.open(source_path) as image:
        width = int(image.width)
        height = int(image.height)
        if width <= 0 or height <= 0:
            raise RuntimeError("截图尺寸异常，无法识别")

        crop_top = int(height * parsed_window_info.top_bar_vertical_ratio)
        crop_top = max(0, min(crop_top, height - 1))
        content_image = image.crop((0, crop_top, width, height))

        with NamedTemporaryFile(suffix=".jpg", delete=False) as temporary_file:
            content_path = Path(temporary_file.name)
        content_image.save(content_path, format="JPEG", quality=95)

    try:
        text_blocks = recognize_texts(content_path)
    finally:
        if content_path.exists():
            content_path.unlink()

    content_height = max(1.0, float(height - crop_top))
    dialog_x1 = parsed_window_info.dialog_box_x1 * float(width)
    dialog_x2 = parsed_window_info.dialog_box_x2 * float(width)
    dialog_y1_raw = parsed_window_info.dialog_box_y1 * float(height)
    dialog_y2_raw = parsed_window_info.dialog_box_y2 * float(height)
    dialog_y1 = max(0.0, min(content_height, dialog_y1_raw - float(crop_top)))
    dialog_y2 = max(0.0, min(content_height, dialog_y2_raw - float(crop_top)))
    dialog_top_band_bottom = dialog_y1 + (dialog_y2 - dialog_y1) * 0.2

    non_dialog_lines: list[str] = []
    name_lines: list[str] = []
    dialog_lines: list[str] = []

    for block in text_blocks:
        x_value = block["x"]
        y_value = block["y"]
        width_value = block["width"]
        height_value = block["height"]
        if not all(math.isfinite(v) for v in (x_value, y_value, width_value, height_value)):
            non_dialog_lines.append(block["text"])
            continue

        center_x = x_value + width_value / 2.0
        center_y = y_value + height_value / 2.0
        inside_dialog_box = (
            dialog_x1 <= center_x <= dialog_x2 and dialog_y1 <= center_y <= dialog_y2
        )
        if not inside_dialog_box:
            non_dialog_lines.append(block["text"])
            continue

        if center_y <= dialog_top_band_bottom:
            name_lines.append(block["text"])
        else:
            dialog_lines.append(block["text"])

    return BetaOcrResult(
        non_dialog_text=_join_dialog_lines(non_dialog_lines),
        name=_join_dialog_lines(name_lines),
        dialog=_join_dialog_lines(dialog_lines),
    )
