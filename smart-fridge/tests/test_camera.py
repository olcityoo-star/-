import io

import pytest
from PIL import Image

from app import demo_scene
from app.camera import (
    CameraError,
    DemoCamera,
    FileCamera,
    HttpSnapshotCamera,
    MjpegCamera,
    RtspCamera,
    _read_jpeg_from_stream,
    build_camera,
    decode_frame,
)


def jpeg_bytes(size=(64, 48), color=(10, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_build_camera_picks_transport_by_url():
    assert isinstance(build_camera("demo:"), DemoCamera)
    assert isinstance(build_camera(""), DemoCamera)
    assert isinstance(build_camera("http://192.168.1.50/snapshot.jpg"), HttpSnapshotCamera)
    assert isinstance(build_camera("https://cam.local/jpg"), HttpSnapshotCamera)
    assert isinstance(build_camera("mjpeg:http://192.168.1.50/video"), MjpegCamera)
    assert isinstance(build_camera("rtsp://user:pass@192.168.1.50:554/stream1"), RtspCamera)
    assert isinstance(build_camera("file:/tmp/frame.jpg"), FileCamera)


def test_build_camera_rejects_unknown_scheme():
    with pytest.raises(CameraError):
        build_camera("ftp://192.168.1.50/frame.jpg")


def test_mjpeg_prefix_is_stripped_from_url():
    camera = build_camera("mjpeg:http://192.168.1.50/video")
    assert camera.source == "http://192.168.1.50/video"


def test_decode_frame_reads_dimensions():
    frame = decode_frame(jpeg_bytes((320, 240)), "test")
    assert (frame.width, frame.height) == (320, 240)
    assert frame.jpeg.startswith(b"\xff\xd8")


def test_decode_frame_converts_png_to_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 0, 0)).save(buffer, format="PNG")
    frame = decode_frame(buffer.getvalue(), "test")
    assert frame.jpeg.startswith(b"\xff\xd8")


def test_decode_frame_rejects_html_error_page():
    with pytest.raises(CameraError):
        decode_frame(b"<html>401 Unauthorized</html>", "cam")
    with pytest.raises(CameraError):
        decode_frame(b"", "cam")


def test_mjpeg_stream_parser_extracts_first_frame():
    payload = jpeg_bytes()
    stream = [
        b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n",
        payload[:20],
        payload[20:],
        b"\r\n--boundary\r\n",
    ]
    assert _read_jpeg_from_stream(iter(stream)) == payload


def test_mjpeg_stream_parser_fails_on_truncated_stream():
    with pytest.raises(CameraError):
        _read_jpeg_from_stream(iter([b"\xff\xd8 no end marker"]))


def test_file_camera_reads_image(tmp_path):
    path = tmp_path / "frame.jpg"
    path.write_bytes(jpeg_bytes((100, 80)))
    frame = FileCamera(str(path)).capture()
    assert (frame.width, frame.height) == (100, 80)


def test_file_camera_reports_missing_file(tmp_path):
    with pytest.raises(CameraError):
        FileCamera(str(tmp_path / "nope.jpg")).capture()


def test_demo_camera_renders_scene_and_advances():
    demo_scene.reset()
    frame = DemoCamera().capture()
    assert (frame.width, frame.height) == (demo_scene.WIDTH, demo_scene.HEIGHT)
    first = demo_scene.current()
    DemoCamera().capture()
    assert demo_scene.current() != first
