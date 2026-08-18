from app.catalog import describe, match_product, normalize_name


def test_normalize_strips_punctuation():
    assert normalize_name("  Молоко, ") == "молоко"


def test_match_russian_and_english_aliases():
    assert match_product("молоко питьевое").key == "milk"
    assert match_product("Yoghurt").key == "yogurt"
    assert match_product("egg carton").key == "eggs"


def test_describe_unknown_keeps_label():
    info = describe("кимчи")
    assert info["name"].lower().startswith("кимчи")
    assert info["category"] == "other"
    assert info["emoji"]
