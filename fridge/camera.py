from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

COMMON_HOSTS = (
    "192.168.100.1",
    "192.168.25.1",
    "192.168.42.1",
    "192.168.234.1",
    "192.168.1.1",
    "192.168.0.1",
)

SCAN_PORTS = (
    80,
    81,
    443,
    554,
    1935,
    3333,
    5000,
    8000,
    8080,
    8081,
    8082,
    8554,
    8888,
    9000,
)

COMMON_PORTS = (8080, 80, 8081, 81, 8000, 8888)

PRIORITY_PATHS = (
    "/?action=stream",
    "/?action=snapshot",
    "/snapshot.jpg",
    "/videostream.cgi",
    "/mjpeg",
    "/",
)

EXTRA_PATHS = (
    "/cgi-bin/snapshot.cgi",
    "/img/snapshot.cgi?size=3",
    "/video",
    "/live",
    "/stream",
    "/cgi-bin/guest/Video.cgi?media=JPEG",
)

COMMON_PATHS = PRIORITY_PATHS + EXTRA_PATHS


def _client(timeout: float = 1.2) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def extract_jpeg(buffer: bytes) -> bytes | None:
    start = buffer.find(JPEG_SOI)
    if start < 0:
        return None
    end = buffer.find(JPEG_EOI, start + 2)
    if end < 0:
        return None
    return buffer[start : end + 2]


def grab_mjpeg_frame(url: str, timeout: float = 2.0) -> bytes:
    with _client(timeout=timeout) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                chunks.extend(chunk)
                frame = extract_jpeg(bytes(chunks))
                if frame:
                    return frame
                if len(chunks) > 1_500_000:
                    break
    raise RuntimeError("Камера не отдала JPEG-кадр из потока")


def grab_url(url: str, timeout: float = 2.0) -> bytes:
    lowered = url.lower()
    stream_like = any(
        token in lowered for token in ("action=stream", "mjpg", "mjpeg", "/stream", "/video", "videostream")
    )
    if stream_like:
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


def _normalize_host(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        if parsed.port:
            return f"{host}:{parsed.port}"
        return host
    return value.rstrip("/")


def tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_open_ports(host: str, ports: tuple[int, ...] = SCAN_PORTS) -> list[int]:
    open_ports: list[int] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(tcp_open, host, port): port for port in ports}
        try:
            for future in as_completed(futures, timeout=6):
                port = futures[future]
                try:
                    if future.result():
                        open_ports.append(port)
                except Exception:  # noqa: BLE001
                    continue
        except TimeoutError:
            pass
    return sorted(open_ports)


def candidate_urls(
    settings: dict[str, str],
    *,
    discovery: bool = False,
    open_ports: list[int] | None = None,
) -> list[str]:
    urls: list[str] = []
    _add_url(urls, settings.get("snapshot_url") or "")
    _add_url(urls, settings.get("stream_url") or "")

    host = _normalize_host(settings.get("camera_host") or "")
    hostname = host.split(":")[0] if host else ""
    paths = PRIORITY_PATHS if discovery else COMMON_PATHS
    ports = open_ports or list(COMMON_PORTS)

    if host:
        if ":" in host and not open_ports:
            for path in paths:
                _add_url(urls, f"http://{host}{path}")
        elif hostname:
            for port in ports:
                if port in {554, 1935, 8554}:
                    continue
                for path in paths:
                    _add_url(urls, f"http://{hostname}:{port}{path}")

    if discovery and hostname:
        for other in COMMON_HOSTS[:2]:
            if other == hostname:
                continue
            for port in (8080, 80):
                for path in PRIORITY_PATHS[:3]:
                    _add_url(urls, f"http://{other}:{port}{path}")
        return urls[:50]

    return urls[:24]


def capture_snapshot(settings: dict[str, str]) -> bytes:
    errors: list[str] = []
    urls = candidate_urls(settings, discovery=False)
    last_error: Exception | None = None
    for url in urls:
        try:
            raw = grab_url(url, timeout=2.0)
            return as_jpeg(raw)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            errors.append(f"{url}: {exc}")

    detail = "; ".join(errors[:4]) if errors else "нет URL камеры"
    raise RuntimeError(
        "Не удалось получить снимок с ActionCam / GoPlus CamPro. "
        "Подключите Mac к Wi‑Fi камеры, укажите IP 192.168.100.1 "
        "и нажмите «Найти поток». "
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
    except Exception as exc:  # noqa: BLE001
        return {
            "online": False,
            "width": None,
            "height": None,
            "bytes": 0,
            "message": str(exc),
        }


def _probe_one(url: str) -> dict[str, object] | None:
    try:
        jpeg = as_jpeg(grab_url(url, timeout=1.0))
        with Image.open(BytesIO(jpeg)) as image:
            width, height = image.size
        parsed = urlparse(url)
        return {
            "url": url,
            "host": parsed.hostname,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "path": parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            "width": width,
            "height": height,
            "bytes": len(jpeg),
            "is_stream": any(token in url.lower() for token in ("stream", "mjpeg", "video")),
        }
    except Exception:  # noqa: BLE001
        return None


def discover_streams(settings: dict[str, str], limit: int = 5) -> dict[str, object]:
    """Scan open ports on camera IP, then probe HTTP paths in parallel."""
    host = _normalize_host(settings.get("camera_host") or "") or "192.168.100.1"
    hostname = host.split(":")[0]
    open_ports = scan_open_ports(hostname)
    urls = candidate_urls(settings, discovery=True, open_ports=open_ports or list(COMMON_PORTS))
    found: list[dict[str, object]] = []
    tried = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_probe_one, url): url for url in urls}
        try:
            for future in as_completed(futures, timeout=10):
                tried += 1
                hit = future.result()
                if hit:
                    found.append(hit)
                    if len(found) >= limit:
                        break
        except TimeoutError:
            pass
        finally:
            for future in futures:
                future.cancel()

    best = found[0] if found else None
    suggestion = None
    if best:
        host_name = best["host"]
        port = best["port"]
        stream = next((row for row in found if row["is_stream"]), best)
        snap = next((row for row in found if not row["is_stream"]), best)
        suggestion = {
            "camera_host": f"{host_name}:{port}" if port not in (80, None) else str(host_name),
            "stream_url": stream["url"],
            "snapshot_url": snap["url"],
        }

    if found:
        message = f"Найдено рабочих HTTP-адресов: {len(found)}. Открытые порты: {open_ports or '—'}"
    elif open_ports:
        message = (
            f"На {hostname} открыты порты {open_ports}, но HTTP MJPEG не найден. "
            "У этой GoPlus CamPro часто закрытый протокол приложения — "
            "тогда используйте «Загрузить фото», либо пришлите список портов."
        )
    else:
        message = (
            f"Хост {hostname} не отвечает ни на один порт. "
            "Проверьте, что Mac в Wi‑Fi ActionCam и камера не уснула."
        )

    return {
        "found": found,
        "tried": tried,
        "open_ports": open_ports,
        "host": hostname,
        "suggestion": suggestion,
        "message": message,
    }
