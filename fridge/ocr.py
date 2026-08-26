from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from fridge.sync import parse_expiry, suggest_name_from_ocr

_OCR = None
_OCR_ERROR: str | None = None
_OCR_ENGINE: str | None = None


def ocr_status() -> dict[str, Any]:
    try:
        _load_ocr()
        return {"ready": True, "engine": _OCR_ENGINE, "message": "OCR готов"}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "engine": None, "message": str(exc)}


def _load_ocr():
    global _OCR, _OCR_ERROR, _OCR_ENGINE
    if _OCR is not None:
        return _OCR
    if _OCR_ERROR:
        raise RuntimeError(_OCR_ERROR)

    # New package works on Python 3.12–3.14. Legacy package kept as fallback.
    try:
        from rapidocr import RapidOCR

        _OCR = RapidOCR()
        _OCR_ENGINE = "rapidocr"
        return _OCR
    except Exception as primary:  # noqa: BLE001
        try:
            from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR

            _OCR = LegacyRapidOCR()
            _OCR_ENGINE = "rapidocr_onnxruntime"
            return _OCR
        except Exception:  # noqa: BLE001
            _OCR_ERROR = (
                "OCR не установлен. Выполните: pip install rapidocr "
                f"(детали: {primary})"
            )
            raise RuntimeError(_OCR_ERROR) from primary


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
    if crop.width < 180 or crop.height < 180:
        scale = max(180 / max(crop.width, 1), 180 / max(crop.height, 1))
        crop = crop.resize(
            (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
            Image.BICUBIC,
        )
    gray = ImageOps.grayscale(crop)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    return gray.convert("RGB")


def _lines_from_result(result: Any) -> list[str]:
    if result is None:
        return []
    # New rapidocr: RapidOCROutput(txts=...)
    txts = getattr(result, "txts", None)
    if txts:
        return [str(t).strip() for t in txts if str(t).strip()]
    # Legacy: (rows, elapse) or just rows
    rows = result[0] if isinstance(result, tuple) else result
    if not rows:
        return []
    lines: list[str] = []
    for row in rows:
        if row is None:
            continue
        if isinstance(row, (list, tuple)) and len(row) > 1:
            text = str(row[1]).strip()
            if text:
                lines.append(text)
    return lines


def read_text(image: Image.Image) -> str:
    engine = _load_ocr()
    import numpy as np

    result = engine(np.asarray(image))
    return "\n".join(_lines_from_result(result))


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
                generic = {
                    "бутылка",
                    "миска",
                    "стакан",
                    "бокал",
                    "банка / ваза",
                    "vase",
                    "bottle",
                    "bowl",
                    "cup",
                }
                if str(item.get("name") or "").lower() in generic and len(suggested) >= 4:
                    item["name"] = suggested
        except Exception as exc:  # noqa: BLE001
            item["ocr_error"] = str(exc)
        enriched.append(item)
    return enriched
