"""Базовые типы для распознавателей продуктов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Detection:
    """Один распознанный продукт на кадре.

    `box` — координаты рамки в долях кадра (x1, y1, x2, y2), чтобы не зависеть
    от разрешения камеры при отрисовке в браузере.
    """

    key: str
    label: str
    confidence: float
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    raw_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "box": [round(v, 4) for v in self.box],
            "raw_label": self.raw_label or self.label,
        }


class Detector(Protocol):
    """Интерфейс распознавателя: получает JPEG-кадр, возвращает список продуктов."""

    name: str

    def detect(self, image_bytes: bytes) -> list[Detection]: ...


class DetectorError(RuntimeError):
    """Распознавание не удалось (модель недоступна, API вернул ошибку и т.п.)."""
