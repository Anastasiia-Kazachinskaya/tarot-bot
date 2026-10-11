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
                    text="🌅 Карта дня",
                    callback_data="daily_now"
                ),
                InlineKeyboardButton(
                    text="📖 История",
                    callback_data="history"
                ),
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
            [
                InlineKeyboardButton(
                    text="📚 Как это работает",
                    callback_data="about"
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

def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def describe_daily_hour(hour: int | None) -> str:
    return "выключена" if hour is None else f"{hour:02d}:00"


def settings_keyboard(
    current: bool | None,
    gender: str | None = None,
    daily_hour: int | None = None,
    checkins_enabled: bool = True
) -> InlineKeyboardMarkup:
    def mark(value):
        return " ✅" if current == value else ""

    def mark_gender(value):
        return " ✅" if gender == value else ""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"🔄 Всегда да{mark(True)}", "settings:reversed:yes")],
            [_btn(f"➡️ Всегда нет{mark(False)}", "settings:reversed:no")],
            [_btn(f"❓ Спрашивать каждый раз{mark(None)}", "settings:reversed:ask")],
            [
                _btn(f"Ж{mark_gender('f')}", "settings:gender:f"),
                _btn(f"М{mark_gender('m')}", "settings:gender:m"),
                _btn(f"Без рода{mark_gender(None)}", "settings:gender:none"),
            ],
            [_btn(f"🌅 Карта дня: {describe_daily_hour(daily_hour)}", "settings:daily")],
            [
                _btn(
                    "📖 «Как сложилось?»: "
                    + ("вкл" if checkins_enabled else "выкл"),
                    "settings:checkins:toggle"
                )
            ],
            [_btn("⌂ Главное меню", "main_menu")],
        ]
    )


DAILY_HOURS = (7, 8, 9, 10, 11, 12, 18, 21)


def daily_hour_keyboard(
    current: int | None,
    prefix: str = "settings:daily_hour",
    back: str | None = "settings"
) -> InlineKeyboardMarkup:
    def mark(hour):
        return " ✅" if current == hour else ""

    hours = [
        _btn(f"{hour:02d}:00{mark(hour)}", f"{prefix}:{hour}")
        for hour in DAILY_HOURS
    ]

    rows = [
        hours[:4],
        hours[4:],
        [_btn(f"🚫 Не присылать{mark(None)}", f"{prefix}:off")],
    ]

    if back:
        rows.append([_btn("← Назад", back)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def onboarding_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("В женском", "onboard:gender:f"),
                _btn("В мужском", "onboard:gender:m"),
            ],
            [_btn("Без рода", "onboard:gender:none")],
        ]
    )


def onboarding_reversed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("🔄 Да, всегда", "onboard:reversed:yes"),
                _btn("➡️ Нет", "onboard:reversed:no"),
            ],
            [_btn("❓ Спрашивать каждый раз", "onboard:reversed:ask")],
        ]
    )


def onboarding_daily_keyboard() -> InlineKeyboardMarkup:
    return daily_hour_keyboard(
        None,
        prefix="onboard:daily",
        back=None
    )


def repeat_question_keyboard(reading_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📖 Открыть тот расклад", f"history_reading:{reading_id}")],
            [_btn("🔮 Всё равно сделать новый", "repeat:go")],
            [_btn("⌂ Главное меню", "main_menu")],
        ]
    )


def checkin_keyboard(reading_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Отозвалось", f"resonance:{reading_id}:yes"),
                _btn("❌ Мимо", f"resonance:{reading_id}:no"),
            ],
            [_btn("📖 Открыть расклад", f"history_reading:{reading_id}")],
            [_btn("🔕 Больше не спрашивать", "checkins:off")],
        ]
    )


def daily_card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔮 Сделать расклад", "new_reading")],
            [_btn("⚙️ Время карты дня", "settings:daily")],
        ]
    )


def delete_data_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🗑 Да, удалить всё", "delete_data:confirm")],
            [_btn("Отмена", "delete_data:cancel")],
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