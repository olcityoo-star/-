from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.inventory import apply_scan, shopping_list
from app.models import Base, Item, Scan
from app.schemas import Box, DetectedItem


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _item(name: str, key: str) -> DetectedItem:
    return DetectedItem(name=name, key=key, emoji="🥛", category="dairy", count=1, box=Box())


def _scan(session: Session, items: list[DetectedItem]):
    scan = Scan(source="test", image_path="x.jpg", created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    session.add(scan)
    session.flush()
    return apply_scan(session, items, scan)


def test_first_scan_adds_items():
    session = _session()
    events = _scan(session, [_item("Молоко", "milk"), _item("Яйца", "eggs")])
    session.commit()
    assert {row.key for row in session.query(Item).all()} == {"milk", "eggs"}
    assert {event.kind for event in events} == {"added"}


def test_missing_twice_marks_gone_and_shopping():
    session = _session()
    _scan(session, [_item("Молоко", "milk")])
    _scan(session, [])
    events = _scan(session, [])
    session.commit()

    milk = session.query(Item).filter(Item.key == "milk").one()
    assert milk.status == "gone"
    assert milk.missed_scans >= 2
    assert any(event.kind == "removed" for event in events)
    assert shopping_list(session)[0]["key"] == "milk"


def test_item_returns_after_being_gone():
    session = _session()
    _scan(session, [_item("Молоко", "milk")])
    _scan(session, [])
    _scan(session, [])
    events = _scan(session, [_item("Молоко", "milk")])
    session.commit()
    milk = session.query(Item).filter(Item.key == "milk").one()
    assert milk.status == "in"
    assert any("Снова" in event.message for event in events)
