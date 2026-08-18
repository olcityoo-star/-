import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import demo_scene
from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    demo_scene.reset()
    settings = Settings(
        camera_url="demo:",
        detector="demo",
        scan_interval=0,
        confirmations=1,
        data_dir=tmp_path,
        keep_snapshots=3,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_status_before_first_scan(client):
    body = client.get("/api/status").json()
    assert body["detector"]["name"] == "demo"
    assert body["camera"]["source"] == "demo:"
    assert body["snapshot"] is None
    assert body["auto_scan"] is False


def test_scan_fills_inventory(client):
    scan = client.post("/api/scan").json()
    assert scan["ok"] is True
    assert scan["detections"]

    inventory = client.get("/api/inventory").json()
    keys = {item["key"] for item in inventory["items"]}
    assert {"milk", "eggs", "tomato"} <= keys
    assert inventory["total"] >= len(keys)

    tomato = next(item for item in inventory["items"] if item["key"] == "tomato")
    assert tomato["count"] == 2  # в демо-сцене два помидора
    assert inventory["groups"]


def test_events_appear_after_scan(client):
    client.post("/api/scan")
    events = client.get("/api/events?limit=100").json()["events"]
    assert {event["kind"] for event in events} == {"added"}
    assert any(event["label"] == "Молоко" for event in events)


def test_second_scan_records_removal(client):
    client.post("/api/scan")  # сцена 1: есть сыр
    client.post("/api/scan")  # сцена 2: сыр убрали, появился йогурт
    inventory = client.get("/api/inventory").json()
    keys = {item["key"] for item in inventory["items"]}
    assert "cheese" not in keys
    assert "yogurt" in keys

    kinds = {(event["key"], event["kind"]) for event in client.get("/api/events").json()["events"]}
    assert ("cheese", "removed") in kinds
    assert ("yogurt", "added") in kinds


def test_snapshot_endpoint_serves_last_frame(client):
    assert client.get("/api/snapshot.jpg").status_code == 404
    client.post("/api/scan")
    response = client.get("/api/snapshot.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (demo_scene.WIDTH, demo_scene.HEIGHT)


def test_status_exposes_boxes_for_overlay(client):
    client.post("/api/scan")
    snapshot = client.get("/api/status").json()["snapshot"]
    assert snapshot["width"] == demo_scene.WIDTH
    box = snapshot["detections"][0]["box"]
    assert len(box) == 4
    assert all(0.0 <= value <= 1.0 for value in box)


def test_manual_adjustment(client):
    client.post("/api/scan")
    response = client.post("/api/items/cheese", json={"count": 4})
    assert response.json()["change"]["count"] == 4

    item = next(row for row in client.get("/api/inventory").json()["items"] if row["key"] == "cheese")
    assert item["count"] == 4
    assert item["manual"] is True

    client.post("/api/items/cheese", json={"count": 0})
    keys = {row["key"] for row in client.get("/api/inventory").json()["items"]}
    assert "cheese" not in keys


def test_manual_adjustment_validates_input(client):
    assert client.post("/api/items/milk", json={"count": "много"}).status_code == 400


def test_upload_endpoint_accepts_pushed_frame(client):
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), (30, 30, 30)).save(buffer, format="JPEG")
    response = client.post(
        "/api/scan/upload",
        files={"file": ("frame.jpg", buffer.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_upload_endpoint_rejects_garbage(client):
    response = client.post("/api/scan/upload", files={"file": ("x.txt", b"not an image", "text/plain")})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_shopping_list_is_returned(client):
    client.post("/api/scan")
    reasons = {row["key"]: row["reason"] for row in client.get("/api/inventory").json()["shopping_list"]}
    assert reasons["bread"] == "закончился"  # хлеба в демо-сцене нет
    assert "milk" not in reasons


def test_old_snapshots_are_pruned(client, tmp_path):
    for _ in range(5):
        client.post("/api/scan")
    assert len(list((tmp_path / "snapshots").glob("*.jpg"))) == 3


def test_camera_failure_returns_502(tmp_path):
    settings = Settings(camera_url="file:/nonexistent/frame.jpg", detector="demo", scan_interval=0, data_dir=tmp_path)
    with TestClient(create_app(settings)) as failing_client:
        response = failing_client.post("/api/scan")
        assert response.status_code == 502
        assert "камера" in response.json()["error"]
        assert failing_client.get("/api/status").json()["last_error"]


def test_web_app_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Умный холодильник" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_catalog_endpoint(client):
    catalog = client.get("/api/catalog").json()
    assert "dairy" in catalog["categories"]
    assert any(product["key"] == "milk" for product in catalog["products"])
