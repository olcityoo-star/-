"""Сервисный слой: кадр с камеры → распознавание → инвентарь."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import camera as camera_module
from .config import Settings
from .detectors import build_detector
from .detectors.base import Detection, DetectorError
from .inventory import InventoryTracker
from .storage import Storage

log = logging.getLogger("fridge")


class FridgeService:
    def __init__(self, settings: Settings, storage: Storage | None = None) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.storage = storage or Storage(settings.db_path)
        self.tracker = InventoryTracker(
            self.storage,
            confirmations=settings.confirmations,
            min_confidence=settings.detector_confidence,
        )
        self._camera: camera_module.Camera | None = None
        self._detector = None
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.last_error: str | None = None
        self.last_scan_ts: float | None = None
        self.scan_count = 0
        self.auto_scan = settings.scan_interval > 0

    # --- ленивое создание камеры и модели --------------------------------

    @property
    def camera(self) -> camera_module.Camera:
        if self._camera is None:
            self._camera = camera_module.build_camera(
                self.settings.camera_url,
                self.settings.camera_username,
                self.settings.camera_password,
                self.settings.camera_timeout,
            )
        return self._camera

    @property
    def detector(self):
        if self._detector is None:
            self._detector = build_detector(self.settings)
        return self._detector

    @property
    def detector_name(self) -> str:
        try:
            return getattr(self.detector, "name", self.settings.detector)
        except DetectorError:
            return self.settings.detector

    # --- сканирование ------------------------------------------------------

    def scan(self) -> dict[str, Any]:
        """Снимает кадр с камеры и обновляет инвентарь."""
        try:
            frame = self.camera.capture()
        except camera_module.CameraError as exc:
            return self._fail(f"камера: {exc}")
        return self._process(frame.jpeg, frame.width, frame.height, frame.source)

    def scan_image(self, jpeg: bytes, source: str = "upload") -> dict[str, Any]:
        """Обрабатывает кадр, присланный извне (камера сама шлёт снимки на сервер)."""
        try:
            frame = camera_module.decode_frame(jpeg, source)
        except camera_module.CameraError as exc:
            return self._fail(str(exc))
        return self._process(frame.jpeg, frame.width, frame.height, source)

    def _process(self, jpeg: bytes, width: int, height: int, source: str) -> dict[str, Any]:
        with self._scan_lock:
            try:
                detections: list[Detection] = self.detector.detect(jpeg)
            except DetectorError as exc:
                return self._fail(f"распознавание: {exc}")
            except Exception as exc:  # noqa: BLE001 — модель может упасть как угодно
                log.exception("детектор упал")
                return self._fail(f"распознавание: {exc}")

            changes = self.tracker.observe(detections)
            snapshot_path = self._save_snapshot(jpeg)
            snapshot_id = self.storage.add_snapshot(
                path=str(snapshot_path),
                width=width,
                height=height,
                detector=self.detector_name,
                detections=[d.to_dict() for d in detections],
            )
            self._prune_snapshots()

            self.last_error = None
            self.last_scan_ts = time.time()
            self.scan_count += 1
            return {
                "ok": True,
                "ts": self.last_scan_ts,
                "source": source,
                "snapshot_id": snapshot_id,
                "detections": [d.to_dict() for d in detections],
                "changes": [c.to_dict() for c in changes],
            }

    def _fail(self, message: str) -> dict[str, Any]:
        log.warning("сканирование не удалось: %s", message)
        self.last_error = message
        return {"ok": False, "error": message, "ts": time.time(), "detections": [], "changes": []}

    def _save_snapshot(self, jpeg: bytes) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = self.settings.snapshots_dir / f"{stamp}.jpg"
        path.write_bytes(jpeg)
        return path

    def _prune_snapshots(self) -> None:
        for path in self.storage.prune_snapshots(self.settings.keep_snapshots):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("не удалось удалить старый снимок %s: %s", path, exc)

    # --- фоновый цикл -------------------------------------------------------

    def start_background(self) -> None:
        if self._thread is not None or self.settings.scan_interval <= 0:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fridge-scan", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        interval = max(5.0, self.settings.scan_interval)
        # Первый скан сразу после старта, чтобы приложение не было пустым.
        while not self._stop.is_set():
            if self.auto_scan:
                try:
                    self.scan()
                except Exception:  # noqa: BLE001 — фоновый поток не должен умирать
                    log.exception("ошибка в фоновом сканировании")
            self._stop.wait(interval)

    # --- состояние -----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        snapshot = self.storage.latest_snapshot()
        return {
            "camera": {
                "source": self.settings.camera_url or "demo:",
                "kind": type(self.camera).__name__,
            },
            "detector": {
                "name": self.settings.detector,
                "model": self.settings.detector_model
                if self.settings.detector == "yolo"
                else self.settings.vlm_model
                if self.settings.detector == "vlm"
                else "—",
            },
            "auto_scan": self.auto_scan,
            "scan_interval": self.settings.scan_interval,
            "scan_count": self.scan_count,
            "last_scan_ts": self.last_scan_ts,
            "last_error": self.last_error,
            "snapshot": None
            if snapshot is None
            else {
                "id": snapshot.id,
                "ts": snapshot.ts,
                "width": snapshot.width,
                "height": snapshot.height,
                "detections": snapshot.detections,
            },
        }
