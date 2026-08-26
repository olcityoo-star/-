from fridge.sync import build_sync_plan, parse_expiry, shopping_list, suggest_name_from_ocr, similarity


def test_parse_expiry_ru():
    assert parse_expiry("Годен до 15.09.2026") == "2026-09-15"
    assert parse_expiry("best before 01/10/26") == "2026-10-01"
    assert parse_expiry("2026-12-31") == "2026-12-31"


def test_suggest_name_from_ocr():
    text = "ПРОСТОКВАШИНО\nМолоко 2.5%\nГоден до 20.09.2026"
    assert "ПРОСТОКВАШИНО" in suggest_name_from_ocr(text, "Бутылка")


def test_similarity_and_sync_plan():
    assert similarity("Молоко", "молоко 2.5%") > 0.7
    items = [
        {"id": 1, "name": "Яблоко", "quantity": 1, "unit": "шт", "category": "фрукты", "expires_on": None},
        {"id": 2, "name": "Молоко", "quantity": 1, "unit": "л", "category": "молочка", "expires_on": "2026-09-01"},
    ]
    detections = [
        {"name": "Яблоко", "accepted": True, "quantity": 1},
        {"name": "Апельсин", "accepted": True, "quantity": 2},
    ]
    plan = build_sync_plan(items, detections)
    assert plan["summary"]["kept"] == 1
    assert plan["summary"]["added"] == 1
    assert plan["summary"]["removed"] == 1
    assert plan["removed"][0]["name"] == "Молоко"


def test_shopping_list():
    items = [
        {"id": 1, "name": "Сыр", "expires_on": "2020-01-01", "quantity": 1, "unit": "шт"},
        {"id": 2, "name": "Хлеб", "expires_on": None, "quantity": 1, "unit": "шт"},
    ]
    need = shopping_list(items)
    assert len(need) == 1
    assert need[0]["name"] == "Сыр"
