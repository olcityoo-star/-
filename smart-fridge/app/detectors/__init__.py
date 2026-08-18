"""Выбор распознавателя по настройкам."""

from __future__ import annotations

from ..config import Settings
from .base import Detection, Detector, DetectorError
from .demo import DemoDetector

__all__ = ["Detection", "Detector", "DetectorError", "DemoDetector", "build_detector"]


def build_detector(settings: Settings) -> Detector:
    name = (settings.detector or "demo").lower()
    if name in {"", "demo", "fake"}:
        return DemoDetector()
    if name in {"yolo", "ultralytics"}:
        from .yolo import YoloDetector

        return YoloDetector(
            model_path=settings.detector_model,
            confidence=settings.detector_confidence,
        )
    if name in {"vlm", "openai", "gpt"}:
        from .vlm import VlmDetector

        if not settings.vlm_api_key and "api.openai.com" in settings.vlm_api_url:
            raise DetectorError("для DETECTOR=vlm нужен VLM_API_KEY")
        return VlmDetector(
            api_url=settings.vlm_api_url,
            api_key=settings.vlm_api_key,
            model=settings.vlm_model,
        )
    raise DetectorError(f"неизвестный распознаватель: {settings.detector!r}")
