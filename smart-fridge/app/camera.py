"""Получение кадра с Wi-Fi камеры.

Поддерживаются три способа, которые умеют почти все бытовые камеры:

* ``http://.../snapshot.jpg`` — одиночный JPEG по HTTP (самый надёжный вариант);
* ``mjpeg:http://.../video`` — поток MJPEG, из которого берётся один кадр;
* ``rtsp://...`` — RTSP-поток (нужен OpenCV, ставится отдельно).

Плюс ``demo:`` и ``file:/path/to.jpg`` для запуска без камеры.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from PIL import Image

from . import demo_scene

MJPEG_PREFIX = "mjpeg:"
DEMO_PREFIX = "demo:"
FILE_PREFIX = "file:"
#: Больше 4 МиБ один кадр бытовой камеры не занимает; ограничение защищает
#: от зависания на бесконечном потоке, отданном вместо снимка.
MAX_FRAME_BYTES = 4 * 1024 * 1024


class CameraError(RuntimeError):
    """Кадр получить не удалось."""


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int
    height: int
    ts: float
    source: str


class Camera(Protocol):
    source: str

    def capture(self) -> Frame: ...


def decode_frame(jpeg: bytes, source: str) -> Frame:
    """Проверяет, что это действительно JPEG, и достаёт размеры кадра."""
    if not jpeg:
        raise CameraError(f"{source}: камера вернула пустой кадр")
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
            if image.format != "JPEG":
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=85)
                jpeg = buffer.getvalue()
    except OSError as exc:  # не изображение вовсе
        raise CameraError(f"{source}: ответ камеры не является изображением ({exc})") from exc
    return Frame(jpeg=jpeg, width=width, height=height, ts=time.time(), source=source)


class HttpSnapshotCamera:
    """Одиночный JPEG по HTTP. Понимает Basic- и Digest-авторизацию."""

    def __init__(self, url: str, username: str = "", password: str = "", timeout: float = 10.0) -> None:
        self.source = url
        self.username = username
        self.password = password
        self.timeout = timeout

    def _auth(self):
        import requests

        if not self.username:
            return None
        # Большинство камер (Hikvision, Dahua, TP-Link) требуют Digest;
        # Basic пробуем как запасной вариант при 401.
        return requests.auth.HTTPDigestAuth(self.username, self.password)

    def capture(self) -> Frame:
        import requests

        try:
            response = requests.get(self.source, auth=self._auth(), timeout=self.timeout)
            if response.status_code == 401 and self.username:
                response = requests.get(
                    self.source,
                    auth=requests.auth.HTTPBasicAuth(self.username, self.password),
                    timeout=self.timeout,
                )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CameraError(f"{self.source}: {exc}") from exc
        return decode_frame(response.content, self.source)


class MjpegCamera:
    """Достаёт один кадр из непрерывного MJPEG-потока."""

    def __init__(self, url: str, username: str = "", password: str = "", timeout: float = 10.0) -> None:
        self.source = url
        self.username = username
        self.password = password
        self.timeout = timeout

    def capture(self) -> Frame:
        import requests

        auth = None
        if self.username:
            auth = requests.auth.HTTPDigestAuth(self.username, self.password)
        try:
            with requests.get(self.source, auth=auth, timeout=self.timeout, stream=True) as response:
                response.raise_for_status()
                jpeg = _read_jpeg_from_stream(response.iter_content(chunk_size=4096))
        except requests.RequestException as exc:
            raise CameraError(f"{self.source}: {exc}") from exc
        return decode_frame(jpeg, self.source)


def _read_jpeg_from_stream(chunks) -> bytes:
    """Ищет в потоке границы JPEG (SOI ff d8 ... EOI ff d9) и отдаёт первый кадр."""
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        start = buffer.find(b"\xff\xd8")
        if start == -1:
            if len(buffer) > MAX_FRAME_BYTES:
                del buffer[:-2]
            continue
        end = buffer.find(b"\xff\xd9", start + 2)
        if end != -1:
            return bytes(buffer[start : end + 2])
        if len(buffer) > MAX_FRAME_BYTES:
            raise CameraError("MJPEG: кадр не закончился в пределах разумного размера")
    raise CameraError("MJPEG: поток закончился, кадр не найден")


class RtspCamera:
    """RTSP через OpenCV.

    Соединение каждый раз открывается заново: холодильник снимается редко,
    а постоянно висящий поток съедает трафик и часто отваливается по таймауту.
    """

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.source = url
        self.timeout = timeout

    def capture(self) -> Frame:
        try:
            import cv2  # noqa: PLC0415 — тяжёлая зависимость, импортируем по требованию
        except ImportError as exc:
            raise CameraError(
                "для RTSP нужен OpenCV: pip install opencv-python-headless"
            ) from exc

        capture = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        try:
            if not capture.isOpened():
                raise CameraError(f"{self.source}: не удалось открыть поток")
            # Первые кадры часто битые, пока декодер не поймал ключевой кадр.
            frame = None
            deadline = time.time() + self.timeout
            for _ in range(5):
                ok, candidate = capture.read()
                if ok and candidate is not None:
                    frame = candidate
                if time.time() > deadline:
                    break
            if frame is None:
                raise CameraError(f"{self.source}: поток открыт, но кадр не пришёл")
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                raise CameraError(f"{self.source}: не удалось закодировать кадр в JPEG")
            return decode_frame(encoded.tobytes(), self.source)
        finally:
            capture.release()


class FileCamera:
    """Читает кадр из файла — удобно для отладки на записанных снимках."""

    def __init__(self, path: str) -> None:
        self.source = f"{FILE_PREFIX}{path}"
        self.path = Path(path)

    def capture(self) -> Frame:
        if not self.path.exists():
            raise CameraError(f"файл не найден: {self.path}")
        return decode_frame(self.path.read_bytes(), self.source)


class DemoCamera:
    """Рисует синтетический кадр холодильника."""

    source = "demo:"

    def capture(self) -> Frame:
        return decode_frame(demo_scene.render(demo_scene.advance()), self.source)


def build_camera(
    source: str,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
) -> Camera:
    """Создаёт камеру по строке подключения."""
    source = source.strip()
    if not source or source.startswith(DEMO_PREFIX):
        return DemoCamera()
    if source.startswith(FILE_PREFIX):
        return FileCamera(source[len(FILE_PREFIX) :])
    if source.startswith(MJPEG_PREFIX):
        return MjpegCamera(source[len(MJPEG_PREFIX) :], username, password, timeout)

    scheme = urlparse(source).scheme.lower()
    if scheme in {"rtsp", "rtsps"}:
        return RtspCamera(source, timeout)
    if scheme in {"http", "https"}:
        return HttpSnapshotCamera(source, username, password, timeout)
    raise CameraError(f"не понимаю адрес камеры: {source!r}")
