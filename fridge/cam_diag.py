"""Quick diagnostics for GoPlus / ActionCam on local Wi‑Fi."""

from __future__ import annotations

import socket
import sys
import time

from fridge.camera import (
    _icmp_wakeup_only,
    grab_raw_http_frame,
    scan_open_ports,
    tcp_open,
)
from fridge.goplus import CONTROL_PORTS, start_preview_session

HTTP_STREAM = "http://{host}:8080/?action=stream"
HTTP_SNAPSHOT = "http://{host}:8080/?action=snapshot"


def _try_http_jpeg(host: str, path: str, timeout: float = 3.0) -> dict[str, object]:
    url = f"http://{host}{path}"
    try:
        data = grab_raw_http_frame(url, timeout=timeout)
        return {"ok": True, "url": url, "bytes": len(data)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def diagnose(host: str = "192.168.100.1") -> dict[str, object]:
    host = host.strip()
    print(f"Диагностика камеры {host}")
    print("=" * 40)
    print("Важно: к ActionCam одновременно подключается только ОДНО устройство.")
    print("Отключите Wi‑Fi ActionCam на телефоне и закройте GoPlus CamPro.")
    print()

    reachable = tcp_open(host, 8080, timeout=1.0)
    print(f"TCP :8080 доступен: {'да' if reachable else 'нет'}")
    if not reachable and not tcp_open(host, 80, timeout=1.0):
        print("Камера не отвечает. Подключите Mac к Wi‑Fi ActionCam.")
        return {"host": host, "reachable": False}

    open_ports = scan_open_ports(host)
    print(f"Открытые порты: {open_ports or '—'}")

    control_hits: list[dict[str, object]] = []
    for port in CONTROL_PORTS:
        if port in open_ports or tcp_open(host, port, timeout=0.6):
            _session, result = start_preview_session(host, port=port)
            control_hits.append({"port": port, **result})
            print(f"TCP :{port} preview: {result}")
        else:
            print(f"TCP :{port} preview: порт закрыт")

    icmp = _icmp_wakeup_only(host)
    print(f"ICMP wakeup: {icmp}")
    if icmp.get("ok"):
        time.sleep(0.3)

    http_stream = _try_http_jpeg(host, ":8080/?action=stream")
    print(f"HTTP stream: {http_stream}")
    http_snap = _try_http_jpeg(host, ":8080/?action=snapshot")
    print(f"HTTP snapshot: {http_snap}")

    if tcp_open(host, 80, timeout=0.6):
        http80 = _try_http_jpeg(host, "/", timeout=2.0)
        print(f"HTTP :80 /: {http80}")

    print()
    if http_stream.get("ok") or http_snap.get("ok"):
        print("OK: MJPEG работает. В интерфейсе нажмите «Найти поток».")
    elif any(item.get("ok") for item in control_hits):
        print("Preview через TCP отправлен, но MJPEG пустой — возможно, нужен RTSP.")
    else:
        print("Поток не стартовал с Mac.")
        print("Проверьте:")
        print("  1) Только Mac на Wi‑Fi ActionCam (телефон отключён от этой сети)")
        print("  2) GoPlus CamPro полностью закрыт на телефоне")
        print("  3) Повторите: python -m fridge.cam_diag", host)
        print("  4) Если снова пусто — пришлите вывод cam_diag (нужен RTSP URL из PCAPdroid)")

    return {
        "host": host,
        "open_ports": open_ports,
        "control": control_hits,
        "icmp": icmp,
        "http_stream": http_stream,
        "http_snapshot": http_snap,
    }


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.100.1"
    diagnose(host)


if __name__ == "__main__":
    main()
