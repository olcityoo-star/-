from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import __version__
from .camera import CameraError, capture_frame, mask_url
from .catalog import CATEGORIES, describe
from .config import ROOT, SCANS_DIR, Settings, get_settings, save_runtime_settings
from .demo_image import render_demo_fridge
from .detector import DetectorError, create_detector
from .inventory import apply_scan, item_to_out, scan_to_out, shopping_list
from .models import Event, Item, Scan, get_session, init_db
from .schemas import (
    AppState,
    EventOut,
    HealthOut,
    ItemOut,
    ItemPatch,
    ScanOut,
    SettingsIn,
    SettingsOut,
)

STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Умный холодильник", version=__version__, lifespan=lifespan)


def settings_out(settings: Settings) -> SettingsOut:
    return SettingsOut(
        detector=settings.detector,
        camera_configured=bool(settings.camera_url.strip()),
        camera_url_masked=mask_url(settings.camera_url),
        scan_interval_seconds=settings.scan_interval_seconds,
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        openai_configured=bool(settings.openai_api_key.strip()),
        openai_model=settings.openai_model,
    )


def save_image(image_bgr, scan_id_hint: str) -> Path:
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCANS_DIR / f"{scan_id_hint}.jpg"
    if not cv2.imwrite(str(path), image_bgr):
        raise HTTPException(status_code=500, detail="Не удалось сохранить снимок.")
    return path


def run_scan(session: Session, settings: Settings, image_bgr, source: str, note: str = "") -> ScanOut:
    detector = create_detector(settings)
    try:
        detections = detector.detect(image_bgr)
    except DetectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    path = save_image(image_bgr, stamp)
    scan = Scan(source=source, image_path=str(path), note=note)
    session.add(scan)
    session.flush()
    apply_scan(session, detections, scan)
    session.flush()
    session.refresh(scan)
    return scan_to_out(scan)


@app.get("/api/health", response_model=HealthOut)
def health(settings: Settings = Depends(get_settings)) -> HealthOut:
    return HealthOut(
        ok=True,
        version=__version__,
        detector=settings.detector,
        camera_configured=bool(settings.camera_url.strip()),
    )


@app.get("/api/state", response_model=AppState)
def state(session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> AppState:
    items = [item_to_out(row) for row in session.query(Item).order_by(Item.status.asc(), Item.name.asc()).all()]
    last = session.query(Scan).order_by(Scan.id.desc()).first()
    events = [
        EventOut(id=row.id, created_at=row.created_at, kind=row.kind, item_key=row.item_key, message=row.message)
        for row in session.query(Event).order_by(Event.id.desc()).limit(20).all()
    ]
    present = [item for item in items if item.status == "in"]
    shopping = shopping_list(session)
    return AppState(
        inventory=items,
        last_scan=scan_to_out(last) if last else None,
        events=events,
        shopping=shopping,
        settings=settings_out(settings),
        summary={
            "inside": len(present),
            "maybe_gone": len([item for item in items if item.status == "maybe_gone"]),
            "gone": len([item for item in items if item.status == "gone"]),
            "shopping": len(shopping),
        },
    )


@app.get("/api/inventory", response_model=list[ItemOut])
def inventory(session: Session = Depends(get_session)) -> list[ItemOut]:
    return [item_to_out(row) for row in session.query(Item).order_by(Item.name.asc()).all()]


@app.patch("/api/items/{item_id}", response_model=ItemOut)
def patch_item(item_id: int, body: ItemPatch, session: Session = Depends(get_session)) -> ItemOut:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    if body.name is not None:
        meta = describe(body.name)
        item.name = body.name
        item.key = meta["key"] if item.key.startswith("unknown") or not item.key else item.key
        item.emoji = meta["emoji"]
        item.category = meta["category"]
    if body.count is not None:
        item.count = max(0, body.count)
    if body.status is not None:
        item.status = body.status
    if body.notes is not None:
        item.notes = body.notes
    if body.wanted is not None:
        item.wanted = max(0, body.wanted)
    return item_to_out(item)


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, session: Session = Depends(get_session)) -> dict:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    session.delete(item)
    return {"ok": True}


@app.post("/api/items", response_model=ItemOut)
def add_item(body: ItemPatch, session: Session = Depends(get_session)) -> ItemOut:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название продукта")
    meta = describe(name)
    existing = session.query(Item).filter(Item.key == meta["key"]).one_or_none()
    if existing:
        existing.status = "in"
        existing.count = body.count or max(existing.count, 1)
        return item_to_out(existing)
    item = Item(
        key=meta["key"],
        name=meta["name"],
        name_en=meta["name_en"],
        emoji=meta["emoji"],
        category=meta["category"],
        count=body.count or 1,
        status="in",
        notes=body.notes or "",
        wanted=body.wanted or 0,
    )
    session.add(item)
    session.flush()
    return item_to_out(item)


@app.post("/api/scans/demo", response_model=ScanOut)
def scan_demo(session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> ScanOut:
    image = render_demo_fridge()
    demo_settings = settings.model_copy(update={"detector": "demo"})
    return run_scan(session, demo_settings, image, source="demo", note="демо-кадр")


@app.post("/api/scans/camera", response_model=ScanOut)
def scan_camera(session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> ScanOut:
    try:
        frame = capture_frame(settings)
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run_scan(session, settings, frame.image, source=frame.source)


@app.post("/api/scans/upload", response_model=ScanOut)
async def scan_upload(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ScanOut:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    from .camera import CameraError, decode_upload

    try:
        image = decode_upload(data)
    except CameraError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run_scan(session, settings, image, source="upload", note=file.filename or "")


@app.get("/api/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, session: Session = Depends(get_session)) -> ScanOut:
    scan = session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Скан не найден")
    return scan_to_out(scan)


@app.get("/api/scans/{scan_id}/image")
def scan_image(scan_id: int, session: Session = Depends(get_session)) -> FileResponse:
    scan = session.get(Scan, scan_id)
    if scan is None or not Path(scan.image_path).exists():
        raise HTTPException(status_code=404, detail="Снимок не найден")
    return FileResponse(scan.image_path, media_type="image/jpeg")


@app.get("/api/settings", response_model=SettingsOut)
def get_app_settings(settings: Settings = Depends(get_settings)) -> SettingsOut:
    return settings_out(settings)


@app.post("/api/settings", response_model=SettingsOut)
def update_settings(body: SettingsIn) -> SettingsOut:
    settings = save_runtime_settings(body.model_dump(exclude_unset=True))
    return settings_out(settings)


@app.get("/api/categories")
def categories() -> dict:
    return CATEGORIES


if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
