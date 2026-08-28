from fridge.goplus_rtsp import _parse_rtsp_url


def test_parse_rtsp_url_action_stream():
    host, port, path = _parse_rtsp_url("rtsp://192.168.100.1:8080/?action=stream")
    assert host == "192.168.100.1"
    assert port == 8080
    assert path == "/?action=stream"
