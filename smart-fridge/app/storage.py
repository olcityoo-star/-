"""Хранилище состояния холодильника на SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    path        TEXT NOT NULL,
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    detector    TEXT NOT NULL DEFAULT '',
    detections  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS items (
    key         TEXT PRIMARY KEY,
    count       INTEGER NOT NULL,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    added_at    REAL NOT NULL,
    expires_at  REAL,
    confidence  REAL NOT NULL DEFAULT 0,
    manual      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    delta       INTEGER NOT NULL DEFAULT 0,
    count       INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'camera'
);

CREATE INDEX IF NOT EXISTS events_ts ON events (ts DESC);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (ts DESC);
"""


@dataclass
class ItemRow:
    key: str
    count: int
    first_seen: float
    last_seen: float
    added_at: float
    expires_at: float | None
    confidence: float
    manual: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventRow:
    id: int
    ts: float
    kind: str
    item_key: str
    delta: int
    count: int
    source: str


@dataclass
class SnapshotRow:
    id: int
    ts: float
    path: str
    width: int
    height: int
    detector: str
    detections: list[dict[str, Any]]


class Storage:
    """Тонкая обёртка над SQLite. Потокобезопасна: доступ сериализуется мьютексом."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent != Path(""):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- инвентарь -----------------------------------------------------

    def get_items(self) -> list[ItemRow]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM items ORDER BY added_at DESC").fetchall()
        return [_item_from_row(row) for row in rows]

    def get_item(self, key: str) -> ItemRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
        return _item_from_row(row) if row else None

    def upsert_item(self, item: ItemRow) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO items (key, count, first_seen, last_seen, added_at, expires_at, confidence, manual)
                VALUES (:key, :count, :first_seen, :last_seen, :added_at, :expires_at, :confidence, :manual)
                ON CONFLICT(key) DO UPDATE SET
                    count = excluded.count,
                    last_seen = excluded.last_seen,
                    added_at = excluded.added_at,
                    expires_at = excluded.expires_at,
                    confidence = excluded.confidence,
                    manual = excluded.manual
                """,
                {**item.to_dict(), "manual": int(item.manual)},
            )
            self._conn.commit()

    def delete_item(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM items WHERE key = ?", (key,))
            self._conn.commit()

    # --- события -------------------------------------------------------

    def add_event(self, kind: str, item_key: str, delta: int, count: int, source: str = "camera") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, item_key, delta, count, source) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), kind, item_key, delta, count, source),
            )
            self._conn.commit()

    def get_events(self, limit: int = 50) -> list[EventRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            EventRow(
                id=row["id"],
                ts=row["ts"],
                kind=row["kind"],
                item_key=row["item_key"],
                delta=row["delta"],
                count=row["count"],
                source=row["source"],
            )
            for row in rows
        ]

    # --- снимки --------------------------------------------------------

    def add_snapshot(
        self,
        path: str,
        width: int,
        height: int,
        detector: str,
        detections: Iterable[dict[str, Any]],
        ts: float | None = None,
    ) -> int:
        payload = json.dumps(list(detections), ensure_ascii=False)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO snapshots (ts, path, width, height, detector, detections) VALUES (?, ?, ?, ?, ?, ?)",
                (ts if ts is not None else time.time(), path, width, height, detector, payload),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def latest_snapshot(self) -> SnapshotRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return SnapshotRow(
            id=row["id"],
            ts=row["ts"],
            path=row["path"],
            width=row["width"],
            height=row["height"],
            detector=row["detector"],
            detections=json.loads(row["detections"]),
        )

    def prune_snapshots(self, keep: int) -> list[str]:
        """Оставляет только `keep` последних снимков, возвращает пути удалённых."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path FROM snapshots ORDER BY ts DESC, id DESC LIMIT -1 OFFSET ?", (keep,)
            ).fetchall()
            if not rows:
                return []
            ids = [row["id"] for row in rows]
            self._conn.executemany("DELETE FROM snapshots WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        return [row["path"] for row in rows]


def _item_from_row(row: sqlite3.Row) -> ItemRow:
    return ItemRow(
        key=row["key"],
        count=row["count"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        added_at=row["added_at"],
        expires_at=row["expires_at"],
        confidence=row["confidence"],
        manual=bool(row["manual"]),
    )
