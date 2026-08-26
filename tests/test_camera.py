from fridge.camera import extract_jpeg


def test_extract_jpeg_from_mjpeg_blob():
    jpeg = b"\xff\xd8" + b"hello" + b"\xff\xd9"
    blob = b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n--boundary--"
    assert extract_jpeg(blob) == jpeg


def test_extract_jpeg_missing():
    assert extract_jpeg(b"not an image") is None


def test_candidate_urls_include_alternates():
    from fridge.camera import candidate_urls

    urls = candidate_urls({"camera_host": "192.168.42.1", "stream_url": "", "snapshot_url": ""})
    assert any("192.168.42.1:8080/?action=stream" in u for u in urls)
    assert len(urls) <= 24

    discovery = candidate_urls(
        {"camera_host": "192.168.100.1", "stream_url": "", "snapshot_url": ""},
        discovery=True,
    )
    assert any("192.168.100.1:8080/?action=stream" in u for u in discovery)
    assert len(discovery) <= 40
