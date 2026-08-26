from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from fridge import dataset, learn


def _jpeg(color=(40, 120, 200)) -> bytes:
    image = Image.new("RGB", (80, 80), color)
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def test_train_and_predict(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "DATASET_DIR", tmp_path)
    monkeypatch.setattr(dataset, "SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr(learn, "GALLERY_PATH", tmp_path / "gallery.npz")
    monkeypatch.setattr("fridge.config.DATASET_DIR", tmp_path)
    monkeypatch.setattr("fridge.config.SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr("fridge.config.GALLERY_PATH", tmp_path / "gallery.npz")

    dataset.add_sample("Молоко", _jpeg((220, 220, 220)))
    dataset.add_sample("Молоко", _jpeg((210, 210, 215)))
    dataset.add_sample("Сок", _jpeg((220, 80, 40)))
    dataset.add_sample("Сок", _jpeg((200, 60, 30)))

    info = learn.train_gallery()
    assert info["labels"] == 2
    assert info["samples"] == 4

    gallery = learn.load_gallery(force=True)
    milk_vec = learn.features_from_bytes(_jpeg((215, 215, 218)))
    juice_vec = learn.features_from_bytes(_jpeg((210, 70, 35)))
    milk_label, milk_score = gallery.predict(milk_vec, threshold=0.5)
    juice_label, juice_score = gallery.predict(juice_vec, threshold=0.5)
    assert milk_label == "Молоко"
    assert juice_label == "Сок"
    assert milk_score > 0.5
    assert juice_score > 0.5


def test_apply_custom_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "DATASET_DIR", tmp_path)
    monkeypatch.setattr(dataset, "SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr(learn, "GALLERY_PATH", tmp_path / "gallery.npz")
    learn._GALLERY = None

    dataset.add_sample("Кефир", _jpeg((180, 180, 190)))
    dataset.add_sample("Кефир", _jpeg((170, 170, 185)))
    learn.train_gallery()

    # Full frame with a bottle-like crop region painted
    frame = Image.new("RGB", (200, 200), (10, 10, 10))
    for x in range(40, 100):
        for y in range(40, 160):
            frame.putpixel((x, y), (175, 175, 188))
    buf = BytesIO()
    frame.save(buf, format="JPEG")
    jpeg = buf.getvalue()
    dets = [
        {
            "name": "Бутылка",
            "class_name": "bottle",
            "bbox": {"x1": 40, "y1": 40, "x2": 100, "y2": 160},
            "confidence": 0.7,
        }
    ]
    out = learn.apply_custom_labels(jpeg, dets, threshold=0.55)
    assert out[0]["name"] == "Кефир"
    assert out[0].get("source_model") == "custom"
