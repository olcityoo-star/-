"""Синтетическая сцена холодильника.

Нужна, чтобы всю систему можно было запустить и посмотреть без камеры и без
моделей: демо-камера рисует полки с продуктами, а демо-детектор «узнаёт»
ровно то, что было нарисовано.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 960, 720


@dataclass(frozen=True)
class DemoItem:
    label: str
    box: tuple[float, float, float, float]
    color: tuple[int, int, int]


#: Сценарий: что лежит в холодильнике на каждом шаге. Между шагами продукты
#: появляются и исчезают, чтобы было видно работу ленты событий.
SCENES: tuple[tuple[DemoItem, ...], ...] = (
    (
        DemoItem("молоко", (0.06, 0.10, 0.20, 0.42), (240, 240, 245)),
        DemoItem("яйца", (0.26, 0.20, 0.48, 0.40), (232, 214, 180)),
        DemoItem("сыр", (0.56, 0.22, 0.72, 0.38), (247, 208, 92)),
        DemoItem("помидор", (0.10, 0.56, 0.22, 0.72), (208, 62, 52)),
        DemoItem("помидор", (0.24, 0.58, 0.35, 0.73), (196, 55, 46)),
        DemoItem("огурец", (0.40, 0.60, 0.60, 0.70), (86, 150, 62)),
        DemoItem("сок", (0.78, 0.12, 0.92, 0.46), (238, 146, 48)),
    ),
    (
        DemoItem("молоко", (0.06, 0.10, 0.20, 0.42), (240, 240, 245)),
        DemoItem("яйца", (0.26, 0.20, 0.48, 0.40), (232, 214, 180)),
        DemoItem("помидор", (0.10, 0.56, 0.22, 0.72), (208, 62, 52)),
        DemoItem("огурец", (0.40, 0.60, 0.60, 0.70), (86, 150, 62)),
        DemoItem("сок", (0.78, 0.12, 0.92, 0.46), (238, 146, 48)),
        DemoItem("йогурт", (0.62, 0.56, 0.74, 0.74), (222, 160, 196)),
    ),
    (
        DemoItem("молоко", (0.06, 0.10, 0.20, 0.42), (240, 240, 245)),
        DemoItem("сыр", (0.56, 0.22, 0.72, 0.38), (247, 208, 92)),
        DemoItem("огурец", (0.40, 0.60, 0.60, 0.70), (86, 150, 62)),
        DemoItem("йогурт", (0.62, 0.56, 0.74, 0.74), (222, 160, 196)),
        DemoItem("колбаса", (0.14, 0.56, 0.34, 0.70), (188, 92, 92)),
        DemoItem("сок", (0.78, 0.12, 0.92, 0.46), (238, 146, 48)),
    ),
)

_lock = threading.Lock()
_index = 0


def advance() -> tuple[DemoItem, ...]:
    """Возвращает следующую сцену и запоминает её как текущую."""
    global _index
    with _lock:
        scene = SCENES[_index % len(SCENES)]
        _index += 1
        return scene


def current() -> tuple[DemoItem, ...]:
    with _lock:
        return SCENES[(_index - 1) % len(SCENES)] if _index else SCENES[0]


def reset() -> None:
    global _index
    with _lock:
        _index = 0


def render(scene: tuple[DemoItem, ...]) -> bytes:
    """Рисует сцену как JPEG-кадр «изнутри холодильника»."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (28, 34, 44))
    draw = ImageDraw.Draw(image)

    for shelf_y in (0.48, 0.82):
        y = int(shelf_y * HEIGHT)
        draw.rectangle([0, y, WIDTH, y + 10], fill=(70, 82, 100))
        draw.rectangle([0, y + 10, WIDTH, y + 16], fill=(46, 54, 68))

    draw.rectangle([0, 0, WIDTH, 14], fill=(120, 190, 255))

    for item in scene:
        x1, y1, x2, y2 = (
            int(item.box[0] * WIDTH),
            int(item.box[1] * HEIGHT),
            int(item.box[2] * WIDTH),
            int(item.box[3] * HEIGHT),
        )
        draw.rounded_rectangle([x1, y1, x2, y2], radius=12, fill=item.color)
        shadow = tuple(max(0, channel - 45) for channel in item.color)
        draw.rounded_rectangle([x1, y2 - 12, x2, y2], radius=6, fill=shadow)
        draw.text((x1 + 8, y1 + 8), item.label, fill=(20, 20, 25))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()
