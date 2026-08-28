import socket
import struct
import threading
import time

from fridge.goplus import (
    LOGIN,
    LOGIN_ACCEPT,
    MAGIC,
    START_PREVIEW,
    login_payload,
    pack_message,
    start_preview_session,
)


def _run_mock_camera(port: int, stop: threading.Event) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)
        server.settimeout(1.0)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except TimeoutError:
                continue
            with conn:
                header = conn.recv(8)
                _magic, length, msg_type = struct.unpack(">HHI", header)
                assert msg_type == LOGIN
                conn.recv(length)
                conn.sendall(pack_message(LOGIN_ACCEPT))
                header = conn.recv(8)
                _magic, length, msg_type = struct.unpack(">HHI", header)
                assert msg_type == START_PREVIEW
                if length:
                    conn.recv(length)
                while not stop.is_set():
                    time.sleep(0.05)


def test_pack_message_login():
    packet = pack_message(LOGIN, login_payload("admin", "12345"))
    magic, length, msg_type = struct.unpack(">HHI", packet[:8])
    assert magic == MAGIC
    assert msg_type == LOGIN
    assert length == 128
    assert packet[8:8 + 64].startswith(b"admin")


def test_start_preview_session():
    stop = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    thread = threading.Thread(target=_run_mock_camera, args=(port, stop), daemon=True)
    thread.start()
    time.sleep(0.05)
    session, result = start_preview_session("127.0.0.1", port=port)
    stop.set()
    assert result["ok"] is True
    assert session is not None
    session.close()
