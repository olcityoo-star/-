from app.detector import _parse_items_payload


def test_parse_vision_json():
    items = _parse_items_payload(
        {
            "items": [
                {"name": "milk", "name_ru": "Молоко", "count": 2, "confidence": 0.9},
                {"name": "eggs", "count": 1},
            ]
        }
    )
    keys = {item.key: item for item in items}
    assert keys["milk"].name == "Молоко"
    assert keys["milk"].count == 2
    assert keys["eggs"].emoji == "🥚"
