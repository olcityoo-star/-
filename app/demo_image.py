"""Демо-кадр «внутренности холодильника», чтобы система работала без камеры."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from .schemas import Box, DetectedItem


DEMO_LAYOUT: tuple[tuple[str, str, tuple[int, int, int, int], tuple[int, int, int]], ...] = (
    ("Молоко", "milk", (40, 70, 130, 250), (245, 245, 240)),
    ("Йогурт", "yogurt", (150, 90, 230, 180), (255, 210, 180)),
    ("Сыр", "cheese", (250, 80, 360, 150), (250, 210, 80)),
    ("Яйца", "eggs", (380, 90, 520, 170), (245, 245, 230)),
    ("Помидоры", "tomato", (50, 280, 160, 360), (210, 60, 50)),
    ("Огурцы", "cucumber", (180, 290, 310, 350), (60, 150, 70)),
    ("Сок", "juice", (430, 260, 520, 420), (255, 140, 40)),
    ("Остатки еды", "leftovers", (200, 390, 360, 500), (90, 140, 170)),
    ("Вода", "water", (40, 400, 110, 530), (160, 210, 230)),
)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_demo_fridge(width: int = 560, height: int = 560) -> np.ndarray:
    image = Image.new("RGB", (width, height), (18, 28, 38))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, width - 12, height - 12), radius=28, fill=(28, 42, 56), outline=(150, 200, 220), width=4)
    for y in (190, 370):
        draw.rectangle((28, y, width - 28, y + 10), fill=(90, 120, 140))
    draw.rectangle((width - 36, 40, width - 22, height - 40), fill=(190, 220, 230))
    label_font = _font(16)
    for _name, _key, box, color in DEMO_LAYOUT:
        x1, y1, x2, y2 = box
        draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=color, outline=(20, 20, 20), width=2)
        draw.text((x1 + 8, y1 + 8), _name, fill=(20, 20, 20), font=label_font)
    rgb = np.array(image)
    return rgb[:, :, ::-1].copy()


def demo_detections() -> list[DetectedItem]:
    items: list[DetectedItem] = []
    from .catalog import describe

    for name, key, box, _color in DEMO_LAYOUT:
        meta = describe(key)
        x1, y1, x2, y2 = box
        items.append(
            DetectedItem(
                name=meta["name"],
                name_en=meta["name_en"],
                key=meta["key"],
                emoji=meta["emoji"],
                category=meta["category"],
                count=1,
                confidence=0.92,
                notes="демо",
                box=Box(x1=x1, y1=y1, x2=x2, y2=y2),
            )
        )
    return items
