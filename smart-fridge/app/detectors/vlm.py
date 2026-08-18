"""Распознавание через мультимодальную модель по HTTP (OpenAI-совместимый API).

Подходит любой сервис с эндпоинтом ``/chat/completions``, принимающий картинки:
OpenAI, OpenRouter, локальные Ollama или llama.cpp. В отличие от YOLO, словарь
не ограничен: модель называет сметану сметаной без всякого дообучения.
"""

from __future__ import annotations

import base64
import json
import re

from .. import products
from .base import Detection, DetectorError

PROMPT = """Ты — система учёта продуктов в холодильнике. На фото полки холодильника.
Перечисли только съедобные продукты и напитки, которые реально видно.
Не перечисляй полки, руки, посуду и пустые контейнеры.
Одинаковые продукты объединяй в одну запись и указывай количество.
Отвечай строго JSON без пояснений в формате:
{"items": [{"name": "название продукта по-русски", "count": 1, "confidence": 0.0-1.0}]}"""


class VlmDetector:
    name = "vlm"

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_items: int = 40,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_items = max_items

    def detect(self, image_bytes: bytes) -> list[Detection]:
        import requests  # noqa: PLC0415

        data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.post(
                f"{self.api_url}/chat/completions", json=payload, headers=headers, timeout=self.timeout
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            raise DetectorError(f"запрос к модели не удался: {exc}") from exc
        except ValueError as exc:
            raise DetectorError(f"модель вернула не JSON: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DetectorError(f"неожиданный ответ модели: {body!r}") from exc

        return parse_items(content, self.max_items)


def parse_items(content: str, max_items: int = 40) -> list[Detection]:
    """Разбирает ответ модели в список детекций.

    Модели любят оборачивать JSON в ```json ... ``` или добавлять фразу
    до и после, поэтому вырезаем первый JSON-объект из текста.
    """
    if isinstance(content, list):  # некоторые API отдают content блоками
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise DetectorError(f"в ответе модели нет JSON: {content[:200]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise DetectorError(f"не разобрать JSON от модели: {exc}") from exc

    raw_items = data.get("items", data if isinstance(data, list) else [])
    detections: list[Detection] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("label") or "").strip()
        if not name or products.is_ignored(name):
            continue
        try:
            count = int(entry.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        try:
            confidence = float(entry.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8
        key = products.normalize(name)
        # Модель отдаёт количество числом, а инвентарь считает объекты —
        # разворачиваем в отдельные детекции.
        for _ in range(max(1, min(count, 20))):
            detections.append(
                Detection(
                    key=key,
                    label=products.describe(key).label,
                    confidence=max(0.0, min(1.0, confidence)),
                    raw_label=name,
                )
            )
        if len(detections) >= max_items:
            break
    return detections[:max_items]
