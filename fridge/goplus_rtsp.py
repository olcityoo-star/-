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
    frag_offset = (payload[1] << 16) | (payload[2] << 8) | payload[3]
    if frag_offset > 0:
        chunk = payload[8:]
    else:
        chunk = payload[8:]
    if chunk.startswith(JPEG_SOI):
        return chunk
    soi = payload.find(JPEG_SOI)
    if soi >= 0:
        return payload[soi:]
    return chunk


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


def _collect_udp_packets(
    rtp_sock: socket.socket,
    deadline: float,
    existing: list[bytes] | None = None,
) -> bytes | None:
    packets = list(existing or [])
    rtp_sock.settimeout(0.5)
    while time.time() < deadline and len(packets) < 200:
        try:
            data, _addr = rtp_sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        if len(data) >= 12:
            packets.append(data)
            frame = _rtp_jpeg_from_packets(packets)
            if frame:
                return frame
    return _rtp_jpeg_from_packets(packets)


def _pop_interleaved(buf: bytearray) -> tuple[int, bytes] | None:
    idx = buf.find(b"$")
    if idx < 0:
        if len(buf) > 65536:
            buf.clear()
        return None
    if idx > 0:
        del buf[:idx]
    if len(buf) < 4:
        return None
    channel = buf[1]
    size = (buf[2] << 8) | buf[3]
    if len(buf) < 4 + size:
        return None
    packet = bytes(buf[4 : 4 + size])
    del buf[: 4 + size]
    return channel, packet


def _collect_interleaved_packets(
    sock: socket.socket,
    deadline: float,
    buffer: bytearray | None = None,
) -> bytes | None:
    packets: list[bytes] = []
    pending = buffer if buffer is not None else bytearray()
    sock.settimeout(0.5)
    while time.time() < deadline and len(packets) < 200:
        try:
            pending.extend(sock.recv(4096))
        except socket.timeout:
            pass
        except OSError:
            break
        while True:
            parsed = _pop_interleaved(pending)
            if parsed is None:
                break
            _channel, rtp = parsed
            if len(rtp) >= 12:
                packets.append(rtp)
                frame = _rtp_jpeg_from_packets(packets)
                if frame:
                    return frame
    return _rtp_jpeg_from_packets(packets)


def _rtsp_session(
    host: str,
    port: int,
    base: str,
    timeout: float,
    transport_mode: str,
) -> tuple[socket.socket, socket.socket | None, str, int]:
    """Return (control_socket, udp_socket_or_none, session_id, next_cseq)."""
    udp_sock: socket.socket | None = None
    client_port = 0
    if transport_mode == "udp":
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind(("0.0.0.0", 0))
        client_port = udp_sock.getsockname()[1]

    tcp = socket.create_connection((host, port), timeout=timeout)
    cseq = 1
    code, _, _, session = _rtsp_exchange(tcp, "OPTIONS", base, cseq, timeout=timeout)
    if code >= 400:
        tcp.close()
        if udp_sock:
            udp_sock.close()
        raise RuntimeError(f"RTSP OPTIONS {code}")

    cseq += 1
    code, _, sdp, session = _rtsp_exchange(
        tcp,
        "DESCRIBE",
        base,
        cseq,
        extra={"Accept": "application/sdp"},
        timeout=timeout,
    )
    if code >= 400:
        tcp.close()
        if udp_sock:
            udp_sock.close()
        raise RuntimeError(f"RTSP DESCRIBE {code}")

    tracks = re.findall(r"a=control:(\S+)", sdp.decode("latin1", errors="replace"))
    if not tracks:
        tracks = ["track0", "track1"]
    interleaved = 0

    for track in tracks[:2]:
        track_url = track if track.startswith("rtsp://") else f"{base.rstrip('/')}/{track.lstrip('/')}"
        if transport_mode == "udp":
            transport = f"RTP/AVP/UDP;unicast;client_port={client_port}-{client_port + 1}"
        else:
            transport = f"RTP/AVP/TCP;unicast;interleaved={interleaved}-{interleaved + 1}"
            interleaved += 2
        cseq += 1
        code, setup_headers, _, session = _rtsp_exchange(
            tcp,
            "SETUP",
            track_url,
            cseq,
            session=session,
            extra={"Transport": transport},
            timeout=timeout,
        )
        if code >= 400:
            tcp.close()
            if udp_sock:
                udp_sock.close()
            raise RuntimeError(f"RTSP SETUP {track} {code} ({transport_mode})")
        if not session:
            tcp.close()
            if udp_sock:
                udp_sock.close()
            raise RuntimeError("RTSP SETUP без Session")

    return tcp, udp_sock, session or "", cseq + 1


def _capture_udp(url: str, timeout: float) -> bytes:
    host, port, path = _parse_rtsp_url(url)
    base = f"rtsp://{host}:{port}{path}"
    tcp, udp_sock, session, cseq = _rtsp_session(host, port, base, timeout, "udp")
    assert udp_sock is not None
    try:
        code, _, _, session = _rtsp_exchange(
            tcp,
            "PLAY",
            base,
            cseq,
            session=session,
            extra={"Range": "npt=0.000-"},
            timeout=timeout,
        )
        if code >= 400:
            raise RuntimeError(f"RTSP PLAY {code} (udp)")
        frame = _collect_udp_packets(udp_sock, time.time() + timeout)
        if frame:
            return frame
        raise RuntimeError("RTSP/RTP (UDP) не вернул JPEG-кадр")
    finally:
        try:
            _rtsp_exchange(tcp, "TEARDOWN", base, cseq + 1, session=session, timeout=2.0)
        except OSError:
            pass
        tcp.close()
        udp_sock.close()


def _capture_interleaved(url: str, timeout: float) -> bytes:
    host, port, path = _parse_rtsp_url(url)
    base = f"rtsp://{host}:{port}{path}"
    tcp, _udp_sock, session, cseq = _rtsp_session(host, port, base, timeout, "tcp")
    try:
        code, _, _, session = _rtsp_exchange(
            tcp,
            "PLAY",
            base,
            cseq,
            session=session,
            extra={"Range": "npt=0.000-"},
            timeout=timeout,
        )
        if code >= 400:
            raise RuntimeError(f"RTSP PLAY {code} (tcp/interleaved)")
        frame = _collect_interleaved_packets(tcp, time.time() + timeout)
        if frame:
            return frame
        raise RuntimeError("RTSP/RTP (TCP interleaved) не вернул JPEG-кадр")
    finally:
        try:
            _rtsp_exchange(tcp, "TEARDOWN", base, cseq + 1, session=session, timeout=2.0)
        except OSError:
            pass
        tcp.close()


def capture_jpeg(url: str, timeout: float = 15.0) -> bytes:
    errors: list[str] = []
    for method in (_capture_udp, _capture_interleaved):
        try:
            frame = method(url, timeout=timeout)
            if frame and len(frame) >= 100:
                return frame
            errors.append(f"{method.__name__}: слишком маленький кадр")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{method.__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "RTSP/RTP не вернул JPEG-кадр")
