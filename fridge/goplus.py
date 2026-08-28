"""GoPlus / Generalplus action camera control (TCP :6666, libipcamera protocol)."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field

MAGIC = 0xABCD
LOGIN = 0x0110
LOGIN_ACCEPT = 0x0111
ALIVE_REQUEST = 0x0112
ALIVE_RESPONSE = 0x0113
DISCOVERY_REQUEST = 0x0114
START_PREVIEW = 0x01FF

DEFAULT_PORT = 6666
CONTROL_PORTS = (6666, 6668, 6688, 7777)
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "12345"
DISCOVERY_PORTS = (22600, 21600)


def pack_message(message_type: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HHI", MAGIC, len(payload), message_type) + payload


def login_payload(username: str, password: str) -> bytes:
    user = username.encode("ascii", errors="ignore")[:64].ljust(64, b"\x00")
    pwd = password.encode("ascii", errors="ignore")[:64].ljust(64, b"\x00")
    return user + pwd


@dataclass
class GoPlusSession:
    host: str
    port: int = DEFAULT_PORT
    username: str = DEFAULT_USERNAME
    password: str = DEFAULT_PASSWORD
    _sock: socket.socket | None = field(default=None, init=False, repr=False)
    _reader: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _logged_in: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _login_error: str | None = field(default=None, init=False, repr=False)

    def connect(self, timeout: float = 3.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self._sock.settimeout(1.0)
        self._stop.clear()
        self._logged_in.clear()
        self._reader = threading.Thread(target=self._read_loop, name="goplus-reader", daemon=True)
        self._reader.start()

    def _recv_exact(self, size: int) -> bytes:
        if not self._sock:
            raise ConnectionError("not connected")
        chunks = bytearray()
        while len(chunks) < size:
            piece = self._sock.recv(size - len(chunks))
            if not piece:
                raise ConnectionError("camera closed connection")
            chunks.extend(piece)
        return bytes(chunks)

    def _read_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                header = self._recv_exact(8)
                magic, length, msg_type = struct.unpack(">HHI", header)
                if magic != MAGIC:
                    continue
                payload = self._recv_exact(length) if length else b""
                if msg_type == ALIVE_REQUEST:
                    self._sock.sendall(pack_message(ALIVE_RESPONSE))
                elif msg_type == LOGIN_ACCEPT:
                    self._logged_in.set()
                elif msg_type == 0x1234:
                    self._login_error = "камера уже занята другим клиентом (GoPlus CamPro?)"
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break

    def login(self, timeout: float = 5.0) -> bool:
        if not self._sock:
            raise ConnectionError("not connected")
        self._sock.sendall(pack_message(LOGIN, login_payload(self.username, self.password)))
        if not self._logged_in.wait(timeout):
            return False
        if self._login_error:
            raise RuntimeError(self._login_error)
        return True

    def start_preview(self) -> None:
        if not self._sock:
            raise ConnectionError("not connected")
        self._sock.sendall(pack_message(START_PREVIEW))
        time.sleep(0.45)

    def activate(self) -> dict[str, object]:
        try:
            self.connect()
            if not self.login():
                return {"ok": False, "method": "tcp_6666", "host": self.host, "error": "login timeout"}
            self.start_preview()
            return {
                "ok": True,
                "method": "tcp_6666",
                "host": self.host,
                "port": self.port,
                "message": "preview started",
            }
        except OSError as exc:
            return {"ok": False, "method": "tcp_6666", "host": self.host, "error": str(exc)}
        except RuntimeError as exc:
            return {"ok": False, "method": "tcp_6666", "host": self.host, "error": str(exc)}

    def close(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def udp_discover(timeout: float = 2.0) -> dict[str, object]:
    packet = pack_message(DISCOVERY_REQUEST)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.bind(("", 0))
        for port in DISCOVERY_PORTS:
            try:
                sock.sendto(packet, ("255.255.255.255", port))
            except OSError:
                continue
        data, addr = sock.recvfrom(256)
        return {"ok": True, "method": "udp_discover", "host": addr[0], "bytes": len(data)}
    except OSError as exc:
        return {"ok": False, "method": "udp_discover", "error": str(exc)}
    finally:
        sock.close()


def start_preview_session(
    host: str,
    port: int | None = None,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
) -> tuple[GoPlusSession | None, dict[str, object]]:
    ports = (port,) if port is not None else CONTROL_PORTS
    last: dict[str, object] = {"ok": False, "method": "tcp_control", "host": host, "error": "no ports tried"}
    for candidate in ports:
        session = GoPlusSession(host, port=candidate, username=username, password=password)
        result = session.activate()
        result["port"] = candidate
        if result.get("ok"):
            return session, result
        last = result
        session.close()
    return None, last


def main() -> None:
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.100.1"
    print(f"Trying GoPlus TCP preview on {host} ports {CONTROL_PORTS} ...")
    session, result = start_preview_session(host)
    print(result)
    if session:
        print("Preview session active for 5s — try:")
        print(f"  curl -m 5 \"http://{host}:8080/?action=stream\" -o /tmp/cam.bin")
        time.sleep(5)
        session.close()
    else:
        print("TCP control недоступен на этой камере.")
        print("Запустите: python -m fridge.cam_diag", host)


if __name__ == "__main__":
    main()
