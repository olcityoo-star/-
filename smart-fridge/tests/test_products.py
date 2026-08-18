from app import products


def test_normalize_matches_russian_and_english_aliases():
    assert products.normalize("молоко") == "milk"
    assert products.normalize("Milk") == "milk"
    assert products.normalize("яйцо") == "eggs"
    assert products.normalize("hot dog") == "sausage"
    assert products.normalize("Помидор") == "tomato"


def test_normalize_finds_product_inside_phrase():
    assert products.normalize("зелёное яблоко") == "apple"
    assert products.normalize("бутылка молока 2.5%") == "milk"


def test_normalize_handles_russian_word_forms():
    assert products.normalize("помидоры") == "tomato"
    assert products.normalize("яблоки") == "apple"
    assert products.normalize("два пакета молока") == "milk"
    assert products.normalize("огурцов нет") == "cucumber"


def test_content_wins_over_container():
    assert products.normalize("бутылка кефира") == "kefir"
    assert products.normalize("банка") == "sauce"
    assert products.normalize("бутылка") == "bottle"


def test_normalize_keeps_unknown_products_as_custom_keys():
    key = products.normalize("кимчи")
    assert key == "custom:кимчи"
    assert products.describe(key).label == "Кимчи"
    assert products.describe(key).category == products.UNKNOWN_CATEGORY


def test_yo_and_punctuation_do_not_break_lookup():
    assert products.normalize("Ёгурт") != products.normalize("йогурт")  # опечатка остаётся своим продуктом
    assert products.normalize("огурцы!") == "cucumber"
    assert products.normalize("   ") == "custom:unknown"


def test_ignored_classes_are_recognised():
    assert products.is_ignored("person")
    assert products.is_ignored("Dining Table")
    assert not products.is_ignored("молоко")


def test_gender_is_detected_for_correct_wording():
    # «молоко появилось», «сметана появилась», «яйца появились»
    assert products.gender("milk") == "n"
    assert products.gender("sour_cream") == "f"
    assert products.gender("eggs") == "p"
    assert products.gender("cheese") == "m"
    assert products.gender("tomato") == "p"
    assert products.gender("carrot") == "f"
    assert products.gender("potato") == "m"
    assert products.gender("broccoli") == "f"
    assert products.gender("butter") == "n"  # «Масло сливочное» — по первому слову


def test_gender_is_guessed_for_unknown_products():
    assert products.gender(products.normalize("пахлава")) == "f"
    assert products.gender(products.normalize("рагу")) == "m"


def test_every_catalog_product_has_plausible_gender():
    assert {products.gender(key) for key in products.PRODUCTS} <= {"m", "f", "n", "p"}


def test_catalog_is_self_consistent():
    for product in products.PRODUCTS.values():
        assert product.category in products.CATEGORIES
        assert products.normalize(product.label) == product.key
        assert product.shelf_life_days is None or product.shelf_life_days > 0
    assert products.staples()
