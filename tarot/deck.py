import json
import random
from pathlib import Path

from .spreads import SPREADS

CARDS_FILE = Path(__file__).parent / "cards.json"


def load_deck() -> list[dict]:
    with open(CARDS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


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

        if reversed_cards:
            card["reversed"] = random.choice([True, False])
        else:
            card["reversed"] = False

        if card["reversed"]:
            card["meaning"] = card["reversed_meaning"]
        else:
            card["meaning"] = card["upright_meaning"]

    return cards


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