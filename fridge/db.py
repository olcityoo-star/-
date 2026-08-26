from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fridge.config import CAPTURES_DIR, DATA_DIR, DB_PATH, DEFAULT_SETTINGS


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    with session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit TEXT NOT NULL DEFAULT 'шт',
                category TEXT NOT NULL DEFAULT 'другое',
                expires_on TEXT,
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                last_scan_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                image_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                detections_json TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT 'camera'
            );

            CREATE TABLE IF NOT EXISTS aliases (
                raw_key TEXT PRIMARY KEY,
                preferred TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        existing = {row["key"] for row in conn.execute("SELECT key FROM settings")}
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({row["key"]: row["value"] for row in rows})
    return data


def update_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> dict[str, str]:
    allowed = set(DEFAULT_SETTINGS) | {"confidence", "food_only"}
    for key, value in values.items():
        if key not in allowed:
            continue
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value).strip()),
        )
    return get_settings(conn)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def list_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM items ORDER BY datetime(expires_on) ASC, name COLLATE NOCASE"
    ).fetchall()
    return [dict(row) for row in rows]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())


def create_item(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    cursor = conn.execute(
        """
        INSERT INTO items (
            name, quantity, unit, category, expires_on, notes, source,
            last_scan_id, created_at, updated_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["name"].strip(),
            float(payload.get("quantity") or 1),
            (payload.get("unit") or "шт").strip() or "шт",
            (payload.get("category") or "другое").strip() or "другое",
            payload.get("expires_on") or None,
            (payload.get("notes") or "").strip(),
            payload.get("source") or "manual",
            payload.get("last_scan_id"),
            now,
            now,
            now,
        ),
    )
    return get_item(conn, cursor.lastrowid)  # type: ignore[arg-type]


def update_item(conn: sqlite3.Connection, item_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    current = get_item(conn, item_id)
    if current is None:
        return None
    fields = {
        "name": payload.get("name", current["name"]),
        "quantity": payload.get("quantity", current["quantity"]),
        "unit": payload.get("unit", current["unit"]),
        "category": payload.get("category", current["category"]),
        "expires_on": payload["expires_on"] if "expires_on" in payload else current["expires_on"],
        "notes": payload.get("notes", current["notes"]),
        "updated_at": utcnow(),
        "last_seen_at": payload.get("last_seen_at", current["last_seen_at"]),
        "source": payload.get("source", current["source"]),
        "last_scan_id": payload.get("last_scan_id", current["last_scan_id"]),
    }
    conn.execute(
        """
        UPDATE items SET
            name = ?, quantity = ?, unit = ?, category = ?, expires_on = ?,
            notes = ?, updated_at = ?, last_seen_at = ?, source = ?, last_scan_id = ?
        WHERE id = ?
        """,
        (
            str(fields["name"]).strip(),
            float(fields["quantity"] or 1),
            str(fields["unit"]).strip() or "шт",
            str(fields["category"]).strip() or "другое",
            fields["expires_on"] or None,
            str(fields["notes"] or ""),
            fields["updated_at"],
            fields["last_seen_at"],
            fields["source"],
            fields["last_scan_id"],
            item_id,
        ),
    )
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: int) -> bool:
    cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return cursor.rowcount > 0


def create_scan(
    conn: sqlite3.Connection,
    image_name: str,
    detections: list[dict[str, Any]],
    source: str = "camera",
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO scans (created_at, image_name, status, detections_json, source)
        VALUES (?, ?, 'pending', ?, ?)
        """,
        (utcnow(), image_name, json.dumps(detections, ensure_ascii=False), source),
    )
    return get_scan(conn, cursor.lastrowid)  # type: ignore[arg-type]


def get_scan(conn: sqlite3.Connection, scan_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["detections"] = json.loads(data.pop("detections_json") or "[]")
    return data


def latest_scan(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    data = dict(row)
    data["detections"] = json.loads(data.pop("detections_json") or "[]")
    return data


def list_scans(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, created_at, image_name, status, source FROM scans ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT raw_key, preferred FROM aliases").fetchall()
    return {row["raw_key"]: row["preferred"] for row in rows}


def upsert_alias(conn: sqlite3.Connection, raw_key: str, preferred: str) -> None:
    key = (raw_key or "").strip().lower()
    value = (preferred or "").strip()
    if not key or not value:
        return
    conn.execute(
        """
        INSERT INTO aliases (raw_key, preferred, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(raw_key) DO UPDATE SET preferred = excluded.preferred, updated_at = excluded.updated_at
        """,
        (key, value, utcnow()),
    )


def apply_aliases(detections: list[dict[str, Any]], aliases: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for det in detections:
        item = dict(det)
        for key in (item.get("class_name"), item.get("name"), item.get("ocr_name")):
            if not key:
                continue
            preferred = aliases.get(str(key).strip().lower())
            if preferred:
                item["name"] = preferred
                item["alias_applied"] = preferred
                break
        result.append(item)
    return result


def confirm_scan(conn: sqlite3.Connection, scan_id: int, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy append-only confirm."""
    return sync_inventory(conn, scan_id, detections, remove_item_ids=[], mode="append")["created"]


def sync_inventory(
    conn: sqlite3.Connection,
    scan_id: int,
    detections: list[dict[str, Any]],
    remove_item_ids: list[int] | None = None,
    mode: str = "sync",
) -> dict[str, Any]:
    scan = update_scan_detections(conn, scan_id, detections, status="confirmed")
    if scan is None:
        raise KeyError("scan not found")

    now = utcnow()
    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    removed: list[int] = []
    remove_ids = {int(x) for x in (remove_item_ids or [])}

    for det in detections:
        if not det.get("accepted", True):
            continue
        name = str(det.get("name") or "").strip()
        if not name:
            continue

        class_name = str(det.get("class_name") or "").strip()
        ocr_name = str(det.get("ocr_name") or "").strip()
        from fridge.config import FOOD_LABELS_RU

        ru = FOOD_LABELS_RU.get(class_name, class_name)
        if name.lower() not in {str(ru).lower(), class_name.lower()}:
            if class_name:
                upsert_alias(conn, class_name, name)
            if ru:
                upsert_alias(conn, ru, name)
            if ocr_name and ocr_name.lower() != name.lower():
                upsert_alias(conn, ocr_name, name)

        match_id = det.get("match_item_id")
        if mode == "sync" and match_id:
            patch = {
                "name": name,
                "quantity": det.get("quantity") or 1,
                "unit": det.get("unit") or "шт",
                "category": det.get("category") or "скан",
                "source": "scan",
                "last_scan_id": scan_id,
                "last_seen_at": now,
            }
            if det.get("expires_on"):
                patch["expires_on"] = det.get("expires_on")
            if det.get("notes"):
                patch["notes"] = det.get("notes")
            item = update_item(conn, int(match_id), patch)
            if item:
                updated.append(item)
            continue

        if mode == "append" or not match_id:
            # Avoid exact-name duplicates in sync mode when match failed.
            if mode == "sync":
                existing = conn.execute(
                    "SELECT id FROM items WHERE lower(name) = lower(?) LIMIT 1",
                    (name,),
                ).fetchone()
                if existing:
                    item = update_item(
                        conn,
                        int(existing["id"]),
                        {
                            "quantity": det.get("quantity") or 1,
                            "expires_on": det.get("expires_on"),
                            "last_scan_id": scan_id,
                            "last_seen_at": now,
                            "source": "scan",
                        },
                    )
                    if item:
                        updated.append(item)
                    continue

            created.append(
                create_item(
                    conn,
                    {
                        "name": name,
                        "quantity": det.get("quantity") or 1,
                        "unit": det.get("unit") or "шт",
                        "category": det.get("category") or "скан",
                        "expires_on": det.get("expires_on") or None,
                        "notes": det.get("notes") or "",
                        "source": "scan",
                        "last_scan_id": scan_id,
                    },
                )
            )

    if mode == "sync":
        for item_id in remove_ids:
            if delete_item(conn, item_id):
                removed.append(item_id)

    return {
        "created": created,
        "updated": updated,
        "removed": removed,
        "scan": get_scan(conn, scan_id),
    }


def update_scan_detections(
    conn: sqlite3.Connection, scan_id: int, detections: list[dict[str, Any]], status: str | None = None
) -> dict[str, Any] | None:
    current = get_scan(conn, scan_id)
    if current is None:
        return None
    conn.execute(
        "UPDATE scans SET detections_json = ?, status = ? WHERE id = ?",
        (json.dumps(detections, ensure_ascii=False), status or current["status"], scan_id),
    )
    return get_scan(conn, scan_id)
