from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any


def normalize_name(value: str) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9\s%./-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def build_sync_plan(
    items: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    threshold: float = 0.72,
) -> dict[str, Any]:
    """Match detections to inventory: kept / added / maybe removed."""
    accepted = [dict(det) for det in detections if det.get("accepted", True) and str(det.get("name") or "").strip()]
    unused_items = list(items)
    kept: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []

    for det in accepted:
        best_idx = -1
        best_score = 0.0
        for idx, item in enumerate(unused_items):
            score = similarity(det["name"], item["name"])
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx >= 0 and best_score >= threshold:
            item = unused_items.pop(best_idx)
            entry = {
                **det,
                "match_item_id": item["id"],
                "match_name": item["name"],
                "match_score": round(best_score, 3),
                "sync_action": "keep",
                "expires_on": det.get("expires_on") or item.get("expires_on"),
            }
            kept.append(entry)
        else:
            entry = {**det, "match_item_id": None, "match_score": round(best_score, 3), "sync_action": "add"}
            added.append(entry)

    removed = [
        {
            "id": item["id"],
            "name": item["name"],
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "category": item.get("category"),
            "expires_on": item.get("expires_on"),
            "sync_action": "remove",
            "remove": True,
        }
        for item in unused_items
    ]

    return {
        "kept": kept,
        "added": added,
        "removed": removed,
        "summary": {
            "kept": len(kept),
            "added": len(added),
            "removed": len(removed),
        },
    }


DATE_PATTERNS = [
    re.compile(r"(?:годен(?:\s*до)?|употребить\s*до|best\s*before|exp(?:iry)?|use\s*by)[^\d]{0,12}(\d{1,2})[./](\d{1,2})[./](\d{2,4})", re.I),
    re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})"),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
]


def _to_iso(year: int, month: int, day: int) -> str | None:
    if year < 100:
        year += 2000
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    # Ignore absurd dates far in the past/future
    today = date.today()
    if value.year < today.year - 1 or value.year > today.year + 5:
        return None
    return value.isoformat()


def parse_expiry(text: str) -> str | None:
    if not text:
        return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 3 and len(groups[0]) == 4:
            iso = _to_iso(int(groups[0]), int(groups[1]), int(groups[2]))
        else:
            iso = _to_iso(int(groups[2]), int(groups[1]), int(groups[0]))
        if iso:
            return iso
    return None


def suggest_name_from_ocr(ocr_text: str, fallback: str) -> str:
    if not ocr_text:
        return fallback
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    # Prefer longer Cyrillic/Latin product-like lines without pure dates.
    candidates: list[str] = []
    for line in lines:
        if parse_expiry(line) and len(re.sub(r"\d|[./:\-]", "", line)) < 4:
            continue
        cleaned = re.sub(r"\s+", " ", line).strip(" -_|")
        if len(cleaned) < 3:
            continue
        if re.fullmatch(r"[\d\s./%-]+", cleaned):
            continue
        candidates.append(cleaned)
    if not candidates:
        return fallback
    candidates.sort(key=lambda value: (len(re.findall(r"[A-Za-zА-Яа-я]", value)), len(value)), reverse=True)
    best = candidates[0]
    if len(best) > 48:
        best = best[:48].rstrip()
    # Keep YOLO label if OCR is just noise shorter than 4 letters
    if len(re.findall(r"[A-Za-zА-Яа-я]", best)) < 3:
        return fallback
    return best


def shopping_list(items: list[dict[str, Any]], days: int = 3) -> list[dict[str, Any]]:
    today = date.today()
    result: list[dict[str, Any]] = []
    for item in items:
        expires = item.get("expires_on")
        reason = None
        if expires:
            try:
                exp = datetime.strptime(expires, "%Y-%m-%d").date()
            except ValueError:
                exp = None
            if exp is not None:
                delta = (exp - today).days
                if delta < 0:
                    reason = "просрочено"
                elif delta <= days:
                    reason = f"истекает через {delta} дн."
        if reason:
            result.append({**item, "reason": reason})
    return result
