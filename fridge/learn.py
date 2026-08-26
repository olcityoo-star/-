from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from fridge.config import GALLERY_PATH, RECLASSIFY_CLASSES
from fridge.dataset import dataset_stats, iter_sample_images

_GALLERY: "Gallery | None" = None


def image_features(image: Image.Image) -> np.ndarray:
    """Compact visual fingerprint: tiny RGB grid + HSV histograms."""
    rgb = image.convert("RGB").resize((16, 16), Image.BILINEAR)
    grid = np.asarray(rgb, dtype=np.float32).reshape(-1) / 255.0
    hsv = np.asarray(image.convert("HSV").resize((48, 48), Image.BILINEAR), dtype=np.float32)
    h_hist, _ = np.histogram(hsv[:, :, 0], bins=16, range=(0, 255), density=True)
    s_hist, _ = np.histogram(hsv[:, :, 1], bins=8, range=(0, 255), density=True)
    v_hist, _ = np.histogram(hsv[:, :, 2], bins=8, range=(0, 255), density=True)
    # Local contrast proxy
    gray = np.asarray(image.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.float32) / 255.0
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    vec = np.concatenate([grid, h_hist.astype(np.float32), s_hist.astype(np.float32), v_hist.astype(np.float32), [gx, gy]])
    norm = np.linalg.norm(vec) + 1e-8
    return (vec / norm).astype(np.float32)


def features_from_bytes(jpeg_bytes: bytes) -> np.ndarray:
    return image_features(Image.open(BytesIO(jpeg_bytes)).convert("RGB"))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8))


@dataclass
class Gallery:
    labels: list[str]
    vectors: np.ndarray
    centroids: dict[str, np.ndarray]
    counts: dict[str, int]
    updated_at: str

    def predict(self, vec: np.ndarray, threshold: float = 0.78) -> tuple[str | None, float]:
        if not self.centroids:
            return None, 0.0
        best_label = None
        best_score = -1.0
        for label, center in self.centroids.items():
            score = cosine(vec, center)
            if score > best_score:
                best_score = score
                best_label = label
        if best_label is None or best_score < threshold:
            return None, best_score if best_score >= 0 else 0.0
        return best_label, best_score


def _build_centroids(labels: list[str], vectors: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    centroids: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    for label in sorted(set(labels)):
        idx = [i for i, name in enumerate(labels) if name == label]
        if not idx:
            continue
        mat = vectors[idx]
        center = mat.mean(axis=0)
        center = center / (np.linalg.norm(center) + 1e-8)
        centroids[label] = center.astype(np.float32)
        counts[label] = len(idx)
    return centroids, counts


def train_gallery(path: Path | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone

    pairs = iter_sample_images()
    if len(pairs) < 1:
        raise RuntimeError("Нет образцов для обучения. Синхронизируйте скан с правильными названиями или добавьте фото.")

    labels: list[str] = []
    vectors: list[np.ndarray] = []
    for label, image_path in pairs:
        try:
            image = Image.open(image_path).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        labels.append(label)
        vectors.append(image_features(image))

    if not vectors:
        raise RuntimeError("Не удалось прочитать образцы датасета")

    mat = np.stack(vectors, axis=0)
    centroids, counts = _build_centroids(labels, mat)
    updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    target = path or GALLERY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target,
        labels=np.array(labels, dtype=object),
        vectors=mat,
        centroid_labels=np.array(list(centroids.keys()), dtype=object),
        centroid_vectors=np.stack(list(centroids.values()), axis=0) if centroids else np.zeros((0, mat.shape[1]), dtype=np.float32),
        counts_json=json.dumps(counts, ensure_ascii=False),
        updated_at=updated_at,
    )
    global _GALLERY
    _GALLERY = Gallery(labels=labels, vectors=mat, centroids=centroids, counts=counts, updated_at=updated_at)
    stats = dataset_stats()
    return {
        "samples": len(labels),
        "labels": len(centroids),
        "counts": counts,
        "updated_at": updated_at,
        "path": str(target),
        "dataset": stats,
    }


def load_gallery(path: Path | None = None, force: bool = False) -> Gallery | None:
    global _GALLERY
    if _GALLERY is not None and not force:
        return _GALLERY
    target = path or GALLERY_PATH
    if not target.exists():
        _GALLERY = None
        return None
    data = np.load(target, allow_pickle=True)
    labels = [str(x) for x in data["labels"].tolist()]
    vectors = data["vectors"].astype(np.float32)
    centroid_labels = [str(x) for x in data["centroid_labels"].tolist()]
    centroid_vectors = data["centroid_vectors"].astype(np.float32)
    centroids = {label: centroid_vectors[i] for i, label in enumerate(centroid_labels)}
    counts = json.loads(str(data["counts_json"]))
    updated_at = str(data["updated_at"])
    _GALLERY = Gallery(labels=labels, vectors=vectors, centroids=centroids, counts=counts, updated_at=updated_at)
    return _GALLERY


def gallery_status() -> dict[str, Any]:
    gallery = load_gallery()
    stats = dataset_stats()
    if gallery is None:
        return {
            "ready": False,
            "samples": 0,
            "labels": 0,
            "counts": {},
            "updated_at": None,
            "dataset": stats,
            "message": "Классификатор ещё не собран. Добавьте образцы и нажмите «Обучить».",
        }
    return {
        "ready": True,
        "samples": len(gallery.labels),
        "labels": len(gallery.centroids),
        "counts": gallery.counts,
        "updated_at": gallery.updated_at,
        "dataset": stats,
        "message": "Кастомный классификатор готов",
    }


def apply_custom_labels(
    image_bytes: bytes,
    detections: list[dict[str, Any]],
    threshold: float = 0.78,
) -> list[dict[str, Any]]:
    """Re-label generic YOLO boxes using the trained gallery."""
    gallery = load_gallery()
    if gallery is None or not detections:
        return detections

    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    result: list[dict[str, Any]] = []
    for det in detections:
        item = dict(det)
        class_name = str(item.get("class_name") or "")
        current = str(item.get("name") or "")
        should_try = class_name in RECLASSIFY_CLASSES or current.lower() in {
            "бутылка",
            "миска",
            "стакан",
            "бокал",
            "банка / ваза",
        }
        if not should_try:
            result.append(item)
            continue
        bbox = item.get("bbox") or {}
        x1 = int(max(0, float(bbox.get("x1") or 0)))
        y1 = int(max(0, float(bbox.get("y1") or 0)))
        x2 = int(min(width, float(bbox.get("x2") or width)))
        y2 = int(min(height, float(bbox.get("y2") or height)))
        if x2 <= x1 or y2 <= y1:
            result.append(item)
            continue
        crop = image.crop((x1, y1, x2, y2))
        label, score = gallery.predict(image_features(crop), threshold=threshold)
        item["custom_score"] = round(float(score), 3)
        if label:
            item["custom_name"] = label
            item["name"] = label
            item["category"] = item.get("category") or "своё"
            item["source_model"] = "custom"
        result.append(item)
    return result
