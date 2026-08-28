"""GoPlus CamPro RTSP client (from PCAP: rtsp://host:8080/?action=stream)."""

from __future__ import annotations

import re
import socket
import time
from urllib.parse import urlparse

from fridge.camera import extract_jpeg

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


def _parse_rtsp_url(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "192.168.100.1"
    port = parsed.port or 8080
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if not path.startswith("/"):
        path = f"/{path}"
    return host, port, path


def _rtsp_exchange(
    sock: socket.socket,
    method: str,
    url: str,
    cseq: int,
    session: str | None = None,
    extra: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes, str | None]:
    lines = [
        f"{method} {url} RTSP/1.0",
        f"CSeq: {cseq}",
        "User-Agent: FridgeCam/1.0",
    ]
    if session:
        lines.append(f"Session: {session}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines.extend(["", ""])
    sock.sendall("\r\n".join(lines).encode("ascii"))
    sock.settimeout(timeout)
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            break
        raw.extend(chunk)
    text = bytes(raw).decode("latin1", errors="replace")
    status_line = text.split("\r\n", 1)[0]
    code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0
    header_blob, _, body_part = bytes(raw).partition(b"\r\n\r\n")
    headers: dict[str, str] = {}
    for line in header_blob.decode("latin1", errors="replace").split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    body = body_part
    need = int(headers.get("content-length", "0") or "0")
    while len(body) < need:
        body += sock.recv(need - len(body))
    new_session = headers.get("session", session)
    if new_session and ";" in new_session:
        new_session = new_session.split(";", 1)[0]
    return code, headers, body, new_session


def _parse_server_port(transport: str) -> tuple[int, int] | None:
    match = re.search(r"server_port=(\d+)-(\d+)", transport)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _depayload_jpeg_rtp(payload: bytes) -> bytes:
    if not payload:
        return b""
    if payload.startswith(JPEG_SOI):
        return payload
    if len(payload) < 8:
        return payload
    # RFC 2435 RTP/JPEG: 8-byte header, optional Q tables on first fragment only.
    frag_offset = (payload[1] << 16) | (payload[2] << 8) | payload[3]
    if frag_offset == 0:
        q_len = payload[4] + (payload[5] << 8)
        skip = 8 + q_len
        if skip <= len(payload):
            return payload[skip:]
    return payload[8:]


def _rtp_jpeg_from_packets(packets: list[bytes]) -> bytes | None:
    buffer = bytearray()
    for packet in packets:
        if len(packet) < 12:
            continue
        payload = packet[12:]
        if not payload:
            continue
        buffer.extend(_depayload_jpeg_rtp(payload))
        frame = extract_jpeg(bytes(buffer))
        if frame:
            return frame
    return extract_jpeg(bytes(buffer))


def capture_jpeg(url: str, timeout: float = 8.0) -> bytes:
    host, port, path = _parse_rtsp_url(url)
    base = f"rtsp://{host}:{port}{path}"
    play_base = base if base.endswith("/") else f"{base}/"

    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp_sock.bind(("0.0.0.0", 0))
    client_port = rtp_sock.getsockname()[1]
    rtp_sock.settimeout(0.5)

    tcp = socket.create_connection((host, port), timeout=timeout)
    try:
        cseq = 1
        code, _, _, session = _rtsp_exchange(tcp, "OPTIONS", base, cseq, timeout=timeout)
        if code >= 400:
            raise RuntimeError(f"RTSP OPTIONS {code}")

        cseq += 1
        code, _, _, session = _rtsp_exchange(
            tcp,
            "DESCRIBE",
            base,
            cseq,
            extra={"Accept": "application/sdp"},
            timeout=timeout,
        )
        if code >= 400:
            raise RuntimeError(f"RTSP DESCRIBE {code}")

        track0 = f"{base.rstrip('/')}/track0"
        cseq += 1
        code, setup_headers, _, session = _rtsp_exchange(
            tcp,
            "SETUP",
            track0,
            cseq,
            session=session,
            extra={
                "Transport": f"RTP/AVP/UDP;unicast;client_port={client_port}-{client_port + 1}",
            },
            timeout=timeout,
        )
        if code >= 400:
            raise RuntimeError(f"RTSP SETUP {code}")
        if not session:
            raise RuntimeError("RTSP SETUP без Session")

        cseq += 1
        code, _, _, session = _rtsp_exchange(
            tcp,
            "PLAY",
            play_base,
            cseq,
            session=session,
            extra={"Range": "npt=0.000-"},
            timeout=timeout,
        )
        if code >= 400:
            raise RuntimeError(f"RTSP PLAY {code}")

        packets: list[bytes] = []
        deadline = time.time() + timeout
        while time.time() < deadline and len(packets) < 80:
            try:
                data, _addr = rtp_sock.recvfrom(65535)
                if len(data) >= 12:
                    packets.append(data)
                    frame = _rtp_jpeg_from_packets(packets)
                    if frame:
                        return frame
            except TimeoutError:
                continue
            except OSError:
                break
        raise RuntimeError("RTSP/RTP не вернул JPEG-кадр")
    finally:
        tcp.close()
        rtp_sock.close()
