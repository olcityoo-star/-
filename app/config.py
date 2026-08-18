from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRIDGE_DATA_DIR", ROOT / "data"))
SCANS_DIR = DATA_DIR / "scans"
RUNTIME_SETTINGS = DATA_DIR / "settings.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    detector: str = "demo"
    camera_url: str = ""
    camera_user: str = ""
    camera_password: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llava"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    scan_interval_seconds: int = 0
    host: str = "0.0.0.0"
    port: int = 8080
    database_url: str = ""


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCANS_DIR.mkdir(parents=True, exist_ok=True)


def _runtime_overrides() -> dict:
    if not RUNTIME_SETTINGS.exists():
        return {}
    try:
        data = json.loads(RUNTIME_SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {key: value for key, value in data.items() if value is not None}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _ensure_dirs()
    values = Settings().model_dump()
    values.update(_runtime_overrides())
    if not values.get("database_url"):
        values["database_url"] = f"sqlite:///{DATA_DIR / 'fridge.db'}"
    return Settings(**values)


def save_runtime_settings(updates: dict) -> Settings:
    _ensure_dirs()
    current = _runtime_overrides()
    for key, value in updates.items():
        if value is not None:
            current[key] = value
    RUNTIME_SETTINGS.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    get_settings.cache_clear()
    return get_settings()
