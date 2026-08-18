"""Справочник типичных продуктов холодильника и сопоставление имён."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


@dataclass(frozen=True)
class ProductKind:
    key: str
    name_ru: str
    name_en: str
    emoji: str
    category: str
    aliases: tuple[str, ...] = ()


CATEGORIES = {
    "dairy": "Молочка",
    "produce": "Овощи и фрукты",
    "meat": "Мясо и рыба",
    "drinks": "Напитки",
    "sauce": "Соусы",
    "leftovers": "Готовое",
    "bakery": "Хлеб",
    "other": "Другое",
}


CATALOG: tuple[ProductKind, ...] = (
    ProductKind("milk", "Молоко", "milk", "🥛", "dairy", ("молоко", "milk carton", "молоко питьевое")),
    ProductKind("kefir", "Кефир", "kefir", "🥛", "dairy", ("кефир",)),
    ProductKind("yogurt", "Йогурт", "yogurt", "🍦", "dairy", ("йогурт", "yoghurt")),
    ProductKind("sour_cream", "Сметана", "sour cream", "🥣", "dairy", ("сметана",)),
    ProductKind("cottage_cheese", "Творог", "cottage cheese", "🧀", "dairy", ("творог", "quark")),
    ProductKind("cheese", "Сыр", "cheese", "🧀", "dairy", ("сыр", "cheese slice", "cheese pack")),
    ProductKind("butter", "Масло", "butter", "🧈", "dairy", ("масло сливочное", "butter")),
    ProductKind("eggs", "Яйца", "eggs", "🥚", "dairy", ("яйца", "яйцо", "egg carton", "egg")),
    ProductKind("mayonnaise", "Майонез", "mayonnaise", "🫙", "sauce", ("майонез", "mayo")),
    ProductKind("ketchup", "Кетчуп", "ketchup", "🍅", "sauce", ("кетчуп",)),
    ProductKind("mustard", "Горчица", "mustard", "🟡", "sauce", ("горчица",)),
    ProductKind("sauce", "Соус", "sauce", "🫙", "sauce", ("соус", "dressing")),
    ProductKind("tomato", "Помидоры", "tomato", "🍅", "produce", ("помидор", "томаты", "tomatoes")),
    ProductKind("cucumber", "Огурцы", "cucumber", "🥒", "produce", ("огурец", "cucumbers")),
    ProductKind("pepper", "Перец", "bell pepper", "🫑", "produce", ("перец", "paprika", "pepper")),
    ProductKind("carrot", "Морковь", "carrot", "🥕", "produce", ("морковь", "carrots")),
    ProductKind("onion", "Лук", "onion", "🧅", "produce", ("лук", "onions")),
    ProductKind("garlic", "Чеснок", "garlic", "🧄", "produce", ("чеснок",)),
    ProductKind("lettuce", "Салат", "lettuce", "🥬", "produce", ("салат", "зелень", "herbs", "greens", "parsley", "dill", "укроп", "петрушка")),
    ProductKind("apple", "Яблоки", "apple", "🍎", "produce", ("яблоко", "яблоки", "apples")),
    ProductKind("banana", "Бананы", "banana", "🍌", "produce", ("банан", "бананы", "bananas")),
    ProductKind("orange", "Апельсины", "orange", "🍊", "produce", ("апельсин", "oranges")),
    ProductKind("lemon", "Лимон", "lemon", "🍋", "produce", ("лимон", "лимончик")),
    ProductKind("berries", "Ягоды", "berries", "🫐", "produce", ("ягоды", "клубника", "strawberry", "blueberry")),
    ProductKind("chicken", "Курица", "chicken", "🍗", "meat", ("курица", "куриное", "chicken breast")),
    ProductKind("sausage", "Колбаса", "sausage", "🌭", "meat", ("колбаса", "сосиски", "hot dog", "wieners")),
    ProductKind("ham", "Ветчина", "ham", "🥓", "meat", ("ветчина", "буженина")),
    ProductKind("fish", "Рыба", "fish", "🐟", "meat", ("рыба", "salmon", "лосось", "селедка")),
    ProductKind("water", "Вода", "water", "💧", "drinks", ("вода", "water bottle")),
    ProductKind("juice", "Сок", "juice", "🧃", "drinks", ("сок", "juice carton")),
    ProductKind("soda", "Газировка", "soda", "🥤", "drinks", ("газировка", "cola", "лимонад", "sparkling")),
    ProductKind("beer", "Пиво", "beer", "🍺", "drinks", ("пиво", "beer can", "beer bottle")),
    ProductKind("wine", "Вино", "wine", "🍷", "drinks", ("вино", "wine bottle")),
    ProductKind("bread", "Хлеб", "bread", "🍞", "bakery", ("хлеб", "батон", "булка", "loaf")),
    ProductKind("leftovers", "Остатки еды", "leftovers", "🍲", "leftovers", ("остатки", "контейнер", "lunch box", "food container", "tupperware")),
    ProductKind("bottle", "Бутылка", "bottle", "🍾", "drinks", ("бутылка",)),
    ProductKind("jar", "Банка", "jar", "🫙", "other", ("банка", "консервы", "canned")),
)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.strip().lower()
    text = re.sub(r"[«»\"'`.,!?:;()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _rebuild_alias_index() -> dict[str, ProductKind]:
    index: dict[str, ProductKind] = {}
    for kind in CATALOG:
        names = (kind.key, kind.name_ru, kind.name_en, *kind.aliases)
        for name in names:
            index[normalize_name(name)] = kind
    return index


ALIAS_INDEX = _rebuild_alias_index()


YOLO_CLASSES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            kind.name_en
            for kind in CATALOG
        ]
        + [
            "milk carton",
            "egg carton",
            "yogurt cup",
            "cheese pack",
            "plastic container",
            "vegetable",
            "fruit",
        ]
    )
)


def match_product(raw_name: str) -> ProductKind | None:
    needle = normalize_name(raw_name)
    if not needle:
        return None
    if needle in ALIAS_INDEX:
        return ALIAS_INDEX[needle]
    for alias, kind in ALIAS_INDEX.items():
        if alias and (alias in needle or needle in alias):
            return kind
    best: ProductKind | None = None
    best_score = 0.72
    for kind in CATALOG:
        candidates = (kind.name_ru, kind.name_en, kind.key, *kind.aliases)
        for candidate in candidates:
            score = SequenceMatcher(None, needle, normalize_name(candidate)).ratio()
            if score > best_score:
                best_score = score
                best = kind
    return best


def describe(raw_name: str) -> dict[str, str]:
    kind = match_product(raw_name)
    if kind:
        return {
            "key": kind.key,
            "name": kind.name_ru,
            "name_en": kind.name_en,
            "emoji": kind.emoji,
            "category": kind.category,
            "category_label": CATEGORIES[kind.category],
        }
    cleaned = (raw_name or "").strip() or "Неизвестно"
    return {
        "key": normalize_name(cleaned).replace(" ", "_") or "unknown",
        "name": cleaned[:1].upper() + cleaned[1:] if cleaned else "Неизвестно",
        "name_en": cleaned,
        "emoji": "🧊",
        "category": "other",
        "category_label": CATEGORIES["other"],
    }
