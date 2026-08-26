from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
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
    80, 81, 443, 554, 1935, 3333, 5000, 8000, 8080, 8081, 8082, 8554, 8888, 9000,
)

COMMON_PORTS = (8080, 80, 8081, 81, 8000, 8888, 554)

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

RTSP_PATHS = (
    "/",
    "/live",
    "/stream",
    "/h264",
    "/video",
    "/cam/realmonitor?channel=1&subtype=0",
    "/Streaming/Channels/101",
    "/11",
    "/12",
    "/0",
    "/1",
)


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


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def grab_rtsp_frame(url: str, timeout: float = 8.0) -> bytes:
    if not ffmpeg_available():
        raise RuntimeError("Для RTSP нужен ffmpeg. На Mac: brew install ffmpeg")
    with tempfile.TemporaryDirectory(prefix="fridge-rtsp-") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", url,
            "-frames:v", "1",
            "-q:v", "2",
            "-y", str(out),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"RTSP таймаут: {url}") from exc
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"RTSP ошибка ({url}): {err or exc}") from exc
        if not out.exists() or out.stat().st_size < 100:
            raise RuntimeError(f"RTSP не вернул кадр: {url}")
        return out.read_bytes()


def grab_mjpeg_frame(url: str, timeout: float = 3.5) -> bytes:
    try:
        return grab_raw_http_frame(url, timeout=timeout)
    except Exception as raw_exc:  # noqa: BLE001
        try:
            with _client(timeout=timeout) as client:
                with client.stream("GET", url) as response:
                    chunks = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        chunks.extend(chunk)
                        frame = extract_jpeg(bytes(chunks))
                        if frame:
                            return frame
                        if len(chunks) > 1_500_000:
                            break
        except Exception as http_exc:  # noqa: BLE001
            raise RuntimeError(
                f"Камера не отдала JPEG-кадр из потока ({raw_exc}; {http_exc})"
            ) from http_exc
    raise RuntimeError("Камера не отдала JPEG-кадр из потока")


