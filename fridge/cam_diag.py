"""Quick diagnostics for GoPlus / ActionCam on local Wi‑Fi."""

from __future__ import annotations

import sys
import time

from fridge.camera import (
    _icmp_wakeup_only,
    _send_icmp_wakeup,
    ffmpeg_available,
    grab_raw_http_frame,
    grab_rtsp_frame,
    scan_open_ports,
    tcp_open,
)
from fridge.goplus import CONTROL_PORTS, start_preview_session

HTTP_PATHS = ("/?action=stream", "/?action=snapshot", "/")
RTSP_PATHS = ("/?action=stream", "/", "/live", "/stream")


def _try_http_jpeg(host: str, port: int, path: str, timeout: float = 3.0) -> dict[str, object]:
    url = f"http://{host}:{port}{path}"
    try:
        data = grab_raw_http_frame(url, timeout=timeout)
        return {"ok": True, "url": url, "bytes": len(data)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def _try_rtsp(host: str, port: int, path: str, timeout: float = 6.0) -> dict[str, object]:
    url = f"rtsp://{host}:{port}{path}"
    try:
        data = grab_rtsp_frame(url, timeout=timeout)
        return {"ok": True, "url": url, "bytes": len(data)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def _probe_http_ports(host: str, ports: list[int]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for port in ports:
        if port in {554, 6666, 6668, 6688, 7777}:
            continue
        for path in HTTP_PATHS:
            result = _try_http_jpeg(host, port, path)
            print(f"HTTP :{port}{path}: {result}")
            if result.get("ok"):
                hits.append(result)
    return hits


def _probe_rtsp_ports(host: str, ports: list[int]) -> list[dict[str, object]]:
    if not ffmpeg_available():
        print("RTSP: ffmpeg не установлен (brew install ffmpeg)")
        return []
    hits: list[dict[str, object]] = []
    for port in ports:
        for path in RTSP_PATHS:
            result = _try_rtsp(host, port, path, timeout=5.0)
            status = "OK" if result.get("ok") else result.get("error", "")[:80]
            print(f"RTSP :{port}{path}: {status}")
            if result.get("ok"):
                hits.append(result)
    return hits


def diagnose(host: str = "192.168.100.1") -> dict[str, object]:
    host = host.strip()
    print(f"Диагностика камеры {host}")
    print("=" * 40)
    print("К ActionCam — только одно устройство. Телефон отключён от ActionCam Wi‑Fi.")
    print()

    open_ports = scan_open_ports(host)
    print(f"Открытые порты: {open_ports or '—'}")
    if not open_ports:
        print("Камера не отвечает. Подключите Mac к Wi‑Fi ActionCam.")
        return {"host": host, "reachable": False, "open_ports": []}

    http_ports = [p for p in open_ports if p not in {554, 1935, 8554}] or [8080, 8081, 8082]

    control_hits: list[dict[str, object]] = []
    for port in CONTROL_PORTS:
        if port in open_ports:
            _session, result = start_preview_session(host, port=port)
            control_hits.append({"port": port, **result})
            print(f"TCP :{port} preview: {result}")

    icmp = _icmp_wakeup_only(host)
    print(f"ICMP ping: {icmp}")
    magic: dict[str, object] | None = None
    try:
        magic = _send_icmp_wakeup(host)
        print(f"ICMP magic: {magic}")
    except PermissionError:
        print("ICMP magic: нужен sudo — см. README")
    if icmp.get("ok") or (magic and magic.get("ok")):
        time.sleep(0.35)

    print("\n--- HTTP MJPEG ---")
    http_hits = _probe_http_ports(host, http_ports)

    print("\n--- RTSP ---")
    rtsp_hits = _probe_rtsp_ports(host, http_ports)

    print()
    if http_hits:
        print(f"OK MJPEG: {http_hits[0]['url']}")
        print("В интерфейсе укажите этот URL и нажмите «Найти поток».")
    elif rtsp_hits:
        print(f"OK RTSP: {rtsp_hits[0]['url']}")
        print("В настройках укажите этот RTSP URL.")
    else:
        print("Поток не найден на портах", http_ports)
        print("Попробуйте на домашнем Wi‑Fi (до ActionCam):")
        print("  sudo python3 -c \"from fridge.camera import _send_icmp_wakeup; print(_send_icmp_wakeup('192.168.100.1'))\"")
        print("затем снова cam_diag на ActionCam Wi‑Fi.")
        print("Или пришлите RTSP URL из PCAPdroid, когда preview шёл с телефона.")

    return {
        "host": host,
        "open_ports": open_ports,
        "control": control_hits,
        "icmp": icmp,
        "magic_icmp": magic,
        "http_hits": http_hits,
        "rtsp_hits": rtsp_hits,
    }


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.100.1"
    diagnose(host)


if __name__ == "__main__":
    main()
