from __future__ import annotations

import os
import platform
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from contextlib import contextmanager
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
    80, 81, 443, 554, 1935, 3333, 5000, 6666, 8000, 8080, 8081, 8082, 8554, 8888, 9000,
    21600, 22600,
)

COMMON_PORTS = (8080, 8081, 8082, 80, 81, 8000, 8888, 554)

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

GENERALPLUS_PATHS = (
    "/?action=stream",
    "/?action=snapshot",
)

RTSP_PATHS = (
    "/?action=stream",
    "/",
    "/live",
    "/stream",
    "/h264",
    "/video",
    "/11",
    "/12",
    "/0",
    "/1",
    "/cam/realmonitor?channel=1&subtype=0",
    "/Streaming/Channels/101",
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


def wakeup_payload(sequence: int = 0) -> bytes:
    """Magic ICMP payload used by GoPlus / Generalplus apps."""
    number = 99 - (sequence % 100)
    payload = f"{number:28d} bottles of beer on the wall".encode("ascii")
    if len(payload) != 56:
        raise ValueError(f"unexpected wakeup payload length: {len(payload)}")
    return payload


def _icmp_checksum(packet: bytes) -> int:
    if len(packet) % 2:
        packet += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(packet) // 2), packet))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def _send_icmp_wakeup(host: str, count: int = 3) -> dict[str, object]:
    payload = wakeup_payload(0)
    ident = os.getpid() & 0xFFFF
    sent = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    try:
        sock.settimeout(1.5)
        for seq in range(count):
            header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
            checksum = _icmp_checksum(header + payload)
            header = struct.pack("!BBHHH", 8, 0, checksum, ident, seq)
            sock.sendto(header + payload, (host, 0))
            sent += 1
    finally:
        sock.close()
    return {"ok": True, "method": "raw_icmp", "sent": sent, "host": host}


def _ping_fallback(host: str, count: int = 3) -> dict[str, object]:
    args = ["ping", "-c", str(count), host]
    if platform.system() == "Darwin":
        args[1:1] = ["-W", "1000"]
    else:
        args[1:1] = ["-W", "1"]
    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            timeout=max(8, count * 3),
            text=True,
        )
        return {
            "ok": proc.returncode == 0,
            "method": "system_ping",
            "sent": count,
            "host": host,
            "note": "без magic payload — если не сработает, запустите сервер через sudo",
            "stderr": (proc.stderr or "")[:180],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "method": "system_ping", "host": host, "error": str(exc)}


def _icmp_wakeup_only(host: str, count: int = 3) -> dict[str, object]:
    try:
        return _send_icmp_wakeup(host, count=count)
    except PermissionError:
        return _ping_fallback(host, count=count)
    except OSError as exc:
        fallback = _ping_fallback(host, count=count)
        if fallback.get("ok"):
            fallback["icmp_error"] = str(exc)
            return fallback
        return {"ok": False, "method": "raw_icmp", "host": host, "error": str(exc)}


@contextmanager
def activated_camera(settings: dict[str, str]):
    host = _normalize_host(settings.get("camera_host") or "") or "192.168.100.1"
    hostname = host.split(":")[0]
    icmp = _icmp_wakeup_only(hostname)
    wake: dict[str, object] = {
        "ok": bool(icmp.get("ok")),
        "icmp": icmp,
        "host": hostname,
        "method": "rtsp_action_stream",
        "url": f"rtsp://{hostname}:8080/?action=stream",
    }
    if icmp.get("ok"):
        time.sleep(0.2)
    yield wake


def wake_camera(settings: dict[str, str], count: int = 3) -> dict[str, object]:
    with activated_camera(settings) as wake:
        wake["sent"] = count
        return wake


def _grab_rtsp_frame(url: str, timeout: float, transport: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="fridge-rtsp-") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", transport,
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
            "-i", url,
            "-map", "0:v:0",
            "-frames:v", "1",
            "-an",
            "-q:v", "2",
            "-y", str(out),
        ]
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)
        if not out.exists() or out.stat().st_size < 100:
            raise RuntimeError(f"RTSP не вернул кадр: {url}")
        return out.read_bytes()


