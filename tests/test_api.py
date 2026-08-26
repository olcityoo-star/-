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
    dataset_dir = tmp_path / "dataset"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "CAPTURES_DIR", captures)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr("fridge.main.CAPTURES_DIR", captures)
    monkeypatch.setattr("fridge.config.DB_PATH", db_path)
    monkeypatch.setattr("fridge.config.CAPTURES_DIR", captures)
    monkeypatch.setattr("fridge.config.DATASET_DIR", dataset_dir)
    monkeypatch.setattr("fridge.config.SAMPLES_DIR", dataset_dir / "samples")
    monkeypatch.setattr("fridge.config.GALLERY_PATH", dataset_dir / "gallery.npz")
    monkeypatch.setattr("fridge.dataset.DATASET_DIR", dataset_dir)
    monkeypatch.setattr("fridge.dataset.SAMPLES_DIR", dataset_dir / "samples")
    monkeypatch.setattr("fridge.dataset.CAPTURES_DIR", captures)
    monkeypatch.setattr("fridge.learn.GALLERY_PATH", dataset_dir / "gallery.npz")
    captures.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)
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


def test_confirm_scan_creates_items(client, monkeypatch):
    monkeypatch.setattr("fridge.main.detect_image", lambda *_args, **_kwargs: [
        {"name": "Яблоко", "accepted": True, "quantity": 2, "unit": "шт", "confidence": 0.9, "bbox": {"x1": 1, "y1": 1, "x2": 10, "y2": 10}}
    ])
    monkeypatch.setattr("fridge.main.enrich_detections", lambda _jpeg, dets: dets)
    scan = client.post("/api/scan/upload", files={"file": ("fridge.jpg", _jpeg_bytes(), "image/jpeg")}).json()
    confirmed = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={"detections": [{"name": "Яблоко", "accepted": True, "quantity": 2, "unit": "шт"}], "mode": "append"},
    )
    assert confirmed.status_code == 200
    items = confirmed.json()["items"]
    assert items[0]["name"] == "Яблоко"
    assert items[0]["source"] == "scan"
    assert client.get("/api/items").json()[0]["name"] == "Яблоко"


def test_sync_removes_missing_and_updates_kept(client, monkeypatch):
    monkeypatch.setattr("fridge.main.enrich_detections", lambda _jpeg, dets: dets)
    client.post("/api/items", json={"name": "Яблоко", "quantity": 1, "category": "фрукты"})
    client.post("/api/items", json={"name": "Молоко", "quantity": 1, "category": "молочка"})
    monkeypatch.setattr("fridge.main.detect_image", lambda *_args, **_kwargs: [
        {"name": "Яблоко", "accepted": True, "quantity": 1, "unit": "шт", "confidence": 0.9, "bbox": {"x1": 1, "y1": 1, "x2": 10, "y2": 10}},
        {"name": "Апельсин", "accepted": True, "quantity": 1, "unit": "шт", "confidence": 0.8, "bbox": {"x1": 12, "y1": 1, "x2": 20, "y2": 10}},
    ])
    scan = client.post("/api/scan/upload", files={"file": ("fridge.jpg", _jpeg_bytes(), "image/jpeg")}).json()
    assert scan["sync"]["summary"]["kept"] == 1
    assert scan["sync"]["summary"]["added"] == 1
    assert scan["sync"]["summary"]["removed"] == 1
    milk_id = scan["sync"]["removed"][0]["id"]
    kept = scan["sync"]["kept"][0]
    added = scan["sync"]["added"][0]
    result = client.post(
        f"/api/scans/{scan['id']}/confirm",
        json={
            "mode": "sync",
            "remove_item_ids": [milk_id],
            "detections": [kept, added],
        },
    ).json()
    names = {item["name"] for item in client.get("/api/items").json()}
    assert names == {"Яблоко", "Апельсин"}
    assert len(result["removed"]) == 1
    assert len(result["created"]) == 1


def test_upload_scan_without_model(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("модель не установлена")

    monkeypatch.setattr("fridge.main.detect_image", boom)
    monkeypatch.setattr("fridge.main.enrich_detections", lambda _jpeg, dets: dets)
    res = client.post("/api/scan/upload", files={"file": ("fridge.jpg", _jpeg_bytes(), "image/jpeg")})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "upload"
    assert body["detections"] == []
    assert "модель" in (body["detect_error"] or "")
    assert client.get(body["image_url"]).status_code == 200


def test_health_version(client):
    body = client.get("/api/health").json()
    assert body["version"] == "0.3.0"


def test_learn_sample_and_status(client, tmp_path, monkeypatch):
    monkeypatch.setattr("fridge.dataset.DATASET_DIR", tmp_path)
    monkeypatch.setattr("fridge.dataset.SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr("fridge.learn.GALLERY_PATH", tmp_path / "gallery.npz")
    monkeypatch.setattr("fridge.main.train_gallery", lambda: {"samples": 1, "labels": 1})
    from fridge import learn as learn_mod

    learn_mod._GALLERY = None
    res = client.post(
        "/api/learn/sample",
        data={"label": "Йогурт"},
        files={"file": ("y.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert res.status_code == 200
    assert res.json()["sample"]["label"] == "Йогурт"
    stats = client.get("/api/learn/dataset").json()
    assert stats["total"] >= 1


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Полки" in res.text
    assert "Версия 3" in res.text
