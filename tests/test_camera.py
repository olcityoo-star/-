from fridge.camera import extract_jpeg, grab_raw_http_frame, wakeup_payload, wake_camera


def test_extract_jpeg_from_mjpeg_blob():
    jpeg = b"\xff\xd8" + b"hello" + b"\xff\xd9"
    blob = b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n--boundary--"
    assert extract_jpeg(blob) == jpeg


def test_extract_jpeg_missing():
    assert extract_jpeg(b"not an image") is None


def test_wakeup_payload_length():
    payload = wakeup_payload(0)
    assert len(payload) == 56
    assert b"99 bottles of beer on the wall" in payload


def test_wake_camera_returns_rtsp_wake(monkeypatch):
    monkeypatch.setattr(
        "fridge.camera._icmp_wakeup_only",
        lambda host, count=3: {"ok": True, "method": "system_ping", "host": host},
    )
    result = wake_camera({"camera_host": "192.168.100.1"})
    assert result["ok"] is True
    assert result["method"] == "rtsp_action_stream"
    assert "action=stream" in str(result["url"])


def test_candidate_urls_include_alternates():
    from fridge.camera import candidate_urls

    urls = candidate_urls({"camera_host": "192.168.42.1", "stream_url": "", "snapshot_url": ""})
    assert any(u.startswith("rtsp://192.168.42.1:8080/") for u in urls)
    assert any("192.168.42.1:8080/?action=stream" in u for u in urls)
    assert urls[0].startswith("http://") or "action=stream" in urls[0]
    assert len(urls) <= 60

    discovery = candidate_urls(
        {"camera_host": "192.168.100.1", "stream_url": "", "snapshot_url": ""},
        discovery=True,
        open_ports=[8080, 80],
    )
    assert any(u == "rtsp://192.168.100.1:8080/" for u in discovery)
    assert len(discovery) <= 60


def test_grab_raw_http_frame_reads_jpeg(monkeypatch):
    jpeg = b"\xff\xd8" + b"frame-bytes" + b"\xff\xd9"
    payload = b"HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace\r\n\r\n" + jpeg

    class FakeSock:
        def __init__(self, *_args, **_kwargs):
            self._data = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, *_args):
            return None

        def sendall(self, data):
            assert data.startswith(b"GET /?action=stream")

        def recv(self, _size):
            if not self._data:
                return b""
            out, self._data = self._data, b""
            return out

    monkeypatch.setattr("fridge.camera.socket.create_connection", lambda *_a, **_k: FakeSock())
    assert grab_raw_http_frame("http://192.168.100.1:8080/?action=stream") == jpeg