def grab_rtsp_frame(url: str, timeout: float = 12.0) -> bytes:
    if not ffmpeg_available():
        if "action=stream" in url.lower():
            from fridge.goplus_rtsp import capture_jpeg

            return capture_jpeg(url, timeout=timeout)
        raise RuntimeError("Для RTSP нужен ffmpeg. На Mac: brew install ffmpeg")
    last_error: Exception | None = None
    for transport in ("tcp", "udp"):
        try:
            return _grab_rtsp_frame(url, timeout=timeout, transport=transport)
        except subprocess.TimeoutExpired:
            last_error = RuntimeError(f"RTSP таймаут ({transport}): {url}")
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", errors="replace")[:240]
            last_error = RuntimeError(f"RTSP ошибка ({transport}, {url}): {err or exc}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if "action=stream" in url.lower():
        try:
            from fridge.goplus_rtsp import capture_jpeg

            return capture_jpeg(url, timeout=timeout)
        except Exception as native_exc:  # noqa: BLE001
            last_error = native_exc
    raise last_error or RuntimeError(f"RTSP недоступен: {url}")


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


def _finalize_urls(urls: list[str], limit: int = 60, rtsp_keep: int = 12) -> list[str]:
    if len(urls) <= limit:
        return urls
    rtsp = [u for u in urls if u.lower().startswith("rtsp://")]
    http = [u for u in urls if not u.lower().startswith("rtsp://")]
    keep_rtsp = min(rtsp_keep, len(rtsp))
    keep_http = max(0, limit - keep_rtsp)
    merged: list[str] = []
    for url in http[:keep_http] + rtsp[:keep_rtsp]:
        _add_url(merged, url)
    return merged


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
    paths = GENERALPLUS_PATHS + (PRIORITY_PATHS if discovery else COMMON_PATHS)
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

    if hostname:
        for url in rtsp_candidate_urls(hostname, open_ports):
            _add_url(urls, url)

    if discovery and hostname:
        for other in COMMON_HOSTS[:2]:
            if other == hostname:
                continue
            for url in rtsp_candidate_urls(other, [8080, 554])[:6]:
                _add_url(urls, url)
        return _finalize_urls(urls, limit=60)

    return _finalize_urls(urls, limit=60)


def capture_snapshot(settings: dict[str, str]) -> bytes:
    with activated_camera(settings) as wake:
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
    tcp = wake.get("tcp") if isinstance(wake.get("tcp"), dict) else {}
    wake_note = ""
    if not tcp.get("ok"):
        wake_note = (
            " TCP control не ответил — к камере подключено только одно устройство: "
            "отключите телефон от Wi‑Fi ActionCam и закройте GoPlus CamPro."
        )
    tip = ""
    if not ffmpeg_available() and any(u.startswith("rtsp://") for u in urls):
        tip = " Для RTSP установите ffmpeg: brew install ffmpeg."
        raise RuntimeError(
        "Не удалось получить снимок с ActionCam / GoPlus CamPro. "
        "RTSP: rtsp://192.168.100.1:8080/?action=stream "
        f"(ffprobe уже показал mjpeg — проверьте URL в настройках).{wake_note}{tip} {detail}"
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

    with activated_camera(settings) as wake:
        open_ports = scan_open_ports(hostname)
        urls = candidate_urls(settings, discovery=True, open_ports=open_ports or [8080, 554, 6666])
        urls.sort(key=lambda u: (1 if u.lower().startswith("rtsp://") else 0, len(u)))
        found: list[dict[str, object]] = []
        tried = 0

        http_urls = [u for u in urls if not u.lower().startswith("rtsp://")][:24]
        rtsp_urls = [u for u in urls if u.lower().startswith("rtsp://")][:16]

        if http_urls:
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

        if len(found) < limit:
            for url in rtsp_urls:
                tried += 1
                hit = _probe_one(url)
                if hit:
                    found.append(hit)
                    if len(found) >= limit:
                        break

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
        tcp = wake.get("tcp") if isinstance(wake.get("tcp"), dict) else {}
        if tcp.get("ok"):
            wake_note = "TCP :6666 preview запущен."
        else:
            wake_note = (
                f"TCP control не ответил ({tcp.get('error', 'нет связи')}). "
                "К камере — одно устройство: отключите телефон от ActionCam Wi‑Fi."
            )
        message = (
            f"На {hostname} открыты порты {open_ports}. {wake_note} "
            "После preview поток обычно на "
            "http://192.168.100.1:8080/?action=stream."
        )
    else:
        message = f"Хост {hostname} не отвечает. Проверьте Wi‑Fi ActionCam."

    return {
        "found": found,
        "tried": tried,
        "open_ports": open_ports,
        "host": hostname,
        "ffmpeg": ffmpeg_available(),
        "wake": wake,
        "suggestion": suggestion,
        "message": message,
    }
