from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Box(BaseModel):
    x1: float = 0
    y1: float = 0
    x2: float = 0
    y2: float = 0


class DetectedItem(BaseModel):
    name: str
    name_en: str = ""
    key: str = ""
    emoji: str = "🧊"
    category: str = "other"
    count: int = 1
    confidence: float = 0.5
    notes: str = ""
    box: Box = Field(default_factory=Box)


class ItemOut(BaseModel):
    id: int
    key: str
    name: str
    name_en: str
    emoji: str
    category: str
    category_label: str
    count: int
    status: str
    first_seen: datetime
    last_seen: datetime
    missed_scans: int
    notes: str
    wanted: int


class ItemPatch(BaseModel):
    name: str | None = None
    count: int | None = None
    status: str | None = None
    notes: str | None = None
    wanted: int | None = None


class ScanOut(BaseModel):
    id: int
    created_at: datetime
    source: str
    note: str
    image_url: str
    detections: list[DetectedItem]


class EventOut(BaseModel):
    id: int
    created_at: datetime
    kind: str
    item_key: str
    message: str


class SettingsOut(BaseModel):
    detector: str
    camera_configured: bool
    camera_url_masked: str
    scan_interval_seconds: int
    ollama_url: str
    ollama_model: str
    openai_configured: bool
    openai_model: str


class SettingsIn(BaseModel):
    detector: str | None = None
    camera_url: str | None = None
    camera_user: str | None = None
    camera_password: str | None = None
    scan_interval_seconds: int | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None


class HealthOut(BaseModel):
    ok: bool
    version: str
    detector: str
    camera_configured: bool


class AppState(BaseModel):
    inventory: list[ItemOut]
    last_scan: ScanOut | None
    events: list[EventOut]
    shopping: list[dict[str, Any]]
    settings: SettingsOut
    summary: dict[str, int]
