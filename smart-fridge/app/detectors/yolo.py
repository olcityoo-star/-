"""Локальное распознавание через YOLO (ultralytics).

Работает офлайн на самом Raspberry Pi / домашнем сервере. Готовая модель
``yolov8n.pt`` обучена на COCO и из продуктов знает немного: банан, яблоко,
апельсин, брокколи, морковь, сэндвич, пиццу, пончик, торт, хот-дог, бутылку,
миску. Для реального холодильника имеет смысл дообучить модель на своих
фотографиях и указать путь к ней в ``DETECTOR_MODEL``.
"""

from __future__ import annotations

import io
import threading

from .. import products
from .base import Detection, DetectorError


class YoloDetector:
    name = "yolo"

    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.35, image_size: int = 640) -> None:
        self.model_path = model_path
        self.confidence = confidence
        self.image_size = image_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from ultralytics import YOLO  # noqa: PLC0415 — тяжёлый импорт по требованию
                except ImportError as exc:
                    raise DetectorError(
                        "не установлен ultralytics: pip install -r requirements-ml.txt"
                    ) from exc
                self._model = YOLO(self.model_path)
        return self._model

    def detect(self, image_bytes: bytes) -> list[Detection]:
        from PIL import Image  # noqa: PLC0415

        model = self._load()
        with Image.open(io.BytesIO(image_bytes)) as image:
            frame = image.convert("RGB")
            width, height = frame.size
            try:
                results = model.predict(frame, conf=self.confidence, imgsz=self.image_size, verbose=False)
            except Exception as exc:  # ultralytics бросает разное
                raise DetectorError(f"YOLO не смогла обработать кадр: {exc}") from exc

        detections: list[Detection] = []
        for result in results:
            names = result.names
            for box in getattr(result, "boxes", []) or []:
                raw = str(names[int(box.cls)])
                if products.is_ignored(raw):
                    continue
                key = products.normalize(raw)
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                detections.append(
                    Detection(
                        key=key,
                        label=products.describe(key).label,
                        confidence=float(box.conf),
                        box=(x1 / width, y1 / height, x2 / width, y2 / height),
                        raw_label=raw,
                    )
                )
        return detections
