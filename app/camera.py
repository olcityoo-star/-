"""Снимок с беспроводной камеры: HTTP snapshot или RTSP."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse, urlunparse

import cv2
import httpx
import numpy as np

from .config import Settings


class CameraError(RuntimeError):
    pass


@dataclass
class Frame:
    image: np.ndarray
    source: str


def mask_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "http"
    path = parsed.path or ""
    if parsed.username or parsed.password:
        return f"{scheme}://***:***@{host}{path}"
    return f"{scheme}://{host}{path}"


def build_camera_url(settings: Settings) -> str:
    url = (settings.camera_url or "").strip()
    if not url:
        raise CameraError("Адрес камеры не задан. Укажите CAMERA_URL в .env или в настройках.")
    parsed = urlparse(url)
    if settings.camera_user and not parsed.username:
        user = quote(settings.camera_user, safe="")
        password = quote(settings.camera_password or "", safe="")
        netloc = f"{user}:{password}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        url = urlunparse(parsed._replace(netloc=netloc))
    return url


def _decode_image(data: bytes) -> np.ndarray:
    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise CameraError("Не удалось разобрать снимок камеры.")
    return image


def capture_http(url: str, timeout: float = 8.0) -> np.ndarray:
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CameraError(f"Камера не отвечает по HTTP: {exc}") from exc
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        raise CameraError("По адресу камеры пришла HTML-страница, а не снимок. Нужен URL кадра (snapshot/RTSP).")
    return _decode_image(response.content)


def capture_rtsp(url: str) -> np.ndarray:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise CameraError("Не удалось прочитать кадр из RTSP. Проверьте логин, пароль и путь потока.")
        return frame
    finally:
        cap.release()


def capture_frame(settings: Settings) -> Frame:
    url = build_camera_url(settings)
    parsed = urlparse(url)
    if parsed.scheme in {"rtsp", "rtsps"}:
        image = capture_rtsp(url)
        source = "rtsp"
    elif parsed.scheme in {"http", "https"}:
        image = capture_http(url)
        source = "http"
    else:
        raise CameraError(f"Неподдерживаемый адрес камеры: {parsed.scheme}")
    return Frame(image=image, source=source)


def decode_upload(data: bytes) -> np.ndarray:
    return _decode_image(data)
