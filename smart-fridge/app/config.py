"""Настройки сервиса. Читаются из переменных окружения и файла .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path | None = None) -> None:
    """Простой парсер .env, чтобы не тянуть зависимость ради десяти строк."""
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name) or default))
    except ValueError:
        return default


@dataclass
class Settings:
    # Камера
    camera_url: str = "demo:"
    camera_username: str = ""
    camera_password: str = ""
    camera_timeout: float = 10.0

    # Распознавание
    detector: str = "demo"  # demo | yolo | vlm
    detector_model: str = "yolov8n.pt"
    detector_confidence: float = 0.35
    vlm_api_url: str = "https://api.openai.com/v1"
    vlm_api_key: str = ""
    vlm_model: str = "gpt-4o-mini"

    # Логика учёта
    scan_interval: float = 300.0  # 0 — автосканирование выключено
    confirmations: int = 2

    # Хранение
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    keep_snapshots: int = 50

    # Сервер
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fridge.db"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        data_dir = _env("DATA_DIR")
        return cls(
            camera_url=_env("CAMERA_URL", "demo:"),
            camera_username=_env("CAMERA_USERNAME"),
            camera_password=_env("CAMERA_PASSWORD"),
            camera_timeout=_env_float("CAMERA_TIMEOUT", 10.0),
            detector=_env("DETECTOR", "demo").lower(),
            detector_model=_env("DETECTOR_MODEL", "yolov8n.pt"),
            detector_confidence=_env_float("DETECTOR_CONFIDENCE", 0.35),
            vlm_api_url=_env("VLM_API_URL", "https://api.openai.com/v1"),
            vlm_api_key=_env("VLM_API_KEY"),
            vlm_model=_env("VLM_MODEL", "gpt-4o-mini"),
            scan_interval=_env_float("SCAN_INTERVAL", 300.0),
            confirmations=_env_int("CONFIRMATIONS", 2),
            data_dir=Path(data_dir) if data_dir else PROJECT_ROOT / "data",
            keep_snapshots=_env_int("KEEP_SNAPSHOTS", 50),
            host=_env("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8000),
        )
