"""Сведение распознанных кадров в устойчивый список содержимого холодильника."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from . import products
from .detectors.base import Detection
from .storage import ItemRow, Storage

DAY = 86400.0

EXPIRY_FRESH = "fresh"
EXPIRY_SOON = "soon"
EXPIRY_EXPIRED = "expired"
EXPIRY_UNKNOWN = "unknown"

#: За сколько суток до конца срока продукт помечается как «скоро испортится».
SOON_THRESHOLD_DAYS = 2.0


@dataclass
class Change:
    key: str
    kind: str  # added | removed | increased | decreased
    delta: int
    count: int

    def to_dict(self) -> dict[str, Any]:
        product = products.describe(self.key)
        return {
            "key": self.key,
            "label": product.label,
            "emoji": product.emoji,
            "kind": self.kind,
            "delta": self.delta,
            "count": self.count,
        }


class InventoryTracker:
    """Превращает поток «сырых» детекций в стабильный инвентарь.

    Детекторы шумят: продукт может пропасть на одном кадре из-за блика или руки
    перед камерой. Поэтому изменение количества применяется только после того,
    как оно подтвердилось на `confirmations` подряд идущих кадрах.
    """

    def __init__(
        self,
        storage: Storage,
        *,
        confirmations: int = 2,
        min_confidence: float = 0.35,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.storage = storage
        self.confirmations = max(1, confirmations)
        self.min_confidence = min_confidence
        self._clock = clock
        # key -> (предлагаемое количество, сколько кадров подряд оно наблюдается)
        self._pending: dict[str, tuple[int, int]] = {}

    # --- основной цикл --------------------------------------------------

    def observe(self, detections: Sequence[Detection]) -> list[Change]:
        """Обрабатывает детекции одного кадра и возвращает применённые изменения."""
        observed = self._aggregate(detections)
        current = {item.key: item for item in self.storage.get_items()}
        changes: list[Change] = []

        for key in set(observed) | set(current):
            item = current.get(key)
            # Продукты, добавленные руками, камерой не пересчитываем: пользователь
            # мог положить их в непросматриваемую зону.
            if item is not None and item.manual and key not in observed:
                continue

            target = observed.get(key, (0, 0.0))[0]
            existing = item.count if item else 0
            if target == existing:
                self._pending.pop(key, None)
                if item is not None and key in observed:
                    self._touch(item, observed[key][1])
                continue

            proposed, streak = self._pending.get(key, (target, 0))
            streak = streak + 1 if proposed == target else 1
            self._pending[key] = (target, streak)
            if streak < self.confirmations:
                continue

            self._pending.pop(key, None)
            confidence = observed.get(key, (0, item.confidence if item else 0.0))[1]
            changes.append(self._apply(key, target, confidence, item, source="camera"))

        return changes

    def _aggregate(self, detections: Iterable[Detection]) -> dict[str, tuple[int, float]]:
        counts: dict[str, tuple[int, float]] = {}
        for det in detections:
            if det.confidence < self.min_confidence:
                continue
            if products.is_ignored(det.raw_label or det.label) or products.is_ignored(det.key):
                continue
            count, best = counts.get(det.key, (0, 0.0))
            counts[det.key] = (count + 1, max(best, det.confidence))
        return counts

    def _touch(self, item: ItemRow, confidence: float) -> None:
        item.last_seen = self._clock()
        item.confidence = max(item.confidence, confidence)
        # Камера подтвердила ручную правку — дальше продукт снова ведёт себя обычно.
        item.manual = False
        self.storage.upsert_item(item)

    def _apply(
        self,
        key: str,
        target: int,
        confidence: float,
        item: ItemRow | None,
        *,
        source: str,
    ) -> Change:
        now = self._clock()
        previous = item.count if item else 0
        delta = target - previous

        if target <= 0:
            self.storage.delete_item(key)
            self.storage.add_event("removed", key, delta, 0, source)
            return Change(key=key, kind="removed", delta=delta, count=0)

        if item is None:
            row = ItemRow(
                key=key,
                count=target,
                first_seen=now,
                last_seen=now,
                added_at=now,
                expires_at=_expiry_for(key, now),
                confidence=confidence,
                manual=source == "manual",
            )
            self.storage.upsert_item(row)
            self.storage.add_event("added", key, delta, target, source)
            return Change(key=key, kind="added", delta=delta, count=target)

        # Срок годности не продлеваем, когда продукта стало больше: считаем,
        # что самая старая упаковка всё ещё в холодильнике.
        item.count = target
        item.last_seen = now
        item.confidence = max(item.confidence, confidence)
        item.manual = source == "manual"
        self.storage.upsert_item(item)
        kind = "increased" if delta > 0 else "decreased"
        self.storage.add_event(kind, key, delta, target, source)
        return Change(key=key, kind=kind, delta=delta, count=target)

    # --- ручные правки ---------------------------------------------------

    def set_count(self, key: str, count: int) -> Change:
        """Ручная правка из приложения: применяется сразу, без подтверждений."""
        self._pending.pop(key, None)
        item = self.storage.get_item(key)
        return self._apply(key, max(0, count), item.confidence if item else 1.0, item, source="manual")

    # --- представление ----------------------------------------------------

    def state(self) -> list[dict[str, Any]]:
        now = self._clock()
        items = []
        for item in self.storage.get_items():
            product = products.describe(item.key)
            expires_in = None if item.expires_at is None else (item.expires_at - now) / DAY
            items.append(
                {
                    "key": item.key,
                    "label": product.label,
                    "emoji": product.emoji,
                    "category": product.category,
                    "category_label": products.category_label(product.category),
                    "count": item.count,
                    "confidence": round(item.confidence, 3),
                    "manual": item.manual,
                    "added_at": item.added_at,
                    "last_seen": item.last_seen,
                    "expires_at": item.expires_at,
                    "expires_in_days": None if expires_in is None else round(expires_in, 2),
                    "freshness": _freshness(expires_in),
                }
            )
        items.sort(key=lambda row: (_FRESHNESS_ORDER[row["freshness"]], row["label"]))
        return items

    def shopping_list(self) -> list[dict[str, Any]]:
        """Что стоит купить: закончившиеся базовые продукты и то, что испортилось.

        Причина отдаётся кодом, а не готовой фразой: текст с правильным родом
        собирает интерфейс.
        """
        state = {row["key"]: row for row in self.state()}
        suggestions: list[dict[str, Any]] = []
        for product in products.staples():
            if product.key not in state:
                suggestions.append(_suggestion(product.key, product.label, product.emoji, "missing"))
        for row in state.values():
            if row["freshness"] == EXPIRY_EXPIRED:
                suggestions.append(_suggestion(row["key"], row["label"], row["emoji"], "expired"))
            elif row["freshness"] == EXPIRY_SOON:
                suggestions.append(_suggestion(row["key"], row["label"], row["emoji"], "expiring"))
        return suggestions


def _suggestion(key: str, label: str, emoji: str, reason: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "emoji": emoji,
        "gender": products.gender(key),
        "reason": reason,
    }


_FRESHNESS_ORDER = {
    EXPIRY_EXPIRED: 0,
    EXPIRY_SOON: 1,
    EXPIRY_FRESH: 2,
    EXPIRY_UNKNOWN: 3,
}


def _freshness(expires_in_days: float | None) -> str:
    if expires_in_days is None:
        return EXPIRY_UNKNOWN
    if expires_in_days <= 0:
        return EXPIRY_EXPIRED
    if expires_in_days <= SOON_THRESHOLD_DAYS:
        return EXPIRY_SOON
    return EXPIRY_FRESH


def _expiry_for(key: str, now: float) -> float | None:
    shelf_life = products.describe(key).shelf_life_days
    return None if shelf_life is None else now + shelf_life * DAY
