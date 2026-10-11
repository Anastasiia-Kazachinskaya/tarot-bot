"""
Дневник в виде текста: выгрузка истории (/export) и итоги месяца (/month).
Здесь только форматирование и подсчёты, без Telegram.
"""

import html
import json
from collections import Counter
from datetime import date

from .tarot.spreads import SPREADS

MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def spread_title(spread_name: str) -> str:
    return SPREADS.get(spread_name, {}).get("name", spread_name)


def parse_spread(spread_json: str) -> list[dict]:
    try:
        spread = json.loads(spread_json)
    except (TypeError, ValueError):
        return []

    return spread if isinstance(spread, list) else []


def format_cards(spread_json: str) -> str:
    spread = parse_spread(spread_json)

    if not spread:
        return "  (не удалось разобрать карты)"

    lines = []

    for index, item in enumerate(spread, start=1):
        card = item.get("card", {})
        position = item.get("position", "?")
        name = card.get("name", "?")
        orientation = "перевёрнутая" if card.get("reversed") else "прямая"

        lines.append(f"  {index}. {position} — {name} ({orientation})")

    return "\n".join(lines)


def format_reading(row) -> str:
    parts = [
        f"Дата: {row['created_at']}",
        f"Расклад: {spread_title(row['spread_name'])}",
        "",
        "Вопрос:",
        row["question"],
        "",
        "Карты:",
        format_cards(row["spread"]),
        "",
        "Интерпретация:",
        row["interpretation"] or "(нет)",
    ]

    if row["note"]:
        parts += ["", "Заметка:", row["note"]]

    if row["resonance"]:
        label = "отозвалось" if row["resonance"] == "yes" else "мимо"
        parts += ["", f"Отметка: {label}"]

    return "\n".join(parts)


def build_export_text(readings: list) -> str:
    header = [
        "Мой таро-дневник",
        f"Выгрузка от {date.today().isoformat()}",
        f"Всего раскладов: {len(readings)}",
        "=" * 64,
        "",
    ]

    body = ("\n\n" + "-" * 64 + "\n\n").join(
        format_reading(reading) for reading in readings
    )

    return "\n".join(header) + body + "\n"


def month_bounds(year: int, month: int) -> tuple[str, str]:
    """Начало месяца и начало следующего — для сравнения с created_at."""

    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def previous_month(today: date) -> tuple[int, int]:
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def month_stats(readings: list) -> dict:
    cards = Counter()
    spreads = Counter()
    resonance = Counter()
    notes = 0

    for row in readings:
        spreads[row["spread_name"]] += 1

        for item in parse_spread(row["spread"]):
            name = item.get("card", {}).get("name")
            if name:
                cards[name] += 1

        if row["resonance"] in ("yes", "no"):
            resonance[row["resonance"]] += 1

        if row["note"]:
            notes += 1

    return {
        "total": len(readings),
        "spreads": spreads.most_common(),
        "top_cards": [(name, count) for name, count in cards.most_common(5) if count > 1],
        "yes": resonance["yes"],
        "no": resonance["no"],
        "notes": notes,
        "questions": [row["question"] for row in readings],
    }


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def format_month_summary(
    stats: dict,
    year: int,
    month: int,
    themes: str | None = None,
    so_far: bool = False
) -> str:
    """HTML для Telegram. Тексты вопросов здесь не выводим — только счётчики."""

    title = f"{MONTHS[month - 1].capitalize()} {year}"
    lines = [
        f"📅 <b>{'Месяц на сегодня' if so_far else 'Итоги месяца'}: {title}</b>",
        "",
    ]

    total = stats["total"]

    if not total:
        lines.append("В этом месяце раскладов не было.")
        return "\n".join(lines)

    lines.append(
        f"Раскладов: <b>{total}</b>"
    )

    spreads = ", ".join(
        f"{html.escape(spread_title(name))} — {count}"
        for name, count in stats["spreads"]
    )
    lines.append(f"Форматы: {spreads}")

    if stats["top_cards"]:
        cards = ", ".join(
            f"{html.escape(name)} ×{count}"
            for name, count in stats["top_cards"]
        )
        lines += ["", f"🃏 Чаще всего выпадали: {cards}"]

    marked = stats["yes"] + stats["no"]

    if marked:
        lines += [
            "",
            f"✅ Отозвалось: {stats['yes']} · ❌ Мимо: {stats['no']} "
            f"(из {marked} {_plural(marked, 'отмеченного', 'отмеченных', 'отмеченных')})",
        ]

    if stats["notes"]:
        lines.append(
            f"📝 Заметок: {stats['notes']}"
        )

    if themes:
        lines += ["", "<b>О чём были вопросы</b>", html.escape(themes)]

    return "\n".join(lines)
