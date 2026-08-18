def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Внутри" in response.text
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detector"] == "demo"


def test_demo_scan_fills_inventory(client):
    response = client.post("/api/scans/demo")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "demo"
    assert payload["detections"]
    names = {item["name"] for item in payload["detections"]}
    assert "Молоко" in names

    state = client.get("/api/state").json()
    assert state["summary"]["inside"] >= 1
    assert state["last_scan"]["id"] == payload["id"]

    image = client.get(payload["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/jpeg")


def test_manual_item_and_settings(client):
    created = client.post("/api/items", json={"name": "Кефир", "count": 2})
    assert created.status_code == 200
    assert created.json()["emoji"]

    saved = client.post(
        "/api/settings",
        json={"detector": "demo", "camera_url": "http://192.168.0.50/snap.jpg"},
    )
    assert saved.status_code == 200
    assert saved.json()["camera_configured"] is True
    assert "192.168.0.50" in saved.json()["camera_url_masked"]
