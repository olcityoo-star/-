"""Демо-распознаватель: «узнаёт» то, что нарисовала демо-камера."""

from __future__ import annotations

import zlib

from .. import demo_scene, products
from .base import Detection


class DemoDetector:
    name = "demo"

    def detect(self, image_bytes: bytes) -> list[Detection]:
        detections: list[Detection] = []
        for item in demo_scene.current():
            # Псевдослучайная, но воспроизводимая уверенность: 0.78…0.97.
            noise = zlib.crc32(item.label.encode()) % 20
            detections.append(
                Detection(
                    key=products.normalize(item.label),
                    label=products.describe(products.normalize(item.label)).label,
                    confidence=0.78 + noise / 100,
                    box=item.box,
                    raw_label=item.label,
                )
            )
        return detections
