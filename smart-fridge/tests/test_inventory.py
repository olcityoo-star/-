import pytest

from app import products
from app.detectors.base import Detection
from app.inventory import DAY, InventoryTracker
from app.storage import Storage


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, days: float) -> None:
        self.now += days * DAY


def detection(name: str, confidence: float = 0.9) -> Detection:
    key = products.normalize(name)
    return Detection(key=key, label=products.describe(key).label, confidence=confidence, raw_label=name)


@pytest.fixture
def tracker(tmp_path):
    storage = Storage(tmp_path / "test.db")
    clock = FakeClock()
    instance = InventoryTracker(storage, confirmations=2, min_confidence=0.4, clock=clock)
    instance.clock = clock  # для удобства в тестах
    yield instance
    storage.close()


def keys(tracker):
    return {row["key"]: row["count"] for row in tracker.state()}


def test_single_frame_does_not_change_inventory(tracker):
    changes = tracker.observe([detection("молоко")])
    assert changes == []
    assert keys(tracker) == {}


def test_item_appears_after_confirmation(tracker):
    tracker.observe([detection("молоко")])
    changes = tracker.observe([detection("молоко")])
    assert [(c.key, c.kind, c.count) for c in changes] == [("milk", "added", 1)]
    assert keys(tracker) == {"milk": 1}


def test_flicker_does_not_remove_item(tracker):
    for _ in range(2):
        tracker.observe([detection("молоко")])
    assert keys(tracker) == {"milk": 1}

    # Рука закрыла молоко на одном кадре — инвентарь не должен дрогнуть.
    assert tracker.observe([]) == []
    assert keys(tracker) == {"milk": 1}

    tracker.observe([detection("молоко")])
    assert keys(tracker) == {"milk": 1}


def test_item_removed_after_two_empty_frames(tracker):
    for _ in range(2):
        tracker.observe([detection("молоко")])
    tracker.observe([])
    changes = tracker.observe([])
    assert [(c.key, c.kind) for c in changes] == [("milk", "removed")]
    assert keys(tracker) == {}


def test_counts_multiple_instances_of_same_product(tracker):
    frame = [detection("помидор"), detection("помидор"), detection("помидор")]
    tracker.observe(frame)
    tracker.observe(frame)
    assert keys(tracker) == {"tomato": 3}

    smaller = [detection("помидор")]
    tracker.observe(smaller)
    changes = tracker.observe(smaller)
    assert [(c.kind, c.delta, c.count) for c in changes] == [("decreased", -2, 1)]


def test_low_confidence_detections_are_ignored(tracker):
    weak = [detection("молоко", confidence=0.2)]
    tracker.observe(weak)
    tracker.observe(weak)
    assert keys(tracker) == {}


def test_non_food_classes_are_ignored(tracker):
    frame = [detection("person"), detection("fork"), detection("молоко")]
    tracker.observe(frame)
    tracker.observe(frame)
    assert keys(tracker) == {"milk": 1}


def test_expiry_is_calculated_from_shelf_life(tracker):
    tracker.observe([detection("молоко")])
    tracker.observe([detection("молоко")])
    row = tracker.state()[0]
    assert row["freshness"] == "fresh"
    assert row["expires_in_days"] == pytest.approx(5.0)

    tracker.clock.advance(4)
    assert tracker.state()[0]["freshness"] == "soon"

    tracker.clock.advance(2)
    assert tracker.state()[0]["freshness"] == "expired"


def test_products_without_shelf_life_are_never_expired(tracker):
    frame = [detection("вода")]
    tracker.observe(frame)
    tracker.observe(frame)
    tracker.clock.advance(400)
    row = tracker.state()[0]
    assert row["expires_at"] is None
    assert row["freshness"] == "unknown"


def test_restocking_does_not_extend_expiry(tracker):
    frame = [detection("молоко")]
    tracker.observe(frame)
    tracker.observe(frame)
    first_expiry = tracker.state()[0]["expires_at"]

    tracker.clock.advance(3)
    bigger = [detection("молоко"), detection("молоко")]
    tracker.observe(bigger)
    tracker.observe(bigger)
    assert tracker.state()[0]["expires_at"] == first_expiry


def test_manual_edit_applies_immediately_and_survives_missing_frames(tracker):
    change = tracker.set_count("cheese", 2)
    assert (change.kind, change.count) == ("added", 2)
    assert keys(tracker) == {"cheese": 2}

    # Сыр лежит в дверце и в кадр не попадает — камера не должна его стереть.
    for _ in range(5):
        tracker.observe([])
    assert keys(tracker) == {"cheese": 2}


def test_camera_takes_over_after_it_sees_manual_item(tracker):
    tracker.set_count("cheese", 2)
    frame = [detection("сыр")]
    tracker.observe(frame)
    tracker.observe(frame)
    assert keys(tracker) == {"cheese": 1}

    tracker.observe([])
    tracker.observe([])
    assert keys(tracker) == {}


def test_manual_zero_removes_item(tracker):
    tracker.set_count("cheese", 1)
    change = tracker.set_count("cheese", 0)
    assert change.kind == "removed"
    assert keys(tracker) == {}


def test_events_are_recorded(tracker):
    frame = [detection("молоко")]
    tracker.observe(frame)
    tracker.observe(frame)
    tracker.observe([])
    tracker.observe([])
    kinds = [(event.kind, event.item_key) for event in tracker.storage.get_events()]
    assert kinds == [("removed", "milk"), ("added", "milk")]


def test_shopping_list_suggests_missing_staples_and_spoiled_food(tracker):
    frame = [detection("молоко")]
    tracker.observe(frame)
    tracker.observe(frame)
    tracker.clock.advance(6)

    suggestions = {row["key"]: row["reason"] for row in tracker.shopping_list()}
    assert suggestions["milk"] == "expired"
    assert suggestions["bread"] == "missing"
    assert suggestions["eggs"] == "missing"


def test_state_sorts_problems_first(tracker):
    frame = [detection("вода"), detection("молоко")]
    tracker.observe(frame)
    tracker.observe(frame)
    tracker.clock.advance(6)
    assert [row["key"] for row in tracker.state()] == ["milk", "water"]


def test_unknown_product_from_vlm_lands_in_inventory(tracker):
    frame = [detection("кимчи")]
    tracker.observe(frame)
    tracker.observe(frame)
    row = tracker.state()[0]
    assert row["key"] == "custom:кимчи"
    assert row["label"] == "Кимчи"
    assert row["category_label"] == "Прочее"
