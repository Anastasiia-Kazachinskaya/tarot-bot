from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def spread_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🃏 1 карта",
                    callback_data="spread:one_card"
                ),
                InlineKeyboardButton(
                    text="🃏 3 карты",
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
                    text="🔮 7 карт",
                    callback_data="spread:seven_cards"
                ),
                InlineKeyboardButton(
                    text="☦️ Кельтский крест",
                    callback_data="spread:celtic_cross"
                ),
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
                    text="➡️ Нет",
                    callback_data="reversed:no"
                ),
            ]
        ]
    )

def after_reading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
                )
            ]
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

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

def reading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Назад к истории",
                    callback_data="history"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔮 Новый расклад",
                    callback_data="new_reading"
                )
            ]
        ]
    )