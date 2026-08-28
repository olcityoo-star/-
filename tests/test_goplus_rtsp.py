from fridge.goplus_rtsp import _parse_rtsp_url, _rtp_jpeg_from_packets


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
