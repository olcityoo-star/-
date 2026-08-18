from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fridge import db
from fridge.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    captures = tmp_path / "captures"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "CAPTURES_DIR", captures)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr("fridge.main.CAPTURES_DIR", captures)
    monkeypatch.setattr("fridge.config.DB_PATH", db_path)
    monkeypatch.setattr("fridge.config.CAPTURES_DIR", captures)
    captures.mkdir(parents=True, exist_ok=True)
    db.init_db(db_path)
    return TestClient(app)


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "ActionCam" in res.json()["camera_name"]


def test_item_crud(client):
    created = client.post(
        "/api/items",
        json={"name": "Молоко", "quantity": 1, "unit": "л", "category": "молочка", "expires_on": "2026-08-20"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    listed = client.get("/api/items")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "Молоко"

    patched = client.patch(f"/api/items/{item_id}", json={"quantity": 2})
    assert patched.json()["quantity"] == 2

    deleted = client.delete(f"/api/items/{item_id}")
    assert deleted.status_code == 204
    assert client.get("/api/items").json() == []


def test_settings_roundtrip(client):
    res = client.put(
        "/api/settings",
        json={
            "camera_host": "192.168.25.1",
            "stream_url": "http://192.168.25.1:8080/?action=stream",
            "confidence": "0.4",
            "food_only": False,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["camera_host"] == "192.168.25.1"
    assert body["food_only"] == "0"
    assert body["confidence"] == "0.4"


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (64, 48), (20, 80, 90))
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_scan_without_model(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("модель не установлена")

    monkeypatch.setattr("fridge.main.detect_image", boom)
    res = client.post("/api/scan/upload", files={"file": ("fridge.jpg", _jpeg_bytes(), "image/jpeg")})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "upload"
    assert body["detections"] == []
    assert "модель" in (body["detect_error"] or "")
    assert client.get(body["image_url"]).status_code == 200


def test_confirm_scan_creates_items(client, monkeypatch):
    monkeypatch.setattr("fridge.main.detect_image", lambda *_args, **_kwargs: [
        {"name": "Яблоко", "accepted": True, "quantity": 2, "unit": "шт", "confidence": 0.9}
    ])
    scan = client.post("/api/scan/upload", files={"file": ("fridge.jpg", _jpeg_bytes(), "image/jpeg")}).json()
    confirmed = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={"detections": [{"name": "Яблоко", "accepted": True, "quantity": 2, "unit": "шт"}]},
    )
    assert confirmed.status_code == 200
    items = confirmed.json()["items"]
    assert items[0]["name"] == "Яблоко"
    assert items[0]["source"] == "scan"
    assert client.get("/api/items").json()[0]["name"] == "Яблоко"


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Полки" in res.text
