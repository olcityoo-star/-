from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from fridge.config import CAPTURES_DIR, DATASET_DIR, SAMPLES_DIR

GENERIC_LABELS = {
    "бутылка",
    "миска",
    "стакан",
    "бокал",
    "банка / ваза",
    "холодильник",
    "bottle",
    "bowl",
    "cup",
    "wine glass",
    "vase",
    "refrigerator",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_label(name: str) -> str:
    text = (name or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip() or "unknown"
    return text[:80]


def slug_label(name: str) -> str:
    text = safe_label(name).lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", "_", text, flags=re.I)
    return text.strip("_") or "unknown"


def ensure_dirs() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)


def crop_to_jpeg(image: Image.Image, bbox: dict[str, Any] | None, pad: float = 0.06) -> bytes:
    width, height = image.size
    if bbox:
        x1 = float(bbox.get("x1") or 0)
        y1 = float(bbox.get("y1") or 0)
        x2 = float(bbox.get("x2") or width)
        y2 = float(bbox.get("y2") or height)
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)
        x1 = max(0, int(x1 - bw * pad))
        y1 = max(0, int(y1 - bh * pad))
        x2 = min(width, int(x2 + bw * pad))
        y2 = min(height, int(y2 + bh * pad))
        crop = image.crop((x1, y1, x2, y2))
    else:
        crop = image
    crop = crop.convert("RGB")
    if min(crop.size) < 64:
        scale = 64 / max(min(crop.size), 1)
        crop = crop.resize((max(64, int(crop.width * scale)), max(64, int(crop.height * scale))), Image.BICUBIC)
    buf = BytesIO()
    crop.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()


def add_sample(
    label: str,
    jpeg_bytes: bytes,
    *,
    source: str = "manual",
    scan_id: int | None = None,
    class_name: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_dirs()
    label = safe_label(label)
    folder = SAMPLES_DIR / slug_label(label)
    folder.mkdir(parents=True, exist_ok=True)
    sample_id = uuid4().hex[:12]
    filename = f"{sample_id}.jpg"
    path = folder / filename
    path.write_bytes(jpeg_bytes)
    record = {
        "id": sample_id,
        "label": label,
        "path": str(path.relative_to(DATASET_DIR)),
        "source": source,
        "scan_id": scan_id,
        "class_name": class_name,
        "created_at": _utcnow(),
        "meta": meta or {},
    }
    with (DATASET_DIR / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def add_samples_from_scan(
    image_name: str,
    detections: list[dict[str, Any]],
    scan_id: int | None = None,
) -> list[dict[str, Any]]:
    """Save crops for accepted detections with non-generic names."""
    image_path = CAPTURES_DIR / image_name
    if not image_path.exists():
        return []
    image = Image.open(image_path).convert("RGB")
    saved: list[dict[str, Any]] = []
    for det in detections:
        if not det.get("accepted", True):
            continue
        name = str(det.get("name") or "").strip()
        if not name or name.lower() in GENERIC_LABELS:
            continue
        jpeg = crop_to_jpeg(image, det.get("bbox"))
        saved.append(
            add_sample(
                name,
                jpeg,
                source="scan",
                scan_id=scan_id,
                class_name=str(det.get("class_name") or "") or None,
                meta={"confidence": det.get("confidence"), "bbox": det.get("bbox")},
            )
        )
    return saved


def list_index() -> list[dict[str, Any]]:
    ensure_dirs()
    path = DATASET_DIR / "index.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def dataset_stats() -> dict[str, Any]:
    rows = list_index()
    by_label: dict[str, int] = {}
    for row in rows:
        label = row.get("label") or "unknown"
        by_label[label] = by_label.get(label, 0) + 1
    ranked = sorted(by_label.items(), key=lambda item: (-item[1], item[0].lower()))
    return {
        "total": len(rows),
        "labels": len(by_label),
        "by_label": [{"label": k, "count": v} for k, v in ranked],
        "ready_for_train": len(rows) >= 3 and len(by_label) >= 1,
        "min_recommended": 3,
    }


def iter_sample_images() -> list[tuple[str, Path]]:
    """Return (label, absolute image path) for training."""
    ensure_dirs()
    pairs: list[tuple[str, Path]] = []
    for row in list_index():
        rel = row.get("path")
        label = row.get("label")
        if not rel or not label:
            continue
        path = DATASET_DIR / rel
        if path.exists():
            pairs.append((str(label), path))
    # Also pick up folders that have images but missing index entries
    if SAMPLES_DIR.exists():
        indexed = {str(DATASET_DIR / row.get("path")) for row in list_index() if row.get("path")}
        for folder in SAMPLES_DIR.iterdir():
            if not folder.is_dir():
                continue
            for image_path in folder.glob("*.jpg"):
                if str(image_path) in indexed:
                    continue
                # reconstruct label from folder name poorly — skip unlabeled orphans
                continue
    return pairs


def delete_label(label: str) -> int:
    """Remove all samples for a label from index and disk."""
    ensure_dirs()
    target = safe_label(label)
    rows = list_index()
    keep: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        if row.get("label") == target:
            path = DATASET_DIR / row["path"]
            if path.exists():
                path.unlink()
            removed += 1
        else:
            keep.append(row)
    with (DATASET_DIR / "index.jsonl").open("w", encoding="utf-8") as fh:
        for row in keep:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    folder = SAMPLES_DIR / slug_label(target)
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    return removed
