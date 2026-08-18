from fridge.camera import extract_jpeg


def test_extract_jpeg_from_mjpeg_blob():
    jpeg = b"\xff\xd8" + b"hello" + b"\xff\xd9"
    blob = b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n--boundary--"
    assert extract_jpeg(blob) == jpeg


def test_extract_jpeg_missing():
    assert extract_jpeg(b"not an image") is None
