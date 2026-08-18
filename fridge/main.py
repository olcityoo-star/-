from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fridge import db
from fridge.camera import capture_snapshot, probe_camera
from fridge.config import CAPTURES_DIR, STATIC_DIR
from fridge.detect import detect_image, detector_status, ensure_model

app = FastAPI(title="Умный холодильник", version="0.1.0")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: float = 1
    unit: str = "шт"
    category: str = "другое"
    expires_on: str | None = None
    notes: str = ""


class ItemPatch(BaseModel):
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    category: str | None = None
    expires_on: str | None = None
    notes: str | None = None


class SettingsIn(BaseModel):
    camera_name: str | None = None
    camera_host: str | None = None
    stream_url: str | None = None
    snapshot_url: str | None = None
    confidence: str | float | None = None
    food_only: str | bool | int | None = None


class ConfirmIn(BaseModel):
    detections: list[dict]


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _save_jpeg(payload: bytes, prefix: str) -> str:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}-{uuid4().hex[:10]}.jpg"
    (CAPTURES_DIR / name).write_bytes(payload)
    return name


def _confidence(settings: dict[str, str]) -> float:
    try:
        return max(0.05, min(0.95, float(settings.get("confidence") or 0.35)))
    except ValueError:
        return 0.35


def _food_only(settings: dict[str, str]) -> bool:
    return str(settings.get("food_only") or "1").lower() not in {"0", "false", "off", "no"}


def _run_detect(image_bytes: bytes, settings: dict[str, str]) -> tuple[list[dict], str | None]:
    try:
        detections = detect_image(
            image_bytes,
            confidence=_confidence(settings),
            food_only=_food_only(settings),
        )
        return detections, None
    except Exception as exc:  # noqa: BLE001 — scan should still save the photo
        return [], str(exc)


@app.get("/api/health")
def health() -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
    status = detector_status()
    return {
        "ok": True,
        "camera_name": settings.get("camera_name"),
        "model_ready": status["model_ready"],
    }


@app.get("/api/settings")
def get_settings() -> dict:
    with db.session() as conn:
        return db.get_settings(conn)


@app.put("/api/settings")
def put_settings(payload: SettingsIn) -> dict:
    values = payload.model_dump(exclude_none=True)
    if "food_only" in values:
        values["food_only"] = "1" if str(values["food_only"]).lower() not in {"0", "false", "off"} else "0"
    with db.session() as conn:
        return db.update_settings(conn, values)


@app.get("/api/items")
def get_items() -> list[dict]:
    with db.session() as conn:
        return db.list_items(conn)


@app.post("/api/items", status_code=201)
def post_item(payload: ItemIn) -> dict:
    with db.session() as conn:
        return db.create_item(conn, payload.model_dump())


@app.patch("/api/items/{item_id}")
def patch_item(item_id: int, payload: ItemPatch) -> dict:
    with db.session() as conn:
        item = db.update_item(conn, item_id, payload.model_dump(exclude_none=True))
    if item is None:
        raise HTTPException(404, "Продукт не найден")
    return item


@app.delete("/api/items/{item_id}", status_code=204)
def remove_item(item_id: int) -> Response:
    with db.session() as conn:
        ok = db.delete_item(conn, item_id)
    if not ok:
        raise HTTPException(404, "Продукт не найден")
    return Response(status_code=204)


@app.get("/api/camera/status")
def camera_status() -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
    probe = probe_camera(settings)
    probe["settings"] = {
        "camera_name": settings.get("camera_name"),
        "camera_host": settings.get("camera_host"),
        "stream_url": settings.get("stream_url"),
        "snapshot_url": settings.get("snapshot_url"),
    }
    return probe


@app.get("/api/camera/snapshot")
def camera_snapshot() -> Response:
    with db.session() as conn:
        settings = db.get_settings(conn)
    try:
        jpeg = capture_snapshot(settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/api/model")
def model_info() -> dict:
    return detector_status()


@app.post("/api/model/download")
def model_download() -> dict:
    try:
        return ensure_model()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/scan")
def scan_camera() -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
    try:
        jpeg = capture_snapshot(settings)
    except Exception as extra:  # noqa: BLE001
        raise HTTPException(503, str(extra)) from extra
    name = _save_jpeg(jpeg, "cam")
    detections, error = _run_detect(jpeg, settings)
    with db.session() as conn:
        scan = db.create_scan(conn, name, detections, source="camera")
    scan["detect_error"] = error
    scan["image_url"] = f"/api/captures/{name}"
    return scan


@app.post("/api/scan/upload")
async def scan_upload(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Пустой файл")
    try:
        from fridge.camera import as_jpeg

        jpeg = as_jpeg(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Нужно изображение JPEG/PNG: {exc}") from exc
    name = _save_jpeg(jpeg, "upl")
    with db.session() as conn:
        settings = db.get_settings(conn)
        detections, error = _run_detect(jpeg, settings)
        scan = db.create_scan(conn, name, detections, source="upload")
    scan["detect_error"] = error
    scan["image_url"] = f"/api/captures/{name}"
    return scan


@app.get("/api/scans/latest")
def get_latest_scan() -> dict:
    with db.session() as conn:
        scan = db.latest_scan(conn)
    if scan is None:
        raise HTTPException(404, "Сканов ещё не было")
    scan["image_url"] = f"/api/captures/{scan['image_name']}"
    return scan


@app.post("/api/scans/{scan_id}/confirm")
def confirm_scan(scan_id: int, payload: ConfirmIn) -> dict:
    with db.session() as conn:
        try:
            items = db.confirm_scan(conn, scan_id, payload.detections)
        except KeyError:
            raise HTTPException(404, "Скан не найден") from None
        scan = db.get_scan(conn, scan_id)
    assert scan is not None
    scan["image_url"] = f"/api/captures/{scan['image_name']}"
    return {"scan": scan, "items": items}


@app.get("/api/captures/{name}")
def get_capture(name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Некорректное имя файла")
    path = CAPTURES_DIR / name
    if not path.exists():
        raise HTTPException(404, "Снимок не найден")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