def grab_raw_http_frame(url: str, timeout: float = 3.5) -> bytes:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise RuntimeError("нет хоста в URL")
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request = (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        "User-Agent: FridgeCam/1.0\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        chunks = bytearray()
        while len(chunks) < 2_000_000:
            try:
                piece = sock.recv(4096)
            except socket.timeout as exc:
                if chunks:
                    break
                raise RuntimeError("таймаут чтения потока") from exc
            if not piece:
                break
            chunks.extend(piece)
            frame = extract_jpeg(bytes(chunks))
            if frame:
                return frame
    frame = extract_jpeg(bytes(chunks))
    if frame:
        return frame
    preview = bytes(chunks[:120]).decode("latin1", errors="replace")
    raise RuntimeError(f"в ответе нет JPEG (начало: {preview!r})")


def grab_url(url: str, timeout: float = 3.5) -> bytes:
    lowered = url.lower().strip()
    if lowered.startswith("rtsp://"):
        return grab_rtsp_frame(url, timeout=max(timeout, 8.0))
    stream_like = any(
        token in lowered
        for token in ("action=stream", "mjpg", "mjpeg", "/stream", "/video", "videostream")
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


def rtsp_candidate_urls(hostname: str, open_ports: list[int] | None = None) -> list[str]:
    preferred: list[int] = []
    source = open_ports if open_ports is not None else [8080, 554]
    for p in (8080, 554, 8554):
        if p in source and p not in preferred:
            preferred.append(p)
    if not preferred:
        preferred = [8080, 554]
    urls: list[str] = []
    for port in preferred:
        for path in RTSP_PATHS:
            _add_url(urls, f"rtsp://{hostname}:{port}{path}")
    return urls


def candidate_urls(
    settings: dict[str, str],
    *,
    discovery: bool = False,
    open_ports: list[int] | None = None,
) -> list[str]:
    urls: list[str] = []
    _add_url(urls, settings.get("stream_url") or "")
    _add_url(urls, settings.get("snapshot_url") or "")

    host = _normalize_host(settings.get("camera_host") or "")
    hostname = host.split(":")[0] if host else ""
    paths = PRIORITY_PATHS if discovery else COMMON_PATHS
    ports = open_ports or list(COMMON_PORTS)

    if hostname:
        for url in rtsp_candidate_urls(hostname, open_ports):
            _add_url(urls, url)

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
            for url in rtsp_candidate_urls(other, [8080, 554])[:6]:
                _add_url(urls, url)
        return urls[:60]

    return urls[:30]


def capture_snapshot(settings: dict[str, str]) -> bytes:
    errors: list[str] = []
    urls = candidate_urls(settings, discovery=False)
    last_error: Exception | None = None
    for url in urls:
        try:
            raw = grab_url(url, timeout=8.0 if url.lower().startswith("rtsp://") else 2.5)
            return as_jpeg(raw)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            errors.append(f"{url}: {exc}")

    detail = "; ".join(errors[:5]) if errors else "нет URL камеры"
    tip = ""
    if not ffmpeg_available() and any(u.startswith("rtsp://") for u in urls):
        tip = " Установите ffmpeg: brew install ffmpeg."
    raise RuntimeError(
        "Не удалось получить снимок с ActionCam / GoPlus CamPro. "
        "PCAPdroid показал RTSP на :8080 — укажите rtsp://192.168.100.1:8080/ "
        f"и нажмите «Найти поток».{tip} {detail}"
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
            "ffmpeg": ffmpeg_available(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "online": False,
            "width": None,
            "height": None,
            "bytes": 0,
            "message": str(exc),
            "ffmpeg": ffmpeg_available(),
        }


def _probe_one(url: str) -> dict[str, object] | None:
    try:
        timeout = 8.0 if url.lower().startswith("rtsp://") else 2.5
        jpeg = as_jpeg(grab_url(url, timeout=timeout))
        with Image.open(BytesIO(jpeg)) as image:
            width, height = image.size
        parsed = urlparse(url)
        return {
            "url": url,
            "host": parsed.hostname,
            "port": parsed.port or (554 if parsed.scheme == "rtsp" else 80),
            "path": parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            "width": width,
            "height": height,
            "bytes": len(jpeg),
            "is_stream": True,
            "protocol": parsed.scheme,
        }
    except Exception:  # noqa: BLE001
        return None


def discover_streams(settings: dict[str, str], limit: int = 4) -> dict[str, object]:
    host = _normalize_host(settings.get("camera_host") or "") or "192.168.100.1"
    hostname = host.split(":")[0]
    open_ports = scan_open_ports(hostname)
    urls = candidate_urls(settings, discovery=True, open_ports=open_ports or [8080, 554])
    urls.sort(key=lambda u: (0 if u.lower().startswith("rtsp://") else 1, len(u)))
    found: list[dict[str, object]] = []
    tried = 0

    rtsp_urls = [u for u in urls if u.lower().startswith("rtsp://")][:12]
    http_urls = [u for u in urls if not u.lower().startswith("rtsp://")][:20]

    for url in rtsp_urls:
        tried += 1
        hit = _probe_one(url)
        if hit:
            found.append(hit)
            if len(found) >= limit:
                break

    if len(found) < limit and http_urls:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_probe_one, url): url for url in http_urls}
            try:
                for future in as_completed(futures, timeout=8):
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
        suggestion = {
            "camera_host": f"{host_name}:{port}" if port not in (80, None) else str(host_name),
            "stream_url": best["url"],
            "snapshot_url": best["url"],
        }

    if found:
        message = (
            f"Найдено потоков: {len(found)} ({found[0]['protocol']}). "
            f"Открытые порты: {open_ports or '—'}"
        )
    elif open_ports:
        ff = "ffmpeg установлен" if ffmpeg_available() else "ffmpeg НЕ установлен (brew install ffmpeg)"
        message = (
            f"На {hostname} открыты порты {open_ports}. "
            f"HTTP пустой, пробуем RTSP — {ff}. "
            "Поставьте rtsp://192.168.100.1:8080/ и снова «Найти поток»."
        )
    else:
        message = f"Хост {hostname} не отвечает. Проверьте Wi‑Fi ActionCam."

    return {
        "found": found,
        "tried": tried,
        "open_ports": open_ports,
        "host": hostname,
        "ffmpeg": ffmpeg_available(),
        "suggestion": suggestion,
        "message": message,
    }
