"""Каталог продуктов: нормализация названий от детектора, сроки хранения, категории."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    category: str
    emoji: str = "🍽"
    #: Сколько суток продукт остаётся свежим в холодильнике с момента появления в кадре.
    #: None — срок не отслеживаем (вода, приправы и т.п.).
    shelf_life_days: int | None = None
    #: Продукт, который принято держать в холодильнике всегда: попадает в список покупок,
    #: когда заканчивается.
    staple: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)


CATEGORIES: dict[str, str] = {
    "dairy": "Молочное",
    "meat": "Мясо и рыба",
    "vegetables": "Овощи",
    "fruits": "Фрукты",
    "drinks": "Напитки",
    "ready": "Готовая еда",
    "grocery": "Бакалея и соусы",
    "other": "Прочее",
}

_CATALOG: tuple[Product, ...] = (
    # Молочное
    Product("milk", "Молоко", "dairy", "🥛", 5, True, ("молоко", "milk carton", "пакет молока")),
    Product("cheese", "Сыр", "dairy", "🧀", 14, True, ("сыр", "cheese slice")),
    Product("butter", "Масло сливочное", "dairy", "🧈", 21, False, ("масло", "сливочное масло")),
    Product("yogurt", "Йогурт", "dairy", "🥤", 7, False, ("йогурт", "yoghurt", "danone")),
    Product("sour_cream", "Сметана", "dairy", "🥣", 7, False, ("сметана", "smetana")),
    Product("cottage_cheese", "Творог", "dairy", "🧆", 5, False, ("творог", "curd", "quark")),
    Product("eggs", "Яйца", "dairy", "🥚", 21, True, ("яйца", "яйцо", "egg", "egg carton")),
    Product("kefir", "Кефир", "dairy", "🥛", 5, False, ("кефир", "ryazhenka", "ряженка")),
    # Мясо и рыба
    Product("meat", "Мясо", "meat", "🥩", 2, False, ("мясо", "beef", "pork", "steak", "говядина", "свинина")),
    Product("chicken", "Курица", "meat", "🍗", 2, False, ("курица", "куриное филе", "poultry")),
    Product("fish", "Рыба", "meat", "🐟", 2, False, ("рыба", "salmon", "лосось", "селёдка")),
    Product("sausage", "Колбаса", "meat", "🌭", 7, False, ("колбаса", "сосиски", "ham", "ветчина", "hot dog")),
    # Овощи
    Product("tomato", "Помидоры", "vegetables", "🍅", 7, False, ("помидор", "томат", "tomatoes")),
    Product("cucumber", "Огурцы", "vegetables", "🥒", 7, False, ("огурец", "cucumbers", "pickle")),
    Product("carrot", "Морковь", "vegetables", "🥕", 21, False, ("морковь", "carrots")),
    Product("broccoli", "Брокколи", "vegetables", "🥦", 7, False, ("брокколи", "капуста брокколи")),
    Product("pepper", "Перец", "vegetables", "🫑", 10, False, ("перец", "болгарский перец", "bell pepper")),
    Product("cabbage", "Капуста", "vegetables", "🥬", 21, False, ("капуста", "салат", "lettuce", "greens", "зелень")),
    Product("onion", "Лук", "vegetables", "🧅", 30, False, ("лук", "onions")),
    Product("potato", "Картофель", "vegetables", "🥔", 30, False, ("картошка", "картофель", "potatoes")),
    Product("mushroom", "Грибы", "vegetables", "🍄", 5, False, ("грибы", "шампиньоны", "mushrooms")),
    # Фрукты
    Product("apple", "Яблоки", "fruits", "🍎", 21, False, ("яблоко", "apples")),
    Product("banana", "Бананы", "fruits", "🍌", 5, False, ("банан", "bananas")),
    Product("orange", "Апельсины", "fruits", "🍊", 14, False, ("апельсин", "мандарин", "tangerine", "oranges")),
    Product("lemon", "Лимон", "fruits", "🍋", 21, False, ("лимон", "lime", "лайм")),
    Product("grapes", "Виноград", "fruits", "🍇", 7, False, ("виноград", "grape")),
    Product("berries", "Ягоды", "fruits", "🍓", 3, False, ("ягоды", "клубника", "strawberry", "малина")),
    # Напитки
    Product("water", "Вода", "drinks", "💧", None, True, ("вода", "water bottle", "минералка")),
    Product("juice", "Сок", "drinks", "🧃", 5, False, ("сок", "juice box", "морс")),
    Product("beer", "Пиво", "drinks", "🍺", None, False, ("пиво", "beer can", "beer bottle")),
    Product("wine", "Вино", "drinks", "🍷", None, False, ("вино", "wine glass", "wine bottle")),
    Product("soda", "Газировка", "drinks", "🥤", None, False, ("газировка", "cola", "кола", "лимонад")),
    Product("bottle", "Бутылка", "drinks", "🍾", None, False, ("бутылка", "bottles")),
    # Готовая еда
    Product("pizza", "Пицца", "ready", "🍕", 3, False, ("пицца",)),
    Product("sandwich", "Бутерброд", "ready", "🥪", 2, False, ("бутерброд", "сэндвич", "burger", "бургер")),
    Product("soup", "Суп", "ready", "🍲", 3, False, ("суп", "борщ", "bowl", "кастрюля", "миска")),
    Product("cake", "Торт", "ready", "🍰", 3, False, ("торт", "пирожное", "donut", "пончик")),
    Product("leftovers", "Контейнер с едой", "ready", "🥡", 3, False, ("контейнер", "остатки", "food container")),
    # Бакалея и соусы
    Product("bread", "Хлеб", "grocery", "🍞", 5, True, ("хлеб", "батон", "булка", "toast")),
    Product("ketchup", "Кетчуп", "grocery", "🍅", 60, False, ("кетчуп", "томатный соус")),
    Product("mayonnaise", "Майонез", "grocery", "🥫", 30, False, ("майонез", "mayo")),
    Product("mustard", "Горчица", "grocery", "🥫", 90, False, ("горчица",)),
    Product("jam", "Варенье", "grocery", "🍯", 90, False, ("варенье", "джем", "honey", "мёд")),
    Product("sauce", "Соус", "grocery", "🫙", 30, False, ("соус", "банка", "jar", "can")),
    Product("eggs_box", "Лоток яиц", "dairy", "🥚", 21, False, ("лоток яиц",)),
)

PRODUCTS: dict[str, Product] = {p.key: p for p in _CATALOG}

UNKNOWN_CATEGORY = "other"
#: Срок хранения по умолчанию для продукта, которого нет в каталоге.
DEFAULT_SHELF_LIFE_DAYS = 7


def _slug(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"[\s\-]+", "_", text.strip())
    return text.strip("_")


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for product in _CATALOG:
        index[_slug(product.key)] = product.key
        index[_slug(product.label)] = product.key
        for alias in product.aliases:
            index.setdefault(_slug(alias), product.key)
    return index


_ALIASES = _build_alias_index()

#: Слова-«тары»: если в названии есть и они, и сам продукт, продукт важнее.
#: «Бутылка молока» — это молоко, а не бутылка.
_GENERIC_KEYS = frozenset({"bottle", "sauce", "soup", "leftovers"})

_MIN_STEM = 4


def _build_stem_index() -> dict[str, str]:
    """Индекс усечённых основ — грубая замена морфологии.

    Детектор и языковая модель называют продукт в любом падеже и числе
    («молока», «помидоры»), а держать все формы в каталоге неудобно.
    """
    stems: dict[str, str] = {}
    for alias, key in _ALIASES.items():
        if "_" in alias or len(alias) < _MIN_STEM + 1:
            continue
        for cut in (1, 2):
            stem = alias[:-cut]
            if len(stem) >= _MIN_STEM:
                stems.setdefault(stem, key)
    return stems


_STEMS = _build_stem_index()


def _match_word(word: str) -> tuple[str, int] | None:
    """Ищет продукт по слову. Возвращает (ключ, качество), где 0 — точное совпадение."""
    if word in _ALIASES:
        return _ALIASES[word], 0
    for length in range(min(len(word), 10), _MIN_STEM - 1, -1):
        stem = word[:length]
        if stem in _STEMS:
            return _STEMS[stem], 1
    return None

#: Названия классов, которые детектор общего назначения (COCO) выдаёт в холодильнике,
#: но которые не являются продуктами. Их отбрасываем, чтобы не засорять инвентарь.
IGNORED_CLASSES = frozenset(
    {
        "person",
        "fork",
        "knife",
        "spoon",
        "cup",
        "dining_table",
        "refrigerator",
        "oven",
        "sink",
        "clock",
        "tv",
        "cell_phone",
        "hand",
        "рука",
        "полка",
        "shelf",
    }
)


def is_ignored(raw_name: str) -> bool:
    return _slug(raw_name) in IGNORED_CLASSES


def normalize(raw_name: str) -> str:
    """Приводит произвольное название от детектора к ключу каталога.

    Незнакомые продукты не отбрасываются: для них возвращается ключ вида
    ``custom:кимчи``, чтобы модель со свободным словарём (VLM) могла добавлять
    в инвентарь то, чего нет в каталоге.
    """
    slug = _slug(raw_name)
    if not slug:
        return "custom:unknown"
    if slug in _ALIASES:
        return _ALIASES[slug]

    # «зелёное яблоко», «бутылка молока 2.5%» — ищем знакомые слова во фразе.
    words = slug.split("_")
    candidates: list[tuple[int, int, int, str]] = []
    for size in (2, 1):
        for start in range(len(words) - size + 1):
            piece = "_".join(words[start : start + size])
            if piece in _ALIASES:
                candidates.append((int(_ALIASES[piece] in _GENERIC_KEYS), 0, -len(piece), _ALIASES[piece]))
            elif size == 1:
                match = _match_word(piece)
                if match is not None:
                    key, quality = match
                    candidates.append((int(key in _GENERIC_KEYS), quality, -len(piece), key))
    if candidates:
        return min(candidates)[3]
    return f"custom:{slug}"


def describe(key: str) -> Product:
    """Возвращает описание продукта по ключу, синтезируя его для незнакомых продуктов."""
    known = PRODUCTS.get(key)
    if known is not None:
        return known
    raw = key.split(":", 1)[1] if key.startswith("custom:") else key
    label = raw.replace("_", " ").strip().capitalize() or "Неизвестный продукт"
    return Product(
        key=key,
        label=label,
        category=UNKNOWN_CATEGORY,
        emoji="🍽",
        shelf_life_days=DEFAULT_SHELF_LIFE_DAYS,
    )


#: Род названия продукта нужен интерфейсу, чтобы писать «молоко появилось»,
#: а не «молоко появился». m — мужской, f — женский, n — средний, p — множественное.
_GENDER_OVERRIDES = {
    "eggs": "p",
    "broccoli": "f",
    "potato": "m",
}

_GENDER_BY_ENDING = {
    "а": "f",
    "я": "f",
    "ь": "f",
    "о": "n",
    "е": "n",
    "ы": "p",
    "и": "p",
}


def gender(key: str) -> str:
    """Определяет род названия продукта.

    Каталог небольшой, но модель со свободным словарём приносит произвольные
    названия, поэтому род угадывается по окончанию, а исключения перечислены явно.
    """
    if key in _GENDER_OVERRIDES:
        return _GENDER_OVERRIDES[key]
    head = describe(key).label.split()[0].lower() if describe(key).label.strip() else ""
    return _GENDER_BY_ENDING.get(head[-1:], "m")


def category_label(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES[UNKNOWN_CATEGORY])


def staples() -> list[Product]:
    return [p for p in _CATALOG if p.staple]
