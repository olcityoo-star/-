from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fridge import db
from fridge.camera import capture_snapshot, discover_streams, probe_camera, wake_camera
from fridge.config import CAPTURES_DIR, STATIC_DIR
from fridge.dataset import add_sample, add_samples_from_scan, dataset_stats, delete_label
from fridge.detect import detect_image, detector_status, ensure_model
from fridge.learn import apply_custom_labels, gallery_status, train_gallery
from fridge.ocr import enrich_detections, ocr_status
from fridge.sync import build_sync_plan, shopping_list


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Умный холодильник", version="0.3.0", lifespan=lifespan)


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
    custom_threshold: str | float | None = None
    use_custom: str | bool | int | None = None


class ConfirmIn(BaseModel):
    detections: list[dict]
    remove_item_ids: list[int] = []
    mode: str = "sync"
    learn: bool = True


class LabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


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


def _use_custom(settings: dict[str, str]) -> bool:
    return str(settings.get("use_custom") or "1").lower() not in {"0", "false", "off", "no"}


def _custom_threshold(settings: dict[str, str]) -> float:
    try:
        return max(0.5, min(0.98, float(settings.get("custom_threshold") or 0.78)))
    except ValueError:
        return 0.78


def _decorate_scan(scan: dict, error: str | None = None) -> dict:
    scan["image_url"] = f"/api/captures/{scan['image_name']}"
    if error is not None:
        scan["detect_error"] = error
    return scan


def _process_image(jpeg: bytes, settings: dict[str, str], source: str) -> dict:
    detections, error = [], None
    try:
        detections = detect_image(
            jpeg,
            confidence=_confidence(settings),
            food_only=_food_only(settings),
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        detections = []

    detections = enrich_detections(jpeg, detections)
    if _use_custom(settings):
        detections = apply_custom_labels(jpeg, detections, threshold=_custom_threshold(settings))

    with db.session() as conn:
        aliases = db.get_aliases(conn)
        detections = db.apply_aliases(detections, aliases)
        items = db.list_items(conn)
        name = _save_jpeg(jpeg, "cam" if source == "camera" else "upl")
        scan = db.create_scan(conn, name, detections, source=source)
        plan = build_sync_plan(items, detections)

    scan = _decorate_scan(scan, error)
    scan["sync"] = plan
    scan["learning"] = gallery_status()
    return scan


@app.get("/api/health")
def health() -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
    status = detector_status()
    return {
        "ok": True,
        "version": "0.3.0",
        "camera_name": settings.get("camera_name"),
        "model_ready": status["model_ready"],
        "ocr": ocr_status(),
        "learning": gallery_status(),
    }


@app.get("/api/settings")
def get_settings() -> dict:
    with db.session() as conn:
        return db.get_settings(conn)


@app.put("/api/settings")
def put_settings(payload: SettingsIn) -> dict:
    values = payload.model_dump(exclude_none=True)
    for flag in ("food_only", "use_custom"):
        if flag in values:
            values[flag] = "1" if str(values[flag]).lower() not in {"0", "false", "off"} else "0"
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


@app.get("/api/shopping")
def get_shopping() -> dict:
    with db.session() as conn:
        items = db.list_items(conn)
    need = shopping_list(items)
    return {"items": need, "count": len(need)}


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


@app.post("/api/camera/wake")
def camera_wake() -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
    return wake_camera(settings)


@app.post("/api/camera/discover")
def camera_discover(save: bool = True) -> dict:
    with db.session() as conn:
        settings = db.get_settings(conn)
        result = discover_streams(settings)
        if save and result.get("suggestion"):
            suggestion = result["suggestion"]
            settings = db.update_settings(
                conn,
                {
                    "camera_host": suggestion["camera_host"],
                    "stream_url": suggestion["stream_url"],
                    "snapshot_url": suggestion["snapshot_url"],
                },
            )
            result["settings"] = settings
        else:
            result["settings"] = settings
    return result


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
    info = detector_status()
    info["ocr"] = ocr_status()
    return info


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
    return _process_image(jpeg, settings, source="camera")


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
    with db.session() as conn:
        settings = db.get_settings(conn)
    return _process_image(jpeg, settings, source="upload")


@app.get("/api/scans")
def get_scans() -> list[dict]:
    with db.session() as conn:
        rows = db.list_scans(conn)
    for row in rows:
        row["image_url"] = f"/api/captures/{row['image_name']}"
    return rows


@app.get("/api/scans/latest")
def get_latest_scan() -> dict:
    with db.session() as conn:
        scan = db.latest_scan(conn)
        items = db.list_items(conn)
    if scan is None:
        raise HTTPException(404, "Сканов ещё не было")
    scan = _decorate_scan(scan)
    scan["sync"] = build_sync_plan(items, scan.get("detections") or [])
    return scan


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: int) -> dict:
    with db.session() as conn:
        scan = db.get_scan(conn, scan_id)
        items = db.list_items(conn)
    if scan is None:
        raise HTTPException(404, "Скан не найден")
    scan = _decorate_scan(scan)
    scan["sync"] = build_sync_plan(items, scan.get("detections") or [])
    return scan


@app.post("/api/scans/{scan_id}/confirm")
def confirm_scan(scan_id: int, payload: ConfirmIn) -> dict:
    mode = payload.mode if payload.mode in {"sync", "append"} else "sync"
    with db.session() as conn:
        try:
            result = db.sync_inventory(
                conn,
                scan_id,
                payload.detections,
                remove_item_ids=payload.remove_item_ids,
                mode=mode,
            )
        except KeyError:
            raise HTTPException(404, "Скан не найден") from None
        scan = result["scan"]

    learned: list[dict] = []
    train_info = None
    if payload.learn and scan is not None:
        learned = add_samples_from_scan(scan["image_name"], payload.detections, scan_id=scan_id)
        if learned:
            try:
                train_info = train_gallery()
            except Exception as exc:  # noqa: BLE001
                train_info = {"error": str(exc)}

    assert scan is not None
    scan = _decorate_scan(scan)
    return {
        "scan": scan,
        "items": result["created"],
        "created": result["created"],
        "updated": result["updated"],
        "removed": result["removed"],
        "mode": mode,
        "learned_samples": len(learned),
        "train": train_info,
        "learning": gallery_status(),
    }


@app.get("/api/learn/status")
def learn_status() -> dict:
    return gallery_status()


@app.get("/api/learn/dataset")
def learn_dataset() -> dict:
    return dataset_stats()


@app.post("/api/learn/train")
def learn_train() -> dict:
    try:
        return train_gallery()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/learn/sample")
async def learn_sample(label: str = Form(...), file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Пустой файл")
    try:
        from fridge.camera import as_jpeg

        jpeg = as_jpeg(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Нужно изображение JPEG/PNG: {exc}") from exc
    sample = add_sample(label, jpeg, source="manual")
    try:
        train = train_gallery()
    except Exception as exc:  # noqa: BLE001
        train = {"error": str(exc)}
    return {"sample": sample, "train": train, "learning": gallery_status()}


@app.delete("/api/learn/label")
def learn_delete_label(payload: LabelIn) -> dict:
    removed = delete_label(payload.label)
    train = None
    try:
        if dataset_stats()["total"] > 0:
            train = train_gallery()
    except Exception as exc:  # noqa: BLE001
        train = {"error": str(exc)}
    return {"removed": removed, "train": train, "learning": gallery_status()}


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
