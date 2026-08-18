from app.camera import CameraError, build_camera_url, mask_url
from app.config import Settings


def test_mask_url_hides_credentials():
    assert "***" in mask_url("rtsp://admin:secret@192.168.1.8:554/stream1")
    assert "secret" not in mask_url("rtsp://admin:secret@192.168.1.8:554/stream1")


def test_build_camera_url_injects_user():
    settings = Settings(
        camera_url="rtsp://192.168.1.8:554/stream1",
        camera_user="admin",
        camera_password="pass word",
    )
    url = build_camera_url(settings)
    assert url.startswith("rtsp://admin:pass")
    assert "192.168.1.8:554/stream1" in url


def test_missing_url_raises():
    try:
        build_camera_url(Settings(camera_url=""))
    except CameraError as exc:
        assert "CAMERA_URL" in str(exc) or "камеры" in str(exc)
    else:
        raise AssertionError("expected CameraError")



def test_mask_url_hides_credentials():
    assert "***" in mask_url("rtsp://admin:secret@192.168.1.8:554/stream1")
    assert "secret" not in mask_url("rtsp://admin:secret@192.168.1.8:554/stream1")


def test_build_camera_url_injects_user():
    settings = Settings(
        camera_url="rtsp://192.168.1.8:554/stream1",
        camera_user="admin",
        camera_password="pass word",
    )
    url = build_camera_url(settings)
    assert url.startswith("rtsp://admin:pass")
    assert "192.168.1.8:554/stream1" in url
