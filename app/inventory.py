"""Сведение кадров в текущий инвентарь холодильника."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .catalog import CATEGORIES
from .models import Detection, Event, Item, Scan
from .schemas import DetectedItem, ItemOut, ScanOut


def apply_scan(session: Session, detections: list[DetectedItem], scan: Scan) -> list[Event]:
    now = datetime.utcnow()
    seen_keys = {item.key for item in detections}
    events: list[Event] = []

    for detected in detections:
        row = session.query(Item).filter(Item.key == detected.key).one_or_none()
        if row is None:
            row = Item(
                key=detected.key,
                name=detected.name,
                name_en=detected.name_en,
                emoji=detected.emoji,
                category=detected.category,
                count=detected.count,
                status="in",
                first_seen=now,
                last_seen=now,
                missed_scans=0,
                notes=detected.notes,
            )
            session.add(row)
            events.append(
                Event(kind="added", item_key=detected.key, message=f"Появилось: {detected.emoji} {detected.name}")
            )
        else:
            was_gone = row.status in {"gone", "maybe_gone"}
            row.name = detected.name
            row.name_en = detected.name_en
            row.emoji = detected.emoji
            row.category = detected.category
            row.count = detected.count
            row.last_seen = now
            row.missed_scans = 0
            row.status = "in"
            if detected.notes:
                row.notes = detected.notes
            if was_gone:
                events.append(
                    Event(kind="added", item_key=row.key, message=f"Снова в холодильнике: {row.emoji} {row.name}")
                )
        session.add(
            Detection(
                scan=scan,
                key=detected.key,
                name=detected.name,
                name_en=detected.name_en,
                emoji=detected.emoji,
                category=detected.category,
                count=detected.count,
                confidence=detected.confidence,
                x1=detected.box.x1,
                y1=detected.box.y1,
                x2=detected.box.x2,
                y2=detected.box.y2,
                notes=detected.notes,
            )
        )

    for row in session.query(Item).all():
        if row.key in seen_keys or row.status == "wanted":
            continue
        row.missed_scans += 1
        if row.missed_scans >= 2:
            if row.status != "gone":
                row.status = "gone"
                events.append(
                    Event(kind="removed", item_key=row.key, message=f"Пропало: {row.emoji} {row.name}")
                )
        else:
            row.status = "maybe_gone"

    for event in events:
        session.add(event)
    return events


def item_to_out(item: Item) -> ItemOut:
    return ItemOut(
        id=item.id,
        key=item.key,
        name=item.name,
        name_en=item.name_en,
        emoji=item.emoji,
        category=item.category,
        category_label=CATEGORIES.get(item.category, "Другое"),
        count=item.count,
        status=item.status,
        first_seen=item.first_seen,
        last_seen=item.last_seen,
        missed_scans=item.missed_scans,
        notes=item.notes,
        wanted=item.wanted,
    )


def scan_to_out(scan: Scan) -> ScanOut:
    return ScanOut(
        id=scan.id,
        created_at=scan.created_at,
        source=scan.source,
        note=scan.note,
        image_url=f"/api/scans/{scan.id}/image",
        detections=[
            DetectedItem(
                name=det.name,
                name_en=det.name_en,
                key=det.key,
                emoji=det.emoji,
                category=det.category,
                count=det.count,
                confidence=det.confidence,
                notes=det.notes,
                box={"x1": det.x1, "y1": det.y1, "x2": det.x2, "y2": det.y2},
            )
            for det in scan.detections
        ],
    )


def shopping_list(session: Session) -> list[dict]:
    rows = session.query(Item).order_by(Item.name.asc()).all()
    result = []
    for row in rows:
        if row.status == "gone" or row.wanted > row.count:
            result.append(
                {
                    "key": row.key,
                    "name": row.name,
                    "emoji": row.emoji,
                    "reason": "закончилось" if row.status == "gone" else "нужно докупить",
                    "wanted": max(row.wanted, 1),
                }
            )
    return result
