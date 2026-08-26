from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from fridge.sync import parse_expiry, suggest_name_from_ocr

_OCR = None
_OCR_ERROR: str | None = None


def ocr_status() -> dict[str, Any]:
    engine = None
    try:
        _load_ocr()
        engine = "rapidocr"
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "engine": None, "message": str(exc)}
    return {"ready": True, "engine": engine, "message": "OCR готов"}


def _load_ocr():
    global _OCR, _OCR_ERROR
    if _OCR is not None:
        return _OCR
    if _OCR_ERROR:
        raise RuntimeError(_OCR_ERROR)
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        _OCR_ERROR = (
            "OCR не установлен. Выполните: pip install rapidocr-onnxruntime"
        )
        raise RuntimeError(_OCR_ERROR) from exc
    _OCR = RapidOCR()
    return _OCR


def _crop(image: Image.Image, bbox: dict[str, Any], pad: float = 0.08) -> Image.Image:
    width, height = image.size
    x1 = float(bbox.get("x1") or 0)
    y1 = float(bbox.get("y1") or 0)
    x2 = float(bbox.get("x2") or width)
    y2 = float(bbox.get("y2") or height)
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    x1 = max(0, int(x1 - bw * pad))
    y1 = max(0, int(y1 - bh * pad))
    x2 = min(width, int(x2 + bw * pad))
    y2 = min(height, int(y2 + bh * pad))
    crop = image.crop((x1, y1, x2, y2))
    # Upscale tiny fridge labels for OCR
    if crop.width < 180 or crop.height < 180:
        scale = max(180 / max(crop.width, 1), 180 / max(crop.height, 1))
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.BICUBIC)
    gray = ImageOps.grayscale(crop)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    return gray.convert("RGB")


def read_text(image: Image.Image) -> str:
    engine = _load_ocr()
    import numpy as np

    result, _ = engine(np.asarray(image))
    if not result:
        return ""
    lines = [str(row[1]).strip() for row in result if row and len(row) > 1 and str(row[1]).strip()]
    return "\n".join(lines)


def enrich_detections(image_bytes: bytes, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add OCR text / expiry / better name hints to YOLO boxes."""
    if not detections:
        return detections
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    enriched: list[dict[str, Any]] = []
    ocr_available = True
    try:
        _load_ocr()
    except Exception:  # noqa: BLE001
        ocr_available = False

    for det in detections:
        item = dict(det)
        item.setdefault("ocr_text", "")
        item.setdefault("ocr_error", None)
        bbox = item.get("bbox") or {}
        if not ocr_available:
            item["ocr_error"] = _OCR_ERROR or "OCR недоступен"
            enriched.append(item)
            continue
        try:
            crop = _crop(image, bbox)
            text = read_text(crop)
            item["ocr_text"] = text
            expiry = parse_expiry(text)
            if expiry and not item.get("expires_on"):
                item["expires_on"] = expiry
            suggested = suggest_name_from_ocr(text, item.get("name") or "")
            if suggested and suggested != item.get("name"):
                item["ocr_name"] = suggested
                # Auto-upgrade generic YOLO labels when OCR found a better label.
                generic = {"бутылка", "миска", "стакан", "бокал", "банка / ваза", "vase", "bottle", "bowl", "cup"}
                if str(item.get("name") or "").lower() in generic and len(suggested) >= 4:
                    item["name"] = suggested
        except Exception as exc:  # noqa: BLE001
            item["ocr_error"] = str(exc)
        enriched.append(item)
    return enriched
