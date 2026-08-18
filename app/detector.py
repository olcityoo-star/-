"""Распознавание продуктов: демо, YOLO-World, Ollama (LLaVA), OpenAI Vision."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import cv2
import httpx
import numpy as np

from .catalog import YOLO_CLASSES, describe
from .demo_image import demo_detections
from .schemas import Box, DetectedItem


VISION_PROMPT = """Ты помощник по инвентаризации холодильника.
Посмотри на фото внутри холодильника.
Верни ТОЛЬКО JSON вида:
{"items":[{"name":"milk","name_ru":"Молоко","count":1,"confidence":0.8,"notes":"бренд или состояние"}]}
Включай только то, что реально видно. count — сколько отдельных упаковок/штук.
name пиши по-английски коротко (milk, eggs, cheese, yogurt, tomato, cucumber...).
"""


class DetectorError(RuntimeError):
    pass


class Detector(ABC):
    name = "base"

    @abstractmethod
    def detect(self, image_bgr: np.ndarray) -> list[DetectedItem]:
        raise NotImplementedError


class DemoDetector(Detector):
    name = "demo"

    def detect(self, image_bgr: np.ndarray) -> list[DetectedItem]:
        _ = image_bgr
        return demo_detections()


def _encode_jpeg_b64(image_bgr: np.ndarray) -> str:
    import base64

    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise DetectorError("Не удалось сжать кадр в JPEG.")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _parse_items_payload(payload: Any) -> list[DetectedItem]:
    if isinstance(payload, str):
        payload = _extract_json(payload)
    items_raw = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(items_raw, list):
        raise DetectorError("Модель вернула JSON без списка items.")
    detections: list[DetectedItem] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name_ru") or raw.get("name") or "").strip()
        if not name:
            continue
        meta = describe(str(raw.get("name") or name))
        if raw.get("name_ru"):
            meta = {**meta, "name": str(raw["name_ru"])}
        box_raw = raw.get("box") or {}
        detections.append(
            DetectedItem(
                name=meta["name"],
                name_en=meta["name_en"],
                key=meta["key"],
                emoji=meta["emoji"],
                category=meta["category"],
                count=max(1, int(raw.get("count") or 1)),
                confidence=float(raw.get("confidence") or 0.6),
                notes=str(raw.get("notes") or ""),
                box=Box(
                    x1=float(box_raw.get("x1") or 0),
                    y1=float(box_raw.get("y1") or 0),
                    x2=float(box_raw.get("x2") or 0),
                    y2=float(box_raw.get("y2") or 0),
                ),
            )
        )
    return _merge_same_keys(detections)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise DetectorError("Модель не вернула JSON.")
        return json.loads(match.group(0))


def _merge_same_keys(items: list[DetectedItem]) -> list[DetectedItem]:
    merged: dict[str, DetectedItem] = {}
    for item in items:
        current = merged.get(item.key)
        if current is None:
            merged[item.key] = item
            continue
        current.count += item.count
        current.confidence = max(current.confidence, item.confidence)
        if item.notes and item.notes not in current.notes:
            current.notes = ", ".join(part for part in (current.notes, item.notes) if part)
    return list(merged.values())


class OllamaDetector(Detector):
    name = "ollama"

    def __init__(self, url: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.model = model

    def detect(self, image_bgr: np.ndarray) -> list[DetectedItem]:
        b64 = _encode_jpeg_b64(image_bgr)
        try:
            response = httpx.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {
                            "role": "user",
                            "content": VISION_PROMPT,
                            "images": [b64],
                        }
                    ],
                },
                timeout=90.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DetectorError(
                f"Ollama недоступна ({self.url}). Установите Ollama и модель {self.model}."
            ) from exc
        content = response.json().get("message", {}).get("content", "")
        return _parse_items_payload(content)


class OpenAIDetector(Detector):
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        if not api_key:
            raise DetectorError("Задайте OPENAI_API_KEY в .env для облачного распознавания.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def detect(self, image_bgr: np.ndarray) -> list[DetectedItem]:
        b64 = _encode_jpeg_b64(image_bgr)
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": VISION_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                                },
                            ],
                        }
                    ],
                },
                timeout=90.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DetectorError(f"Ошибка OpenAI Vision: {exc}") from exc
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_items_payload(content)


class YoloWorldDetector(Detector):
    name = "yolo"

    def __init__(self) -> None:
        try:
            from ultralytics import YOLOWorld
        except ImportError as exc:
            raise DetectorError(
                "Для локального YOLO установите: pip install -r requirements-ml.txt"
            ) from exc
        self.model = YOLOWorld("yolov8s-world.pt")
        self.model.set_classes(list(YOLO_CLASSES))

    def detect(self, image_bgr: np.ndarray) -> list[DetectedItem]:
        results = self.model.predict(image_bgr, verbose=False, conf=0.18)
        detections: list[DetectedItem] = []
        if not results:
            return detections
        result = results[0]
        names = result.names or {}
        if result.boxes is None:
            return detections
        for box in result.boxes:
            cls_id = int(box.cls[0])
            label = str(names.get(cls_id, cls_id))
            meta = describe(label)
            xyxy = box.xyxy[0].tolist()
            detections.append(
                DetectedItem(
                    name=meta["name"],
                    name_en=meta["name_en"],
                    key=meta["key"],
                    emoji=meta["emoji"],
                    category=meta["category"],
                    count=1,
                    confidence=float(box.conf[0]),
                    box=Box(x1=xyxy[0], y1=xyxy[1], x2=xyxy[2], y2=xyxy[3]),
                )
            )
        return _merge_same_keys(detections)


def create_detector(settings) -> Detector:
    kind = (settings.detector or "demo").strip().lower()
    if kind == "demo":
        return DemoDetector()
    if kind == "ollama":
        return OllamaDetector(settings.ollama_url, settings.ollama_model)
    if kind == "openai":
        return OpenAIDetector(settings.openai_api_key, settings.openai_base_url, settings.openai_model)
    if kind == "yolo":
        return YoloWorldDetector()
    raise DetectorError(f"Неизвестный детектор: {kind}. Доступны: demo, ollama, openai, yolo.")
