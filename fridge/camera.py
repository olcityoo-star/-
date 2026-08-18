from __future__ import annotations

from io import BytesIO

import httpx
from PIL import Image

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

COMMON_PATHS = (
    "/?action=snapshot",
    "/?action=stream",
    "/snapshot.jpg",
    "/cgi-bin/snapshot.cgi",
    "/img/snapshot.cgi?size=3",
)


def _client(timeout: float = 4.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def extract_jpeg(buffer: bytes) -> bytes | None:
    start = buffer.find(JPEG_SOI)
    if start < 0:
        return None
    end = buffer.find(JPEG_EOI, start + 2)
    if end < 0:
        return None
    return buffer[start : end + 2]


def grab_mjpeg_frame(url: str, timeout: float = 6.0) -> bytes:
    with _client(timeout=timeout) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                chunks.extend(chunk)
                frame = extract_jpeg(bytes(chunks))
                if frame:
                    return frame
                if len(chunks) > 4_000_000:
                    break
    raise RuntimeError("Камера не отдала JPEG-кадр из потока")


def grab_url(url: str, timeout: float = 5.0) -> bytes:
    lowered = url.lower()
    if "action=stream" in lowered or "mjpg" in lowered or "mjpeg" in lowered:
        return grab_mjpeg_frame(url, timeout=timeout)
    with _client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.content
    if extract_jpeg(payload):
        jpeg = extract_jpeg(payload)
        if jpeg:
            return jpeg
    # Some firmwares ignore snapshot and still serve MJPEG.
    return grab_mjpeg_frame(url, timeout=timeout)


def as_jpeg(image_bytes: bytes, quality: int = 90) -> bytes:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    out = BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def _add_url(urls: list[str], url: str) -> None:
    url = (url or "").strip()
    if url and url not in urls:
        urls.append(url)


def capture_snapshot(settings: dict[str, str]) -> bytes:
    errors: list[str] = []
    urls: list[str] = []
    _add_url(urls, settings.get("snapshot_url") or "")
    _add_url(urls, settings.get("stream_url") or "")

    host = (settings.get("camera_host") or "").strip().rstrip("/")
    if host:
        base = host if host.startswith(("http://", "https://")) else f"http://{host}"
        for path in COMMON_PATHS:
            _add_url(urls, f"{base}{path}")

    last_error: Exception | None = None
    for url in urls:
        try:
            raw = grab_url(url)
            return as_jpeg(raw)
        except Exception as exc:  # noqa: BLE001 — collect probe errors for the UI
            last_error = exc
            errors.append(f"{url}: {exc}")

    detail = "; ".join(errors[:4]) if errors else "нет URL камеры"
    raise RuntimeError(
        "Не удалось получить снимок с ActionCam / GoPlus CamPro. "
        "Подключите сервер к Wi‑Fi камеры (SSID ActionCam_f8160c0282c2) "
        f"и проверьте адрес 192.168.25.1. {detail}"
    ) from last_error


def probe_camera(settings: dict[str, str]) -> dict[str, object]:
    try:
        jpeg = capture_snapshot(settings)
        with Image.open(BytesIO(jpeg)) as image:
            width, height = image.size
        return {
            "online": True,
            "width": width,
            "height": height,
            "bytes": len(jpeg),
            "message": "Камера отвечает, снимок получен",
        }
    except Exception as exc:  # noqa: BLE001 — status endpoint should not 500
        return {
            "online": False,
            "width": None,
            "height": None,
            "bytes": 0,
            "message": str(exc),
        }
