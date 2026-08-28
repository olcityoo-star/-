from unittest.mock import MagicMock

import pytest

from fridge.goplus_rtsp import (
    _collect_udp_packets,
    _depayload_jpeg_rtp,
    _parse_rtsp_url,
    _pop_interleaved,
    _rtp_jpeg_from_packets,
)


def test_parse_rtsp_url_action_stream():
    host, port, path = _parse_rtsp_url("rtsp://192.168.100.1:8080/?action=stream")
    assert host == "192.168.100.1"
    assert port == 8080
    assert path == "/?action=stream"


def test_rtp_jpeg_single_packet():
    jpeg = b"\xff\xd8" + b"hello" + b"\xff\xd9"
    rtp = b"\x00" * 12 + jpeg
    assert _rtp_jpeg_from_packets([rtp]) == jpeg


def test_rtp_jpeg_rfc2435_first_fragment():
    jpeg = b"\xff\xd8" + b"frame" + b"\xff\xd9"
    header = bytes([0, 0, 0, 0, 0, 0, 0, 0])  # frag_offset=0, no Q tables
    rtp = b"\x80\x60" + b"\x00" * 10 + header + jpeg
    assert _rtp_jpeg_from_packets([rtp]) == jpeg


def test_depayload_finds_soi_after_header():
    jpeg = b"\xff\xd8" + b"x" + b"\xff\xd9"
    payload = bytes([0, 0, 0, 0, 1, 2, 3, 4]) + jpeg
    assert _depayload_jpeg_rtp(payload) == jpeg


def test_pop_interleaved_packet():
    jpeg = b"\xff\xd8" + b"ok" + b"\xff\xd9"
    rtp = b"\x00" * 12 + jpeg
    buf = bytearray(b"noise")
    buf.extend(b"$")
    buf.append(0)
    buf.extend(len(rtp).to_bytes(2, "big"))
    buf.extend(rtp)
    channel, packet = _pop_interleaved(buf)
    assert channel == 0
    assert packet == rtp
    assert not buf


def test_collect_udp_continues_after_timeout():
    import time

    jpeg = b"\xff\xd8" + b"delayed" + b"\xff\xd9"
    rtp = b"\x00" * 12 + jpeg
    sock = MagicMock()
    sock.recvfrom.side_effect = [TimeoutError(), (rtp, ("192.168.100.1", 5000))]
    frame = _collect_udp_packets(sock, time.time() + 2.0)
    assert frame == jpeg


def test_collect_udp_continues_after_socket_timeout():
    import socket
    import time

    jpeg = b"\xff\xd8" + b"delayed" + b"\xff\xd9"
    rtp = b"\x00" * 12 + jpeg
    sock = MagicMock()
    sock.recvfrom.side_effect = [socket.timeout(), (rtp, ("192.168.100.1", 5000))]
    frame = _collect_udp_packets(sock, time.time() + 2.0)
    assert frame == jpeg
