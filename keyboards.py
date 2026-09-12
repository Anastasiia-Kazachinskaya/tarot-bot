from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🃏 Новый расклад",
                    callback_data="new_reading"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📖 История",
                    callback_data="history"
                ),
                InlineKeyboardButton(
                    text="📚 Как это работает",
                    callback_data="about"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="settings"
                )
            ],
        ]
    )


def spread_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🃏 Одна карта",
                    callback_data="spread:one_card"
                ),
                InlineKeyboardButton(
                    text="🃏 Три карты",
                    callback_data="spread:three_cards"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❤️ Отношения",
                    callback_data="spread:relationship"
                ),
                InlineKeyboardButton(
                    text="⚖️ Выбор",
                    callback_data="spread:choice"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔮 Семь карт",
                    callback_data="spread:seven_cards"
                ),
                InlineKeyboardButton(
                    text="✥ Кельтский крест",
                    callback_data="spread:celtic_cross"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Главное меню",
                    callback_data="main_menu"
                )
            ],
        ]
    )


def reversed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Да",
                    callback_data="reversed:yes"
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data="reversed:no"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data="new_reading"
                )
            ]
        ]
    )


def after_reading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Уточняющая карта",
                    callback_data="draw_clarifying_card"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔮 Новый расклад",
                    callback_data="new_reading"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📖 История",
                    callback_data="history"
                ),
                InlineKeyboardButton(
                    text="⌂ Главное меню",
                    callback_data="main_menu"
                )
            ],
        ]
    )

def history_keyboard(readings) -> InlineKeyboardMarkup:
    buttons = []

    for index, reading in enumerate(readings, start=1):
        question = reading["question"]

        if len(question) > 35:
            question = question[:35] + "..."

        buttons.append([
            InlineKeyboardButton(
                text=f"🔮 #{index} {question}",
                callback_data=f"history_reading:{reading['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🃏 Новый расклад",
            callback_data="new_reading"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="⌂ Главное меню",
            callback_data="main_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def reading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← История",
                    callback_data="history"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔮 Новый расклад",
                    callback_data="new_reading"
                ),
                InlineKeyboardButton(
                    text="⌂ Меню",
                    callback_data="main_menu"
                )
            ]
        ]
    )

def settings_keyboard(current: bool | None) -> InlineKeyboardMarkup:
    def mark(value):
        return " ✅" if current == value else ""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔄 Всегда да{mark(True)}",
                    callback_data="settings:reversed:yes"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"➡️ Всегда нет{mark(False)}",
                    callback_data="settings:reversed:no"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"❓ Спрашивать каждый раз{mark(None)}",
                    callback_data="settings:reversed:ask"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⌂ Главное меню",
                    callback_data="main_menu"
                )
            ]
        ]
    )

def llm_error_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Повторить",
                    callback_data="retry_interpretation"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔮 Новый расклад",
                    callback_data="new_reading"
                ),
                InlineKeyboardButton(
                    text="⌂ Меню",
                    callback_data="main_menu"
                )
            ],
        ]
    )