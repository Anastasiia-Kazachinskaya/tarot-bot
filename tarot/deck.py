import json
import random
from pathlib import Path

from .spreads import SPREADS

CARDS_FILE = Path(__file__).parent / "cards.json"
IMAGES_DIR = Path(__file__).parent / "images" / "rider_waite"

MAJOR_ARCANA_IMAGES = {
    "Шут": "00_Fool.jpg",
    "Маг": "01_Magician.jpg",
    "Верховная Жрица": "02_High_Priestess.jpg",
    "Императрица": "03_Empress.jpg",
    "Император": "04_Emperor.jpg",
    "Иерофант": "05_Hierophant.jpg",
    "Влюблённые": "06_Lovers.jpg",
    "Колесница": "07_Chariot.jpg",
    "Сила": "08_Strength.jpg",
    "Отшельник": "09_Hermit.jpg",
    "Колесо Фортуны": "10_Wheel_of_Fortune.jpg",
    "Справедливость": "11_Justice.jpg",
    "Повешенный": "12_Hanged_Man.jpg",
    "Смерть": "13_Death.jpg",
    "Умеренность": "14_Temperance.jpg",
    "Дьявол": "15_Devil.jpg",
    "Башня": "16_Tower.jpg",
    "Звезда": "17_Star.jpg",
    "Луна": "18_Moon.jpg",
    "Солнце": "19_Sun.jpg",
    "Суд": "20_Judgement.jpg",
    "Мир": "21_World.jpg",
}

SUIT_PREFIXES = {
    "wands": "Wands",
    "cups": "Cups",
    "swords": "Swords",
    "pentacles": "Pents",
}

RANKS = {
    "Туз": 1,
    "Двойка": 2,
    "Тройка": 3,
    "Четвёрка": 4,
    "Пятёрка": 5,
    "Шестёрка": 6,
    "Семёрка": 7,
    "Восьмёрка": 8,
    "Девятка": 9,
    "Десятка": 10,
    "Паж": 11,
    "Рыцарь": 12,
    "Королева": 13,
    "Король": 14,
}

def load_deck() -> list[dict]:
    with open(CARDS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)

def get_card_image(card: dict) -> Path:
    """
    Возвращает путь к изображению карты.
    """

    if card["arcana"] == "major":
        filename = MAJOR_ARCANA_IMAGES[card["name"]]

    else:
        suit = card["suit"]
        prefix = SUIT_PREFIXES[suit]

        rank_name = card["name"].split()[0]
        rank = RANKS[rank_name]

        filename = f"{prefix}{rank:02d}.jpg"

    image_path = IMAGES_DIR / filename

    if not image_path.exists():
        raise FileNotFoundError(
            f"Изображение карты не найдено: {image_path}"
        )

    return image_path

def _apply_orientation(
    card: dict,
    reversed_cards: bool
) -> None:

    if reversed_cards:
        card["reversed"] = random.choice([True, False])
    else:
        card["reversed"] = False

    if card["reversed"]:
        card["meaning"] = card["reversed_meaning"]
    else:
        card["meaning"] = card["upright_meaning"]


def draw_cards(
    count: int = 3,
    reversed_cards: bool = True
) -> list[dict]:

    deck = load_deck()

    if count > len(deck):
        raise ValueError(
            "Нельзя вытянуть больше карт, чем есть в колоде"
        )

    cards = random.sample(deck, count)

    for card in cards:
        _apply_orientation(card, reversed_cards)

    return cards


def draw_single_card(
    exclude_names: set[str] | None = None,
    reversed_cards: bool = True
) -> dict:
    """
    Вытягивает одну дополнительную (уточняющую) карту,
    не повторяя карты, уже лежащие в раскладе.
    """

    deck = load_deck()

    if exclude_names:
        deck = [
            card for card in deck
            if card["name"] not in exclude_names
        ]

    if not deck:
        raise ValueError(
            "Не осталось карт для уточняющего вытягивания"
        )

    card = random.choice(deck)

    _apply_orientation(card, reversed_cards)

    return card


def make_spread(
    spread_name: str,
    reversed_cards: bool = True
) -> list[dict]:

    if spread_name not in SPREADS:
        raise ValueError(
            f"Неизвестный расклад: {spread_name}"
        )

    positions = SPREADS[spread_name]["cards"]

    cards = draw_cards(
        count=len(positions),
        reversed_cards=reversed_cards
    )

    result = []

    for position, card in zip(positions, cards):
        result.append({
            "position": position,
            "card": card
        })

    return result


