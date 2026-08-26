from __future__ import annotations

from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

# Typical ActionCam / Generalplus / cheap Wi‑Fi cam endpoints.
COMMON_HOSTS = (
    "192.168.25.1",
    "192.168.42.1",
    "192.168.234.1",
    "192.168.1.1",
    "192.168.0.1",
    "192.168.2.1",
    "192.168.10.1",
)

COMMON_PORTS = (8080, 80, 8081, 81)

COMMON_PATHS = (
    "/?action=stream",
    "/?action=snapshot",
    "/snapshot.jpg",
    "/cgi-bin/snapshot.cgi",
    "/img/snapshot.cgi?size=3",
    "/videostream.cgi",
    "/mjpeg",
    "/video",
    "/live",
    "/stream",
    "/cgi-bin/guest/Video.cgi?media=JPEG",
    "/cgi-bin/hi3510/snap.cgi",
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
    if "action=stream" in lowered or "mjpg" in lowered or "mjpeg" in lowered or "/stream" in lowered or "/video" in lowered:
        return grab_mjpeg_frame(url, timeout=timeout)
    with _client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.content
    jpeg = extract_jpeg(payload)
    if jpeg:
        return jpeg
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


def _host_candidates(settings: dict[str, str]) -> list[str]:
    hosts: list[str] = []
    configured = (settings.get("camera_host") or "").strip()
    if configured:
        parsed = urlparse(configured if "://" in configured else f"http://{configured}")
        if parsed.hostname:
            hosts.append(parsed.hostname)
        elif configured:
            hosts.append(configured.split("/")[0].split(":")[0])
    for host in COMMON_HOSTS:
        if host not in hosts:
            hosts.append(host)
    return hosts


def candidate_urls(settings: dict[str, str]) -> list[str]:
    urls: list[str] = []
    _add_url(urls, settings.get("snapshot_url") or "")
    _add_url(urls, settings.get("stream_url") or "")

    host = (settings.get("camera_host") or "").strip().rstrip("/")
    if host:
        if host.startswith(("http://", "https://")):
            base = host
            for path in COMMON_PATHS:
                _add_url(urls, f"{base.rstrip('/')}{path}")
        else:
            # host may already include :port
            bare = host
            for path in COMMON_PATHS:
                _add_url(urls, f"http://{bare}{path}")

    for hostname in _host_candidates(settings):
        for port in COMMON_PORTS:
            base = f"http://{hostname}:{port}"
            for path in COMMON_PATHS:
                _add_url(urls, f"{base}{path}")
    return urls


def capture_snapshot(settings: dict[str, str]) -> bytes:
    errors: list[str] = []
    urls = candidate_urls(settings)
    last_error: Exception | None = None
    for url in urls:
        try:
            raw = grab_url(url)
            return as_jpeg(raw)
        except Exception as exc:  # noqa: BLE001 — collect probe errors for the UI
            last_error = exc
            errors.append(f"{url}: {exc}")

    detail = "; ".join(errors[:6]) if errors else "нет URL камеры"
    raise RuntimeError(
        "Не удалось получить снимок с ActionCam / GoPlus CamPro. "
        "Подключите Mac к Wi‑Fi камеры, затем нажмите «Найти поток» "
        "или укажите правильный URL вручную. "
        f"{detail}"
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


def discover_streams(settings: dict[str, str], limit: int = 8) -> dict[str, object]:
    """Try common ActionCam URLs and return the ones that return a JPEG frame."""
    found: list[dict[str, object]] = []
    tried = 0
    for url in candidate_urls(settings):
        if len(found) >= limit:
            break
        tried += 1
        try:
            jpeg = as_jpeg(grab_url(url, timeout=2.5))
            with Image.open(BytesIO(jpeg)) as image:
                width, height = image.size
            parsed = urlparse(url)
            found.append(
                {
                    "url": url,
                    "host": parsed.hostname,
                    "port": parsed.port or (443 if parsed.scheme == "https" else 80),
                    "path": parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                    "width": width,
                    "height": height,
                    "bytes": len(jpeg),
                    "is_stream": "stream" in url.lower() or "mjpeg" in url.lower() or "video" in url.lower(),
                }
            )
        except Exception:  # noqa: BLE001 — discovery should continue
            continue

    best = found[0] if found else None
    suggestion = None
    if best:
        host = best["host"]
        port = best["port"]
        stream = next((row for row in found if row["is_stream"]), best)
        snap = next((row for row in found if not row["is_stream"]), best)
        suggestion = {
            "camera_host": f"{host}:{port}" if port not in (80, None) else str(host),
            "stream_url": stream["url"],
            "snapshot_url": snap["url"],
        }

    return {
        "found": found,
        "tried": tried,
        "suggestion": suggestion,
        "message": (
            f"Найдено рабочих адресов: {len(found)}"
            if found
            else "Поток не найден. Подключите Mac к Wi‑Fi камеры ActionCam_… и повторите."
        ),
    }
