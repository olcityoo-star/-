"""HTTP API и раздача веб-приложения."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import products
from .config import PROJECT_ROOT, Settings
from .service import FridgeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

WEB_DIR = PROJECT_ROOT / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    service = FridgeService(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.start_background()
        try:
            yield
        finally:
            service.stop_background()

    app = FastAPI(title="Умный холодильник", version="1.0.0", lifespan=lifespan)
    app.state.service = service
    app.state.settings = settings

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return service.status()

    @app.get("/api/inventory")
    def inventory() -> dict[str, Any]:
        items = service.tracker.state()
        by_category: dict[str, dict[str, Any]] = {}
        for item in items:
            group = by_category.setdefault(
                item["category"],
                {"category": item["category"], "label": item["category_label"], "items": []},
            )
            group["items"].append(item)
        return {
            "items": items,
            "groups": list(by_category.values()),
            "total": sum(item["count"] for item in items),
            "shopping_list": service.tracker.shopping_list(),
        }

    @app.get("/api/events")
    def events(limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        rows = []
        for event in service.storage.get_events(limit):
            product = products.describe(event.item_key)
            rows.append(
                {
                    "id": event.id,
                    "ts": event.ts,
                    "kind": event.kind,
                    "key": event.item_key,
                    "label": product.label,
                    "emoji": product.emoji,
                    "delta": event.delta,
                    "count": event.count,
                    "source": event.source,
                }
            )
        return {"events": rows}

    @app.post("/api/scan")
    def scan() -> JSONResponse:
        result = service.scan()
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @app.post("/api/scan/upload")
    async def scan_upload(file: UploadFile) -> JSONResponse:
        """Точка для камер, которые сами отправляют снимок на сервер."""
        payload = await file.read()
        result = service.scan_image(payload, source=f"upload:{file.filename}")
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @app.post("/api/items/{key:path}")
    def set_item(key: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        try:
            count = int(body.get("count", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="count должен быть числом") from exc
        change = service.tracker.set_count(key, count)
        return {"ok": True, "change": change.to_dict()}

    @app.get("/api/snapshot.jpg")
    def snapshot() -> Response:
        latest = service.storage.latest_snapshot()
        if latest is None or not Path(latest.path).exists():
            raise HTTPException(status_code=404, detail="снимков ещё нет")
        return FileResponse(latest.path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return {
            "categories": products.CATEGORIES,
            "products": [
                {
                    "key": p.key,
                    "label": p.label,
                    "emoji": p.emoji,
                    "category": p.category,
                    "shelf_life_days": p.shelf_life_days,
                    "staple": p.staple,
                }
                for p in products.PRODUCTS.values()
            ],
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:  # pragma: no cover — только при поломанной установке

        @app.get("/")
        def missing_ui(request: Request) -> dict[str, str]:
            return {"error": f"каталог с интерфейсом не найден: {WEB_DIR}"}

    return app


app = create_app()