# =================================================
# Поиск карты по названию (команда /card)
# =================================================

SUIT_NAMES = {
    "wands": "Жезлов",
    "cups": "Кубков",
    "swords": "Мечей",
    "pentacles": "Пентаклей",
}

# Как масть могут написать в запросе.
SUIT_ALIASES = {
    "жезл": "wands",
    "жезлы": "wands",
    "жезлов": "wands",
    "посох": "wands",
    "посохи": "wands",
    "посохов": "wands",
    "кубок": "cups",
    "кубки": "cups",
    "кубков": "cups",
    "чаша": "cups",
    "чаши": "cups",
    "чаш": "cups",
    "меч": "swords",
    "мечи": "swords",
    "мечей": "swords",
    "пентакль": "pentacles",
    "пентакли": "pentacles",
    "пентаклей": "pentacles",
    "монета": "pentacles",
    "монеты": "pentacles",
    "монет": "pentacles",
    "диск": "pentacles",
    "диски": "pentacles",
    "дисков": "pentacles",
}

# Как может быть записан ранг: цифрой или другим словом.
RANK_ALIASES = {
    "1": "Туз",
    "туза": "Туз",
    "2": "Двойка",
    "двойки": "Двойка",
    "двойку": "Двойка",
    "две": "Двойка",
    "3": "Тройка",
    "тройки": "Тройка",
    "тройку": "Тройка",
    "три": "Тройка",
    "4": "Четвёрка",
    "четверка": "Четвёрка",
    "четыре": "Четвёрка",
    "5": "Пятёрка",
    "пятерка": "Пятёрка",
    "пять": "Пятёрка",
    "6": "Шестёрка",
    "шестерка": "Шестёрка",
    "шесть": "Шестёрка",
    "7": "Семёрка",
    "семерка": "Семёрка",
    "семь": "Семёрка",
    "8": "Восьмёрка",
    "восьмерка": "Восьмёрка",
    "восемь": "Восьмёрка",
    "9": "Девятка",
    "девятки": "Девятка",
    "девять": "Девятка",
    "10": "Десятка",
    "десятки": "Десятка",
    "десять": "Десятка",
    "11": "Паж",
    "пажа": "Паж",
    "валет": "Паж",
    "12": "Рыцарь",
    "рыцаря": "Рыцарь",
    "конь": "Рыцарь",
    "13": "Королева",
    "королевы": "Королева",
    "дама": "Королева",
    "14": "Король",
    "короля": "Король",
}


def normalize(text: str) -> str:
    """
    Приводит запрос к сравнимому виду: нижний регистр, «ё» как «е»,
    без лишних пробелов и знаков препинания.
    """

    text = text.lower().replace("ё", "е")

    text = "".join(
        char if char.isalnum() else " "
        for char in text
    )

    return " ".join(text.split())


def _normalized_query(query: str) -> str:
    """
    Переводит «10 мечей», «туз чаш», «дама монет» в нормализованное
    название из колоды: «десятка мечей», «туз кубков» и так далее.
    """

    words = normalize(query).split()

    converted = []

    for word in words:
        if word in RANK_ALIASES:
            converted.append(normalize(RANK_ALIASES[word]))
            continue

        if word in SUIT_ALIASES:
            suit = SUIT_ALIASES[word]
            converted.append(normalize(SUIT_NAMES[suit]))
            continue

        converted.append(word)

    return " ".join(converted)


def find_cards(query: str) -> list[dict]:
    """
    Ищет карту по названию.

    Возвращает список совпадений: пустой — не нашлось, один элемент —
    точное попадание, несколько — запрос слишком общий («мечей»),
    и пользователю нужно показать варианты.
    """

    normalized = _normalized_query(query)

    if not normalized:
        return []

    deck = load_deck()

    names = {
        normalize(card["name"]): card
        for card in deck
    }

    if normalized in names:
        return [names[normalized]]

    # Совпадение по всем словам запроса в любом порядке:
    # «мечей туз» тоже находится.
    words = normalized.split()

    matches = [
        card for name, card in names.items()
        if all(word in name for word in words)
    ]

    if matches:
        return matches

    # Последняя попытка: запрос — начало названия («отшель»).
    return [
        card for name, card in names.items()
        if name.startswith(normalized)
    ]
