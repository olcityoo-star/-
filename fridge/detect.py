from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from fridge.config import CATEGORY_BY_CLASS, COCO_NAMES, FOOD_LABELS_RU, MODEL_DIR, MODEL_PATH, MODEL_URLS

_SESSION = None
_SESSION_ERROR: str | None = None


def is_food_class(name: str) -> bool:
    return name in FOOD_LABELS_RU


def label_ru(name: str) -> str:
    return FOOD_LABELS_RU.get(name, name)


def download_model(path: Path = MODEL_PATH) -> Path:
    import httpx

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        return path

    last_error: Exception | None = None
    for url in MODEL_URLS:
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                if len(response.content) < 1_000_000:
                    raise RuntimeError("слишком маленький файл модели")
                path.write_bytes(response.content)
                return path
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"Не удалось скачать YOLO-модель: {last_error}") from last_error


def _load_session():
    global _SESSION, _SESSION_ERROR
    if _SESSION is not None:
        return _SESSION
    if _SESSION_ERROR:
        raise RuntimeError(_SESSION_ERROR)
    try:
        import onnxruntime as ort
    except ImportError as exc:
        _SESSION_ERROR = "onnxruntime не установлен. Выполните: pip install -r requirements.txt"
        raise RuntimeError(_SESSION_ERROR) from exc

    model_path = MODEL_PATH if MODEL_PATH.exists() else download_model()
    _SESSION = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    return _SESSION


def letterbox(image: Image.Image, size: int = 640) -> tuple[Image.Image, float, int, int]:
    width, height = image.size
    scale = min(size / width, size / height)
    new_w, new_h = int(round(width * scale)), int(round(height * scale))
    resized = image.resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))
    return canvas, scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float = 0.45) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def _xywh_to_xyxy(xywh: np.ndarray) -> np.ndarray:
    x, y, w, h = xywh.T
    return np.stack((x - w / 2, y - h / 2, x + w / 2, y + h / 2), axis=1)


def parse_predictions(output: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return boxes (xywh), scores, class ids from YOLOv8 or YOLOv5 ONNX output."""
    pred = output[0] if output.ndim == 3 else output
    # YOLOv8: (84, 8400) or (8400, 84)
    if pred.shape[0] in (84, 85) and pred.shape[1] > pred.shape[0]:
        pred = pred.T
    if pred.shape[1] == 84:
        boxes = pred[:, :4]
        class_scores = pred[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores.max(axis=1)
        return boxes, scores, class_ids
    if pred.shape[1] == 85:
        boxes = pred[:, :4]
        objectness = pred[:, 4]
        class_scores = pred[:, 5:]
        class_ids = class_scores.argmax(axis=1)
        scores = objectness * class_scores.max(axis=1)
        return boxes, scores, class_ids
    raise RuntimeError(f"Неизвестный формат YOLO-выхода: {pred.shape}")


def detect_image(
    image_bytes: bytes,
    confidence: float = 0.35,
    food_only: bool = True,
) -> list[dict[str, Any]]:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = image.size
    canvas, scale, pad_x, pad_y = letterbox(image)
    blob = np.asarray(canvas, dtype=np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))[None, ...]

    session = _load_session()
    inp = session.get_inputs()[0]
    input_name = inp.name
    if "float16" in (inp.type or ""):
        blob = blob.astype(np.float16)
    outputs = session.run(None, {input_name: blob})
    boxes, scores, class_ids = parse_predictions(np.array(outputs[0], dtype=np.float32))

    mask = scores >= confidence
    boxes, scores, class_ids = boxes[mask], scores[mask], class_ids[mask]
    if len(boxes) == 0:
        return []

    xyxy = _xywh_to_xyxy(boxes)
    keep = _nms(xyxy, scores)
    detections: list[dict[str, Any]] = []
    for index, i in enumerate(keep, start=1):
        class_id = int(class_ids[i])
        if class_id < 0 or class_id >= len(COCO_NAMES):
            continue
        name = COCO_NAMES[class_id]
        if food_only and not is_food_class(name):
            continue
        x1, y1, x2, y2 = xyxy[i]
        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale
        detections.append(
            {
                "id": index,
                "class_id": class_id,
                "class_name": name,
                "name": label_ru(name),
                "confidence": round(float(scores[i]), 3),
                "bbox": {
                    "x1": round(float(max(0, x1)), 1),
                    "y1": round(float(max(0, y1)), 1),
                    "x2": round(float(min(orig_w, x2)), 1),
                    "y2": round(float(min(orig_h, y2)), 1),
                },
                "accepted": True,
                "quantity": 1,
                "unit": "шт",
                "category": CATEGORY_BY_CLASS.get(name, "скан"),
                "expires_on": None,
            }
        )
    return detections


def detector_status() -> dict[str, Any]:
    ready = MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 1_000_000
    return {
        "model_path": str(MODEL_PATH),
        "model_ready": ready,
        "session_error": _SESSION_ERROR,
        "food_classes": list(FOOD_LABELS_RU.values()),
    }


def ensure_model() -> dict[str, Any]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = download_model()
    return {"model_path": str(path), "bytes": path.stat().st_size, "model_ready": True}
