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
                    text="📊 Дневник",
                    callback_data="stats"
                ),
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

def history_keyboard(
    readings,
    page: int = 0,
    pages: int = 1,
    start_index: int = 1
) -> InlineKeyboardMarkup:
    buttons = []

    for offset, reading in enumerate(readings):
        number = start_index + offset

        question = reading["question"]

        if len(question) > 32:
            question = question[:32] + "..."

        # Значок показывает, что у расклада есть заметка или отметка.
        mark = ""

        if reading["resonance"] == "yes":
            mark = "✅ "
        elif reading["resonance"] == "no":
            mark = "❌ "
        elif reading["note"]:
            mark = "📝 "

        buttons.append([
            InlineKeyboardButton(
                text=f"{mark}#{number} {question}",
                callback_data=f"history_reading:{reading['id']}"
            )
        ])

    # Листалка: страница 0 — самые свежие расклады.
    if pages > 1:
        navigation = []

        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="← Новее",
                    callback_data=f"history_page:{page - 1}"
                )
            )

        if page < pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="Раньше →",
                    callback_data=f"history_page:{page + 1}"
                )
            )

        if navigation:
            buttons.append(navigation)

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


def reading_keyboard(
    reading_id: int | None = None,
    note: str | None = None,
    resonance: str | None = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура под раскладом из истории. Если передан reading_id,
    добавляются кнопки дневника: заметка и отметка «отозвалось».
    """

    buttons = []

    if reading_id is not None:
        buttons.append([
            InlineKeyboardButton(
                text=(
                    "📝 Изменить заметку"
                    if note
                    else "📝 Добавить заметку"
                ),
                callback_data=f"note:{reading_id}"
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                text=(
                    "✅ Отозвалось"
                    if resonance == "yes"
                    else "☑️ Отозвалось"
                ),
                callback_data=(
                    f"resonance:{reading_id}:clear"
                    if resonance == "yes"
                    else f"resonance:{reading_id}:yes"
                )
            ),
            InlineKeyboardButton(
                text=(
                    "❌ Мимо"
                    if resonance == "no"
                    else "⬜️ Мимо"
                ),
                callback_data=(
                    f"resonance:{reading_id}:clear"
                    if resonance == "no"
                    else f"resonance:{reading_id}:no"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="← История",
            callback_data="history"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🔮 Новый расклад",
            callback_data="new_reading"
        ),
        InlineKeyboardButton(
            text="⌂ Меню",
            callback_data="main_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def note_cancel_keyboard(reading_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить заметку",
                    callback_data=f"note_delete:{reading_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Отмена",
                    callback_data=f"history_reading:{reading_id}"
                )
            ]
        ]
    )


def card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔮 Сделать расклад",
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