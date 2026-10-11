import asyncio
import html
import json
import logging
import os
import re
import time
import traceback
from datetime import datetime
from functools import partial

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    ReactionTypeEmoji,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardMarkup,
    Message,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

from .keyboards import (
    main_menu_keyboard,
    card_keyboard,
    note_cancel_keyboard,
    spread_keyboard,
    reversed_keyboard,
    after_reading_keyboard,
    history_keyboard,
    reading_keyboard,
    llm_error_keyboard,
    settings_keyboard,
    daily_hour_keyboard,
    describe_daily_hour,
    onboarding_gender_keyboard,
    onboarding_reversed_keyboard,
    onboarding_daily_keyboard,
    repeat_question_keyboard,
    checkin_keyboard,
    daily_card_keyboard,
    delete_data_keyboard,
    suggest_keyboard
)

from .states import TarotStates
from .suggest import suggest_spread
from .storage import SQLiteStorage, delete_user_fsm
from .tarot.deck import make_spread, draw_single_card, find_cards
from .tarot.renderer import render_spread, render_single_card
from .llm.cloudru import (
    interpret_tarot,
    answer_followup,
    interpret_clarifying_card,
    daily_card_message,
    month_themes
)
from .tarot.spreads import SPREADS

from .database import (
    init_database,
    save_reading,
    get_user_readings,
    count_user_readings,
    get_reading,
    get_reversed_cards_preference,
    set_reversed_cards_preference,
    remember_reversed_cards_choice,
    get_user_gender,
    set_user_gender,
    set_reading_note,
    set_reading_resonance,
    get_diary_stats,
    get_usage,
    consume_quota,
    touch_user,
    get_bot_stats,
    get_profile,
    set_onboarded,
    set_daily_hour,
    set_checkins_enabled,
    get_daily_card_recipients,
    mark_daily_card_sent,
    get_due_checkins,
    mark_checkin_sent,
    find_recent_same_question,
    delete_user_data,
    get_today_daily_card,
    save_today_daily_card,
    set_monthly_enabled,
    mark_monthly_sent,
    get_all_readings,
    get_readings_between,
    get_monthly_recipients
)
from .journal import (
    build_export_text,
    month_bounds,
    month_key,
    month_stats,
    previous_month,
    format_month_summary
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger("tarot")

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")


def int_env(name: str, default: int) -> int:
    value = os.getenv(name)

    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"{name} должен быть целым числом, а не {value!r}"
        ) from None


# Сколько раскладов и обращений к модели можно за сутки.
# 0 отключает соответствующий лимит.
DAILY_READINGS_LIMIT = int_env("DAILY_READINGS_LIMIT", 10)
DAILY_LLM_LIMIT = int_env("DAILY_LLM_LIMIT", 30)

def id_set_env(name: str) -> set[int]:
    raw = os.getenv(name, "").replace(" ", "")

    try:
        return {
            int(user_id)
            for user_id in raw.split(",")
            if user_id
        }
    except ValueError:
        raise ValueError(
            f"{name} должен быть списком числовых Telegram ID "
            "через запятую"
        ) from None


# Кому доступна команда /admin со сводкой по пользователям.
ADMIN_USER_IDS = id_set_env("ADMIN_USER_IDS")

# Telegram ID, на которых лимит не распространяется (например, твой).
UNLIMITED_USER_IDS = id_set_env("UNLIMITED_USER_IDS")


bot = Bot(token=BOT_TOKEN)

storage = SQLiteStorage()
dp = Dispatcher(storage=storage)


class TrackUsersMiddleware(BaseMiddleware):
    """
    Запоминает, кто и когда пользовался ботом: нужно, чтобы видеть
    сводку по друзьям в /admin. Сохраняются только Telegram ID,
    имя и username — ни вопросов, ни интерпретаций.
    """

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)

        if user is not None:
            try:
                await asyncio.to_thread(
                    touch_user,
                    user.id,
                    user.username,
                    user.first_name
                )
            except Exception:
                # Статистика не должна мешать работе бота.
                logger.warning(
                    "Не удалось отметить пользователя %s",
                    user.id,
                    exc_info=True
                )

        return await handler(event, data)


dp.message.outer_middleware(TrackUsersMiddleware())
dp.callback_query.outer_middleware(TrackUsersMiddleware())


def log_metrics(status: str, error: bool = False, **fields) -> None:
    """
    Одна строка метрик на запрос. При error=True вызывается внутри
    except и добавляет в лог traceback.
    """

    details = " | ".join(
        f"{key}={value}"
        for key, value in fields.items()
    )

    logger.log(
        logging.ERROR if error else logging.INFO,
        "TAROT %s | %s",
        status,
        details,
        exc_info=error
    )


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 расклад», «2 расклада», «5 раскладов»."""

    if count % 10 == 1 and count % 100 != 11:
        return one

    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few

    return many


async def take_quota(user_id: int, is_reading: bool) -> str | None:
    """
    Засчитывает обращение к модели и проверяет дневной лимит.
    Возвращает None, если можно продолжать, иначе текст отказа.

    Считаем до запроса: платим за попытку, а не за удачный ответ.
    """

    if user_id in UNLIMITED_USER_IDS:
        return None

    allowed, reason, used, limit = await asyncio.to_thread(
        consume_quota,
        user_id,
        DAILY_READINGS_LIMIT,
        DAILY_LLM_LIMIT,
        is_reading
    )

    if allowed:
        return None

    logger.info(
        "Лимит исчерпан: user_id=%s | %s=%s/%s",
        user_id,
        reason,
        used,
        limit
    )

    if reason == "readings":
        return (
            "🔮 <b>На сегодня хватит.</b>\n\n"
            f"Лимит — {limit} "
            f"{plural(limit, 'расклад', 'расклада', 'раскладов')} "
            "в сутки, он уже выбран.\n\n"
            "История и заметки никуда не делись: "
            "их можно открыть через /history."
        )

    return (
        "🔮 <b>На сегодня хватит.</b>\n\n"
        f"Лимит обращений к AI — {limit} в сутки, он уже выбран "
        "(сюда идут и расклады, и уточняющие вопросы).\n\n"
        "История и заметки доступны через /history."
    )


def typing(chat_id: int):
    """Показывает «печатает…», пока бот ждёт ответ модели."""
    return ChatActionSender.typing(bot=bot, chat_id=chat_id)


def seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}s"


def chat_id_of(callback: CallbackQuery) -> int:
    if callback.message is not None:
        return callback.message.chat.id

    return callback.from_user.id


async def safe_edit(
    callback: CallbackQuery,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """
    Редактирует сообщение, на котором нажали кнопку.

    Если отредактировать нельзя (сообщение удалено, слишком старое),
    отправляет новое. Повторное нажатие на ту же кнопку даёт
    «message is not modified» — это не ошибка, просто ничего не делаем.
    """

    if callback.message is not None:
        try:
            await callback.message.edit_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            return

        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return

            logger.warning(
                "Не удалось отредактировать сообщение: %s",
                error
            )

    await bot.send_message(
        chat_id_of(callback),
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup
    )


SPREAD_DESCRIPTIONS = {
    "one_card": (
        "Один символический взгляд на ситуацию.\n\n"
        "Подходит, когда тебе нужен короткий фокус: "
        "что сейчас особенно важно увидеть или осознать."
    ),

    "three_cards": (
        "Короткий разбор динамики ситуации.\n\n"
        "Первая карта показывает, что происходит сейчас, "
        "вторая — скрытый фактор, третья — направление развития."
    ),

    "relationship": (
        "Расклад о динамике между тобой и другим человеком.\n\n"
        "Он помогает посмотреть на твою позицию, "
        "позицию другого человека, то, что происходит между вами, "
        "скрытые факторы и возможное направление отношений."
    ),

    "choice": (
        "Расклад для ситуации, в которой перед тобой два варианта.\n\n"
        "Он сравнивает потенциал и возможные риски каждого пути, "
        "чтобы тебе было проще увидеть разницу между ними."
    ),

    "seven_cards": (
        "Подробный разбор ситуации.\n\n"
        "Карты рассматривают её с разных сторон: "
        "прошлое влияние, настоящее, ближайшее развитие, "
        "скрытые факторы, совет и возможный итог."
    ),

    "celtic_cross": (
        "Глубокий расклад для сложных ситуаций.\n\n"
        "Десять карт помогают рассмотреть ситуацию целиком: "
        "внутренние и внешние факторы, прошлое, настоящее, "
        "возможное развитие, надежды, страхи и итоговую тенденцию."
    ),
}

# Примеры вопросов под каждый расклад: у раскладов разные задачи,
# и общий пример про работу сбивает с толку там, где нужны два
# варианта или второй человек.
SPREAD_EXAMPLES = {
    "one_card": (
        "На что мне сейчас важнее всего обратить внимание?",
        "Что я упускаю в истории с переездом?",
    ),

    "three_cards": (
        "Что происходит с моим проектом и куда он движется?",
        "Почему разговор с руководителем зашёл в тупик "
        "и чем это закончится?",
    ),

    "relationship": (
        "Что сейчас происходит между мной и Настей?",
        "Почему мы с подругой отдалились?",
    ),

    "choice": (
        "Мне предложили работу в другой компании, не знаю, уходить ли.",
        "Думаю, где жить дальше: снимать или брать ипотеку.",
    ),

    "seven_cards": (
        "Что влияет на мою усталость от работы и что с ней делать?",
        "Как мне подойти к запуску своего дела этой осенью?",
    ),

    "celtic_cross": (
        "Что на самом деле происходит с моими отношениями "
        "с мамой и куда они идут?",
        "Почему я который год не могу решиться сменить профессию?",
    ),
}

# Что стоит обязательно упомянуть в вопросе для этого расклада.
SPREAD_QUESTION_HINTS = {
    "relationship": (
        "Обязательно скажи, о ком речь и что вас связывает — "
        "расклад читает динамику между двумя людьми."
    ),

    "choice": (
        "Опиши ситуацию, в которой выбираешь. Варианты А и Б "
        "я спрошу следующими сообщениями — по одному."
    ),

    "one_card": (
        "Для одной карты лучше один короткий фокус, "
        "а не несколько вопросов сразу."
    ),
}


def get_spread_setup_text(spread_name: str) -> str:
    spread_title = html.escape(
        SPREADS[spread_name]["name"].upper()
    )

    description = html.escape(
        SPREAD_DESCRIPTIONS[spread_name]
    )

    return (
        f"🔮 <b>{spread_title}</b>\n\n"
        f"{description}\n\n"
        "──────────────\n\n"
        "<b>Использовать перевёрнутые карты?</b>"
    )


def describe_reversed_preference(value: bool | None) -> str:
    if value is None:
        return "спрашивать каждый раз"

    return "да" if value else "нет"


def describe_gender(gender: str | None) -> str:
    return {
        "f": "в женском роде",
        "m": "в мужском роде",
    }.get(gender, "без рода")


def get_settings_text(
    current: bool | None,
    gender: str | None = None,
    daily_hour: int | None = None,
    checkins_enabled: bool = True,
    monthly_enabled: bool = True
) -> str:
    return (
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        "<b>Перевёрнутые карты</b> в новых раскладах: "
        f"{describe_reversed_preference(current)}.\n\n"
        "<b>Обращение</b> в интерпретациях: "
        f"{describe_gender(gender)}.\n\n"
        "<b>Карта дня</b> (по московскому времени): "
        f"{describe_daily_hour(daily_hour)}.\n\n"
        "<b>«Как сложилось?»</b> — через неделю после расклада "
        "спрошу, отозвался ли он: "
        f"{'вкл' if checkins_enabled else 'выкл'}.\n\n"
        "<b>Итоги месяца</b> — 1-го числа пришлю сводку "
        "за прошлый месяц: "
        f"{'вкл' if monthly_enabled else 'выкл'}."
    )


async def settings_view(user_id: int):
    """Текст и клавиатура экрана настроек с актуальными значениями."""

    reversed_pref = await asyncio.to_thread(
        get_reversed_cards_preference,
        user_id
    )
    profile = await asyncio.to_thread(get_profile, user_id)

    text = get_settings_text(
        reversed_pref,
        profile["gender"],
        profile["daily_hour"],
        profile["checkins_enabled"],
        profile["monthly_enabled"]
    )

    keyboard = settings_keyboard(
        reversed_pref,
        profile["gender"],
        profile["daily_hour"],
        profile["checkins_enabled"],
        profile["monthly_enabled"]
    )

    return text, keyboard

# Признаки острого кризиса. Это не диагностика, а страховка:
# на такие сообщения бот не трактует карты, а даёт контакты помощи.
CRISIS_RE = re.compile(
    r"(покончить с собой|суицид|не хочу жить|жить не хочется|"
    r"не хочется жить|хочу умереть|убить себя|убью себя|"
    r"свести счеты с жизнью|свести счёты с жизнью|"
    r"самоповрежд|порезать себя|причинить себе вред)",
    re.IGNORECASE
)

CRISIS_TEXT = (
    "🤍 <b>На это я не хочу отвечать картами.</b>\n\n"
    "Похоже, сейчас очень тяжело. Пожалуйста, не оставайся "
    "с этим наедине — напиши или позвони тому, кому доверяешь, "
    "или специалистам, которые помогают именно в таких "
    "ситуациях. Это бесплатно и анонимно.\n\n"
    "• <b>112</b> — если есть угроза жизни прямо сейчас\n"
    "• <b>8 (495) 989-50-50</b> — телефон доверия экстренной "
    "психологической помощи МЧС, круглосуточно\n"
    "• <b>051</b> (с мобильного <b>8 (495) 051</b>) — "
    "психологическая помощь в Москве, круглосуточно\n"
    "• <b>8-800-2000-122</b> — телефон доверия для подростков "
    "и молодёжи\n\n"
    "Если ты не в России — позвони в местную экстренную службу.\n\n"
    "Я рядом, если захочется вернуться к раскладам позже."
)


async def answer_if_crisis(message: Message) -> bool:
    """Если в сообщении признаки острого кризиса — отвечаем контактами помощи."""

    if not CRISIS_RE.search(message.text or ""):
        return False

    logger.warning(
        "Сообщение с признаками кризиса: user_id=%s",
        message.from_user.id
    )

    await message.answer(CRISIS_TEXT, parse_mode="HTML")
    return True


MAIN_MENU_TEXT = (
    "🔮 <b>MY PERSONAL TAROT</b>\n\n"
    "Не предсказываю будущее.\n"
    "Помогаю посмотреть на ситуацию с другой стороны.\n\n"
    "Выбери, что хочешь сделать:"
)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    profile = await asyncio.to_thread(get_profile, message.from_user.id)

    if not profile["onboarded"]:
        await message.answer(
            "🔮 <b>Привет! Я — личный таро-бот.</b>\n\n"
            "Не предсказываю будущее, а помогаю посмотреть "
            "на ситуацию с другой стороны. Все расклады сохраняются "
            "в дневник — к ним можно вернуться.\n\n"
            "Пара быстрых настроек, и начнём (всё меняется "
            "потом в /settings).\n\n"
            "<b>1/3. Как к тебе обращаться в интерпретациях?</b>",
            parse_mode="HTML",
            reply_markup=onboarding_gender_keyboard()
        )
        return

    await message.answer(
        MAIN_MENU_TEXT,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data.startswith("onboard:gender:"))
async def onboard_gender(callback: CallbackQuery):
    choice = callback.data.split(":")[2]

    if choice not in ("f", "m", "none"):
        await callback.answer("Неизвестный вариант.")
        return

    await asyncio.to_thread(
        set_user_gender,
        callback.from_user.id,
        None if choice == "none" else choice
    )

    await safe_edit(
        callback,
        "🔮 <b>2/3. Использовать перевёрнутые карты?</b>\n\n"
        "Перевёрнутая карта добавляет оттенки: блок, задержку, "
        "обратную сторону темы. Без них расклады проще.",
        reply_markup=onboarding_reversed_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("onboard:reversed:"))
async def onboard_reversed(callback: CallbackQuery):
    choice = callback.data.split(":")[2]

    if choice not in ("yes", "no", "ask"):
        await callback.answer("Неизвестный вариант.")
        return

    await asyncio.to_thread(
        set_reversed_cards_preference,
        callback.from_user.id,
        {"yes": True, "no": False, "ask": None}[choice]
    )

    await safe_edit(
        callback,
        "🔮 <b>3/3. Присылать карту дня?</b>\n\n"
        "Каждый день в выбранный час — одна карта и короткая мысль "
        "на день. Время московское.",
        reply_markup=onboarding_daily_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("onboard:daily:"))
async def onboard_daily(callback: CallbackQuery):
    ok, hour = parse_hour_choice(callback.data.split(":")[2])

    if not ok:
        await callback.answer("Неизвестное время.")
        return

    await asyncio.to_thread(set_daily_hour, callback.from_user.id, hour)
    await asyncio.to_thread(set_onboarded, callback.from_user.id)

    daily_note = (
        f"Карта дня будет приходить в {hour:02d}:00.\n\n"
        if hour is not None
        else ""
    )

    await safe_edit(
        callback,
        "✅ <b>Готово!</b>\n\n"
        f"{daily_note}"
        "Через неделю после расклада я спрошу, как всё сложилось, — "
        "так в дневнике будет видно, что отзывается. "
        "Выключить можно в /settings.\n\n"
        "Выбери, что хочешь сделать:",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await safe_edit(callback,
        "🔮 <b>MY PERSONAL TAROT</b>\n\n"
        "Не предсказываю будущее.\n"
        "Помогаю посмотреть на ситуацию\n"
        "с другой стороны.\n\n"
        "Выбери, что хочешь сделать:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

    await callback.answer()


def get_question_prompt_text(
    spread_name: str,
    note: str = ""
) -> str:
    """
    Экран «задай свой вопрос». Примеры и подсказка берутся под
    конкретный расклад — задачи у них разные.
    """

    spread_title = html.escape(
        SPREADS[spread_name]["name"].upper()
    )

    examples = "\n".join(
        f"«{html.escape(example)}»"
        for example in SPREAD_EXAMPLES[spread_name]
    )

    hint = SPREAD_QUESTION_HINTS.get(spread_name)

    hint_block = (
        f"{html.escape(hint)}\n\n"
        if hint
        else ""
    )

    return (
        f"🔮 <b>{spread_title}</b>\n\n"
        f"{note}"
        "Теперь задай свой вопрос.\n\n"
        f"{hint_block}"
        "Чем конкретнее ситуация, тем полезнее "
        "получится интерпретация.\n\n"
        "<b>Например:</b>\n"
        f"{examples}"
    )


def describe_saved_preference_note(saved_preference: bool | None) -> str:
    if saved_preference is None:
        return ""

    return (
        "Использую сохранённую настройку: перевёрнутые карты — "
        f"{describe_reversed_preference(saved_preference)}. "
        "Изменить можно в ⚙️ Настройках.\n\n"
    )


@dp.callback_query(F.data.startswith("spread:"))
async def choose_spread(callback: CallbackQuery, state: FSMContext):
    spread_name = callback.data.split(":")[1]

    if spread_name not in SPREADS:
        await callback.answer("Неизвестный расклад.")
        return

    # Флаги «своя колода» и «вопрос уже задан» переживают выбор формата.
    previous = await state.get_data()
    kept = {
        key: previous[key]
        for key in ("live", "prefilled_question", "prefilled_message_id")
        if key in previous
    }

    await state.clear()
    await state.update_data(spread_name=spread_name, **kept)

    if kept.get("live"):
        # Карты тянутся вручную, ориентацию человек назовёт сам.
        await state.update_data(reversed_cards=True)
        await callback.answer()
        await continue_to_question(callback, state, spread_name, LIVE_NOTE)
        return

    saved_preference = await asyncio.to_thread(
        get_reversed_cards_preference,
        callback.from_user.id
    )

    if saved_preference is None:
        await state.set_state(TarotStates.choosing_reversed)

        await safe_edit(callback,
            get_spread_setup_text(spread_name),
            parse_mode="HTML",
            reply_markup=reversed_keyboard()
        )
    else:
        await state.update_data(reversed_cards=saved_preference)
        await callback.answer()
        await continue_to_question(
            callback,
            state,
            spread_name,
            describe_saved_preference_note(saved_preference)
        )
        return

    await callback.answer()


LIVE_NOTE = (
    "✋ <b>Своя колода.</b> Перемешай колоду и задай вопрос — "
    "потом я попрошу назвать карты по позициям.\n\n"
)


async def continue_to_question(
    callback: CallbackQuery,
    state: FSMContext,
    spread_name: str,
    note: str = ""
) -> None:
    """
    После выбора формата: либо просим вопрос, либо — если вопрос уже
    задан при подборе расклада — сразу идём дальше с ним.
    """

    await state.set_state(TarotStates.waiting_for_question)

    data = await state.get_data()
    prefilled = data.get("prefilled_question")

    if not prefilled:
        await safe_edit(
            callback,
            get_question_prompt_text(spread_name, note)
        )
        return

    await state.update_data(prefilled_question=None)

    await safe_edit(
        callback,
        f"🔮 <b>{html.escape(SPREADS[spread_name]['name'])}</b>\n\n"
        f"{note}"
        f"Вопрос: <i>{html.escape(prefilled)}</i>"
    )

    await process_question(
        state,
        prefilled,
        callback.from_user.id,
        chat_id_of(callback),
        data.get("prefilled_message_id")
    )

@dp.callback_query(
    TarotStates.choosing_reversed,
    F.data.startswith("reversed:")
)
async def choose_reversed(
    callback: CallbackQuery,
    state: FSMContext
):
    use_reversed = callback.data == "reversed:yes"

    data = await state.get_data()
    spread_name = data.get("spread_name")

    if spread_name not in SPREADS:
        await callback.answer(
            "Расклад не выбран. Начни заново: /start",
            show_alert=True
        )
        return

    await state.update_data(
        reversed_cards=use_reversed
    )

    await state.set_state(
        TarotStates.waiting_for_question
    )

    # Запоминаем выбор только если пользователь ещё ничего не настраивал.
    # Режим «спрашивать каждый раз» тоже хранится в базе, и перезаписывать
    # его ответом на этот вопрос нельзя — иначе он живёт один расклад.
    remembered = await asyncio.to_thread(
        remember_reversed_cards_choice,
        callback.from_user.id,
        use_reversed
    )

    note = (
        "Запомнил этот выбор — в следующий раз спрашивать "
        "не буду (поменять можно в ⚙️ Настройках).\n\n"
        if remembered
        else ""
    )

    await callback.answer()
    await continue_to_question(callback, state, spread_name, note)

@dp.callback_query(F.data == "settings")
async def settings_menu(callback: CallbackQuery):
    text, keyboard = await settings_view(callback.from_user.id)

    await safe_edit(callback, text, reply_markup=keyboard)
    await callback.answer()


@dp.message(Command("settings"))
async def settings_command(message: Message, state: FSMContext):
    await state.clear()

    text, keyboard = await settings_view(message.from_user.id)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("settings:reversed:"))
async def update_reversed_setting(callback: CallbackQuery):
    choice = callback.data.split(":")[2]

    if choice not in ("yes", "no", "ask"):
        await callback.answer("Неизвестная настройка.")
        return

    value = {
        "yes": True,
        "no": False,
        "ask": None
    }[choice]

    current = await asyncio.to_thread(
        get_reversed_cards_preference,
        callback.from_user.id
    )

    if current == value:
        await callback.answer("Уже выбрано")
        return

    await asyncio.to_thread(
        set_reversed_cards_preference,
        callback.from_user.id,
        value
    )

    text, keyboard = await settings_view(callback.from_user.id)
    await safe_edit(callback, text, reply_markup=keyboard)
    await callback.answer("Сохранено")


@dp.callback_query(F.data.startswith("settings:gender:"))
async def update_gender_setting(callback: CallbackQuery):
    choice = callback.data.split(":")[2]

    if choice not in ("f", "m", "none"):
        await callback.answer("Неизвестная настройка.")
        return

    value = None if choice == "none" else choice

    current_gender = await asyncio.to_thread(
        get_user_gender,
        callback.from_user.id
    )

    if current_gender == value:
        await callback.answer("Уже выбрано")
        return

    await asyncio.to_thread(
        set_user_gender,
        callback.from_user.id,
        value
    )

    text, keyboard = await settings_view(callback.from_user.id)
    await safe_edit(callback, text, reply_markup=keyboard)
    await callback.answer("Сохранено")


@dp.callback_query(F.data == "settings:daily")
async def daily_settings(callback: CallbackQuery):
    profile = await asyncio.to_thread(get_profile, callback.from_user.id)

    await safe_edit(
        callback,
        "🌅 <b>КАРТА ДНЯ</b>\n\n"
        "Каждый день в выбранный час пришлю одну карту "
        "и короткую мысль на день.\n\n"
        "Время московское.\n\n"
        f"Сейчас: <b>{describe_daily_hour(profile['daily_hour'])}</b>.",
        reply_markup=daily_hour_keyboard(profile["daily_hour"])
    )
    await callback.answer()


def parse_hour_choice(value: str) -> tuple[bool, int | None]:
    """«9» → (True, 9), «off» → (True, None), мусор → (False, None)."""

    if value == "off":
        return True, None

    try:
        hour = int(value)
    except ValueError:
        return False, None

    return (0 <= hour <= 23), hour


@dp.callback_query(F.data.startswith("settings:daily_hour:"))
async def update_daily_hour(callback: CallbackQuery):
    ok, hour = parse_hour_choice(callback.data.split(":")[2])

    if not ok:
        await callback.answer("Неизвестное время.")
        return

    await asyncio.to_thread(set_daily_hour, callback.from_user.id, hour)

    text, keyboard = await settings_view(callback.from_user.id)
    await safe_edit(callback, text, reply_markup=keyboard)

    await callback.answer(
        "Карта дня выключена"
        if hour is None
        else f"Пришлю карту дня в {hour:02d}:00"
    )


@dp.callback_query(F.data == "settings:checkins:toggle")
async def toggle_checkins(callback: CallbackQuery):
    profile = await asyncio.to_thread(get_profile, callback.from_user.id)
    enabled = not profile["checkins_enabled"]

    await asyncio.to_thread(
        set_checkins_enabled,
        callback.from_user.id,
        enabled
    )

    text, keyboard = await settings_view(callback.from_user.id)
    await safe_edit(callback, text, reply_markup=keyboard)
    await callback.answer("Включено" if enabled else "Выключено")


@dp.callback_query(F.data == "settings:monthly:toggle")
async def toggle_monthly(callback: CallbackQuery):
    profile = await asyncio.to_thread(get_profile, callback.from_user.id)
    enabled = not profile["monthly_enabled"]

    await asyncio.to_thread(
        set_monthly_enabled,
        callback.from_user.id,
        enabled
    )

    text, keyboard = await settings_view(callback.from_user.id)
    await safe_edit(callback, text, reply_markup=keyboard)
    await callback.answer("Включено" if enabled else "Выключено")


@dp.callback_query(F.data == "checkins:off")
async def checkins_off(callback: CallbackQuery):
    await asyncio.to_thread(
        set_checkins_enabled,
        callback.from_user.id,
        False
    )

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    await callback.answer(
        "Больше не спрошу. Включить снова можно в /settings",
        show_alert=True
    )


@dp.callback_query(F.data == "daily_now")
async def daily_now(callback: CallbackQuery):
    user_id = callback.from_user.id
    today = datetime.now().date().isoformat()

    already = await asyncio.to_thread(get_today_daily_card, user_id, today)

    if already is None:
        # Новая карта — это запрос к модели, считаем его в лимит.
        denied = await take_quota(user_id, is_reading=False)

        if denied:
            await callback.answer()
            await bot.send_message(
                user_id,
                denied,
                parse_mode="HTML",
                reply_markup=main_menu_keyboard()
            )
            return

    await callback.answer(
        "Карта дня уже вытянута — вот она"
        if already is not None
        else "Тяну карту дня…"
    )

    async with typing(chat_id_of(callback)):
        await send_daily_card(user_id)

    # Если карту попросили сами, по расписанию сегодня вторую не шлём.
    await asyncio.to_thread(mark_daily_card_sent, user_id, today)


def format_spread(spread: list[dict]) -> str:
    """
    Формирует красивое отображение вытянутых карт
    для пользователя Telegram.
    """

    lines = [
        "🔮 <b>Твой расклад</b>",
        ""
    ]

    for index, item in enumerate(spread, start=1):
        position = html.escape(item["position"])
        card = item["card"]

        card_name = html.escape(card["name"])

        orientation = (
            "прямая"
            if not card["reversed"]
            else "перевёрнутая"
        )

        lines.append(
            f"<b>{index}. {position}</b>"
        )

        lines.append(
            f"🃏 {card_name} — {orientation}"
        )

        lines.append("")

    return "\n".join(lines)

# Лимит Telegram — 4096 символов. Режем с небольшим запасом:
# длина считается уже по готовому HTML, вместе с префиксом и подсказкой.
MESSAGE_LIMIT = 4000

TAG_RE = re.compile(r"</?(b|i)>")


def llm_to_html(text: str) -> str:
    """
    Преобразует ответ LLM в безопасный Telegram HTML.

    Экранируется всё, кроме кавычек: они не ломают разметку,
    а `&quot;` заметно удлиняет текст.
    """

    text = html.escape(text, quote=False)

    # **текст** -> <b>текст</b>
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<b>\1</b>",
        text
    )

    # ### Заголовок -> <b>Заголовок</b>
    text = re.sub(
        r"^#{1,6}\s*(.+)$",
        r"<b>\1</b>",
        text,
        flags=re.MULTILINE
    )

    return text


def _safe_cut(text: str, limit: int) -> int:
    """
    Ищет позицию для разреза, не попадающую внутрь HTML-тега,
    HTML-сущности или слова.
    """

    cut = limit

    last_open = text.rfind("<", 0, cut)
    last_close = text.rfind(">", 0, cut)

    if last_open > last_close:
        cut = last_open

    last_amp = text.rfind("&", 0, cut)
    last_semicolon = text.rfind(";", 0, cut)

    if last_amp > last_semicolon:
        cut = last_amp

    space = text.rfind(" ", 0, cut)

    if space > cut // 2:
        cut = space

    return max(cut, 1)


MIN_TAIL = 200


def _append(chunks: list[str], chunk: str) -> None:
    if chunk.strip():
        chunks.append(chunk)


def _balance_tags(chunk: str, carry: list[str]) -> tuple[str, list[str]]:
    """
    Если кусок обрывается внутри <b> или <i>, дописывает закрывающие
    теги в конце и открывает их заново в начале следующего куска.
    """

    chunk = "".join(f"<{tag}>" for tag in carry) + chunk

    stack: list[str] = []

    for match in TAG_RE.finditer(chunk):
        tag = match.group(1)

        if match.group(0).startswith("</"):
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)

    chunk += "".join(f"</{tag}>" for tag in reversed(stack))

    return chunk, stack


def split_html(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """
    Разбивает готовый HTML на сообщения, стараясь не разрывать абзацы
    и не оставлять незакрытые теги. Длина считается по итоговому
    тексту, поэтому заголовок и подсказка тоже учитываются.
    """

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        while paragraph:
            separator = "\n\n" if current else ""
            room = limit - len(current) - len(separator)

            if len(paragraph) <= room:
                current += separator + paragraph
                break

            if room < MIN_TAIL:
                # В текущем сообщении уже не осталось места
                _append(chunks, current)
                current = ""
                continue

            cut = _safe_cut(paragraph, room)

            current += separator + paragraph[:cut].rstrip()

            _append(chunks, current)
            current = ""

            paragraph = paragraph[cut:].lstrip()

    _append(chunks, current)

    balanced: list[str] = []
    carry: list[str] = []

    for chunk in chunks:
        fixed, carry = _balance_tags(chunk, carry)
        balanced.append(fixed)

    return balanced


FOLLOWUP_HINT = (
    "\n\n💬 Можешь задать уточняющий вопрос по раскладу "
    "— просто напиши его. Или вытяни ещё одну уточняющую "
    "карту кнопкой ниже."
)


async def send_html(
    answer,
    text: str,
    reply_markup=None
) -> list[str]:
    """
    Отправляет готовый HTML, разбивая его на несколько сообщений,
    если он не помещается в лимит. Клавиатура идёт с последним.
    """

    chunks = split_html(text)

    if not chunks:
        return []

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1

        await answer(
            chunk,
            parse_mode="HTML",
            reply_markup=(
                reply_markup
                if is_last
                else None
            )
        )

    return chunks


async def send_llm_response(
    answer,
    text: str,
    prefix: str = "",
    suffix: str = "",
    reply_markup=None
) -> list[str]:
    """
    Отправляет ответ LLM: сначала переводит его в HTML вместе
    с префиксом и подсказкой, и только потом режет на сообщения —
    иначе экранирование может вытолкнуть кусок за лимит Telegram.
    """

    body = prefix + llm_to_html(text) + suffix

    return await send_html(
        answer,
        body,
        reply_markup=(
            reply_markup
            if reply_markup is not None
            else after_reading_keyboard()
        )
    )


@dp.message(Command("about"))
async def about_command(message: Message):
    await message.answer(
        "🔮 <b>MY PERSONAL TAROT</b>\n\n"
        "AI-таролог на основе системы "
        "Райдера—Уэйта.\n\n"
        "<b>Внутри:</b>\n"
        "• 78 карт\n"
        "• прямые и перевёрнутые положения\n"
        "• 6 типов раскладов\n"
        "• персональная интерпретация вопроса\n"
        "• история раскладов\n"
        "• дневник: заметки и отметки «отозвалось / мимо»\n"
        "• карта дня по расписанию или по кнопке\n"
        "• «Как сложилось?» — напоминание через неделю после расклада\n"
        "• изображения карт\n\n"
        "Таро здесь используется как инструмент "
        "символического анализа и рефлексии.\n\n"
        "Расклады и заметки хранятся на сервере бота, чтобы их "
        "можно было открыть позже в истории. Удалить всё можно "
        "в любой момент командой /delete_my_data.\n\n"
        "Финальное решение всегда остаётся за тобой.",
        parse_mode="HTML"
    )

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "❓ <b>ПОМОЩЬ</b>\n\n"
        "<b>Как сделать расклад?</b>\n\n"
        "1. Выбери тип расклада.\n"
        "2. Реши, использовать ли перевёрнутые карты.\n"
        "3. Задай свой вопрос.\n"
        "4. Получи расклад и интерпретацию.\n\n"
        "<b>Как задавать вопросы?</b>\n\n"
        "Лучше всего работают конкретные вопросы, "
        "связанные с реальной ситуацией.\n\n"
        "❌ «Что меня ждёт?»\n"
        "✅ «Что влияет на мою усталость от работы "
        "и что с ней делать?»\n\n"
        "Вопрос стоит подбирать под расклад: "
        "для «Выбора» опиши ситуацию, а варианты А и Б бот спросит "
        "отдельно; для «Отношений» — о ком речь; для одной карты "
        "хватит короткого фокуса. "
        "Примеры бот показывает на экране вопроса.\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/new — новый расклад\n"
        "/card Башня — значение любой карты\n"
        "/history — история раскладов\n"
        "/stats — дневник и статистика\n"
        "/month — итоги текущего месяца\n"
        "/export — выгрузить весь дневник файлом\n"
        "/settings — настройки: обращение, перевёрнутые карты, "
        "карта дня, «как сложилось?»\n"
        "/about — о боте\n"
        "/help — помощь\n"
        "/reset — сбросить текущий расклад\n"
        "/delete_my_data — удалить все свои данные\n\n"
        "<b>Карта дня</b>\n\n"
        "Кнопка «🌅 Карта дня» в главном меню — одна карта "
        "на весь день. Присылать её автоматически в нужный час "
        "можно включить в /settings.\n\n"
        "<b>Дневник</b>\n\n"
        "Открой расклад в истории — под ним можно оставить "
        "заметку и отметить, отозвался он или нет. "
        "Сводка по всем раскладам — в /stats.",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "about")
async def about_callback(callback: CallbackQuery):
    await safe_edit(callback,
        "📚 <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "Таро здесь — инструмент символического "
        "анализа, а не способ узнать будущее.\n\n"
        "Карты не знают фактов о твоей жизни "
        "и не могут достоверно предсказывать события "
        "или читать мысли других людей.\n\n"
        "Вместо этого расклад помогает посмотреть "
        "на ситуацию через систему символов: "
        "увидеть возможные факторы, противоречия, "
        "риски и направления для размышления.\n\n"
        "Ты задаёшь вопрос.\n"
        "Карты формируют структуру расклада.\n"
        "AI интерпретирует их в контексте твоего вопроса.\n"
        "После ответа можно задать уточняющий вопрос "
        "или вытянуть уточняющую карту.\n\n"
        "<b>Что ещё умеет бот</b>\n"
        "🌅 <b>Карта дня</b> — одна карта и мысль на день. "
        "Можно вытянуть кнопкой или включить по расписанию "
        "в ⚙️ Настройках.\n"
        "⚖️ <b>«Выбор»</b> — сначала опиши ситуацию, потом "
        "бот спросит варианты А и Б по очереди.\n"
        "📖 <b>«Как сложилось?»</b> — через неделю бот спросит, "
        "отозвался ли расклад, и отметит это в дневнике.\n"
        "✨ <b>Подобрать по вопросу</b> — напиши вопрос, "
        "бот предложит подходящий формат.\n"
        "✋ <b>Своя колода</b> — тянешь карты сама, бот их читает "
        "и сохраняет расклад в дневник.\n"
        "📊 <b>Дневник</b> — заметки, отметки и частые карты. "
        "/month — итоги месяца, /export — весь дневник файлом.\n\n"
        "А решение всегда остаётся за тобой.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

    await callback.answer()

@dp.message(Command("reset"))
async def reset(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "🔮 <b>Текущий расклад сброшен.</b>\n\n"
        "Что хочешь сделать?",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

@dp.message(Command("new"))
async def new_command(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(TarotStates.choosing_spread)

    await message.answer(
        "🃏 <b>НОВЫЙ РАСКЛАД</b>\n\n"
        "Выбери формат, который лучше подходит "
        "твоему вопросу:",
        parse_mode="HTML",
        reply_markup=spread_keyboard()
    )

def format_card_text(card: dict) -> str:
    """Название карты и оба значения — для команды /card."""

    parts = [f"🃏 <b>{html.escape(card['name'])}</b>"]

    if card["arcana"] == "major":
        parts.append("<i>Старший аркан</i>")
    else:
        parts.append("<i>Младший аркан</i>")

    parts.append("")
    parts.append("<b>Прямое положение</b>")
    parts.append(html.escape(card["upright_meaning"]))
    parts.append("")
    parts.append("<b>Перевёрнутое положение</b>")
    parts.append(html.escape(card["reversed_meaning"]))

    return "\n".join(parts)


@dp.message(Command("card"))
async def card_command(message: Message):
    """
    Показывает карту и её значения без расклада и без обращения
    к модели — просто справка по колоде.
    """

    parts = (message.text or "").split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await message.answer(
            "🃏 <b>Карта из колоды</b>\n\n"
            "Напиши название после команды:\n"
            "<code>/card Башня</code>\n"
            "<code>/card 10 мечей</code>\n"
            "<code>/card дама монет</code>",
            parse_mode="HTML"
        )
        return

    matches = await asyncio.to_thread(find_cards, query)

    if not matches:
        await message.answer(
            "🃏 Не нашла такую карту.\n\n"
            "Попробуй по-другому: «Башня», «10 мечей», "
            "«туз кубков», «рыцарь пентаклей».",
            parse_mode="HTML"
        )
        return

    if len(matches) > 1:
        names = "\n".join(
            f"• {html.escape(card['name'])}"
            for card in matches[:14]
        )

        await message.answer(
            "🃏 Нашлось несколько карт — уточни, какая нужна:\n\n"
            f"{names}",
            parse_mode="HTML"
        )
        return

    card = dict(matches[0])
    card["reversed"] = False

    try:
        image_path = await asyncio.to_thread(
            render_single_card,
            card
        )

        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=format_card_text(card),
                parse_mode="HTML",
                reply_markup=card_keyboard()
            )
        finally:
            image_path.unlink(missing_ok=True)

    except Exception:
        log_metrics(
            "ERROR (CARD)",
            error=True,
            card=card["name"]
        )

        # Картинка не обязательна — значение всё равно отдаём.
        await send_html(
            message.answer,
            format_card_text(card),
            reply_markup=card_keyboard()
        )


@dp.message(Command("history"))
async def history_command(message: Message):
    text, keyboard = await build_history_response(message.from_user.id)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

def format_stats(stats: dict, usage: tuple[int, int]) -> str:
    """Сводка дневника: сколько раскладов, заметок, какие карты частят."""

    readings = stats["readings"]

    if not readings:
        return (
            "📊 <b>ДНЕВНИК</b>\n\n"
            "Пока пусто. Сделай первый расклад — он попадёт "
            "в историю, и к нему можно будет добавить заметку."
        )

    lines = [
        "📊 <b>ДНЕВНИК</b>",
        "",
        f"Всего раскладов: <b>{readings}</b>",
    ]

    if stats["first_reading"]:
        lines.append(
            f"Первый: {html.escape(stats['first_reading'][:10])}"
        )

    lines.append(f"С заметками: <b>{stats['notes']}</b>")

    if stats["resonated"] or stats["not_resonated"]:
        lines.append(
            f"Отозвалось: <b>{stats['resonated']}</b> · "
            f"мимо: <b>{stats['not_resonated']}</b>"
        )

    if stats["top_cards"]:
        lines.append("")
        lines.append("<b>Чаще всего выпадали</b>")

        for card in stats["top_cards"]:
            suffix = (
                f" (перевёрнутой — {card['reversed']})"
                if card["reversed"]
                else ""
            )

            lines.append(
                f"• {html.escape(card['name'])} — "
                f"{card['count']}{suffix}"
            )

    readings_today, llm_today = usage

    lines.append("")
    lines.append(
        f"Сегодня: {readings_today} "
        + plural(readings_today, "расклад", "расклада", "раскладов")
        + (
            f" из {DAILY_READINGS_LIMIT}"
            if DAILY_READINGS_LIMIT
            else ""
        )
        + f", обращений к AI — {llm_today}"
        + (
            f" из {DAILY_LLM_LIMIT}"
            if DAILY_LLM_LIMIT
            else ""
        )
    )

    return "\n".join(lines)


async def build_stats_text(user_id: int) -> str:
    stats = await asyncio.to_thread(get_diary_stats, user_id)
    usage = await asyncio.to_thread(get_usage, user_id)

    return format_stats(stats, usage)


def format_user_line(index: int, user: dict) -> str:
    name = user["first_name"] or "без имени"

    title = html.escape(name)

    if user["username"]:
        title += f" (@{html.escape(user['username'])})"

    line = (
        f"{index}. {title}\n"
        f"   раскладов: {user['readings']}"
        f" · запросов к AI: {user['llm_requests']}"
    )

    if user["readings_today"]:
        line += f" · сегодня: {user['readings_today']}"

    line += f"\n   последняя активность: {user['last_seen'][:16].replace('T', ' ')}"

    return line


def format_bot_stats(stats: dict) -> str:
    if not stats["users"]:
        return "👤 <b>Ботом ещё никто не пользовался.</b>"

    lines = [
        "👤 <b>КТО ПОЛЬЗУЕТСЯ</b>",
        "",
        f"Людей: <b>{stats['users']}</b>",
        f"Раскладов всего: <b>{stats['readings']}</b>",
        f"Запросов к AI: <b>{stats['llm_requests']}</b>"
        f" (сегодня — {stats['llm_today']})",
    ]

    if stats["spreads"]:
        lines.append("")
        lines.append("<b>Расклады</b>")

        for spread in stats["spreads"]:
            name = SPREADS.get(
                spread["spread_name"], {}
            ).get("name", spread["spread_name"])

            lines.append(
                f"• {html.escape(name)} — {spread['count']}"
            )

    lines.append("")
    lines.append("<b>Люди</b>")

    for index, user in enumerate(stats["top_users"], start=1):
        lines.append(format_user_line(index, user))

    return "\n".join(lines)


@dp.message(Command("admin"))
async def admin_command(message: Message):
    """
    Сводка по пользователям — только для тех, кто указан
    в ADMIN_USER_IDS. Остальным команда не отвечает вовсе.
    """

    if message.from_user.id not in ADMIN_USER_IDS:
        logger.info(
            "Попытка вызвать /admin: user_id=%s",
            message.from_user.id
        )
        return

    stats = await asyncio.to_thread(get_bot_stats)

    await send_html(
        message.answer,
        format_bot_stats(stats),
        reply_markup=main_menu_keyboard()
    )


@dp.message(Command("stats"))
async def stats_command(message: Message):
    await message.answer(
        await build_stats_text(message.from_user.id),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery):
    await safe_edit(
        callback,
        await build_stats_text(callback.from_user.id),
        reply_markup=main_menu_keyboard()
    )

    await callback.answer()


@dp.message(Command("export"))
async def export_command(message: Message, state: FSMContext):
    readings = await asyncio.to_thread(
        get_all_readings,
        message.from_user.id
    )

    if not readings:
        await message.answer(
            "📖 В дневнике пока нет раскладов — выгружать нечего."
        )
        return

    text = build_export_text(readings)
    filename = f"tarot-diary-{datetime.now().date().isoformat()}.txt"

    await message.answer_document(
        BufferedInputFile(text.encode("utf-8"), filename=filename),
        caption=(
            f"📖 Твой таро-дневник: {len(readings)} "
            f"{plural(len(readings), 'расклад', 'расклада', 'раскладов')} "
            "с вопросами, картами, интерпретациями и заметками."
        )
    )


async def build_month_summary(
    user_id: int,
    year: int,
    month: int,
    so_far: bool = False
) -> str:
    start, end = month_bounds(year, month)

    readings = await asyncio.to_thread(
        get_readings_between,
        user_id,
        start,
        end
    )

    stats = month_stats(readings)
    themes = None

    if stats["total"] >= 3:
        profile = await asyncio.to_thread(get_profile, user_id)

        try:
            themes = await asyncio.to_thread(
                month_themes,
                stats["questions"],
                profile["gender"]
            )
        except Exception:
            log_metrics("ERROR (MONTH THEMES)", error=True, user_id=user_id)

    return format_month_summary(stats, year, month, themes, so_far=so_far)


@dp.message(Command("month"))
async def month_command(message: Message, state: FSMContext):
    today = datetime.now().date()

    denied = await take_quota(message.from_user.id, is_reading=False)

    if denied:
        await message.answer(denied, parse_mode="HTML")
        return

    async with typing(message.chat.id):
        text = await build_month_summary(
            message.from_user.id,
            today.year,
            today.month,
            so_far=True
        )

    await send_html(message.answer, text, reply_markup=main_menu_keyboard())


@dp.message(Command("delete_my_data"))
async def delete_my_data_command(message: Message, state: FSMContext):
    await state.clear()

    total = await asyncio.to_thread(
        count_user_readings,
        message.from_user.id
    )

    await message.answer(
        "🗑 <b>УДАЛИТЬ ВСЕ ДАННЫЕ?</b>\n\n"
        f"Будут удалены: {total} "
        f"{plural(total, 'расклад', 'расклада', 'раскладов')} "
        "с заметками и отметками, все настройки, карта дня "
        "и счётчики лимитов.\n\n"
        "<b>Восстановить их будет нельзя.</b>",
        parse_mode="HTML",
        reply_markup=delete_data_keyboard()
    )


@dp.callback_query(F.data == "delete_data:confirm")
async def delete_my_data_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    deleted = await asyncio.to_thread(
        delete_user_data,
        callback.from_user.id
    )

    await asyncio.to_thread(delete_user_fsm, callback.from_user.id)

    logger.info(
        "Пользователь %s удалил свои данные (раскладов: %s)",
        callback.from_user.id,
        deleted
    )

    await safe_edit(
        callback,
        "🗑 <b>Готово, всё удалено.</b>\n\n"
        f"Раскладов удалено: {deleted}.\n\n"
        "Если захочешь вернуться — просто нажми /start."
    )
    await callback.answer("Удалено")


@dp.callback_query(F.data == "delete_data:cancel")
async def delete_my_data_cancel(callback: CallbackQuery):
    await safe_edit(
        callback,
        "Хорошо, ничего не удаляю.",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "new_reading")
async def new_reading(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TarotStates.choosing_spread)

    await safe_edit(callback,
        "🃏 <b>НОВЫЙ РАСКЛАД</b>\n\n"
        "Выбери формат, который лучше подходит "
        "твоему вопросу:",
        parse_mode="HTML",
        reply_markup=spread_keyboard()
    )

    await callback.answer()

@dp.message(TarotStates.waiting_for_question)
async def get_question(
    message: Message,
    state: FSMContext
):
    question = (message.text or "").strip()

    if not question:
        await message.answer(
            "🔮 Пожалуйста, напиши свой вопрос текстом."
        )
        return

    if await answer_if_crisis(message):
        return

    await process_question(
        state,
        question,
        message.from_user.id,
        message.chat.id,
        message.message_id
    )


async def process_question(
    state: FSMContext,
    question: str,
    user_id: int,
    chat_id: int,
    message_id: int | None = None
) -> None:
    """Вопрос получен — из сообщения или из подбора расклада."""

    data = await state.get_data()

    # «Выбор»: варианты спрашиваем отдельно, чтобы модель не гадала,
    # где вариант А, а где Б.
    if data.get("spread_name") == "choice":
        await state.update_data(choice_question=question)
        await state.set_state(TarotStates.waiting_for_option_a)

        await bot.send_message(
            chat_id,
            "⚖️ <b>Вариант А</b>\n\n"
            "Коротко опиши первый вариант.\n"
            "Например: <i>остаться на нынешней работе</i>",
            parse_mode="HTML"
        )
        return

    await start_reading_or_warn(
        state,
        question,
        user_id,
        chat_id,
        message_id
    )


@dp.message(TarotStates.waiting_for_option_a)
async def get_option_a(message: Message, state: FSMContext):
    option_a = (message.text or "").strip()

    if await answer_if_crisis(message):
        return

    if not option_a:
        await message.answer("⚖️ Напиши вариант А текстом.")
        return

    await state.update_data(option_a=option_a)
    await state.set_state(TarotStates.waiting_for_option_b)

    await message.answer(
        "⚖️ <b>Вариант Б</b>\n\n"
        "Теперь второй вариант.\n"
        "Например: <i>принять оффер</i>",
        parse_mode="HTML"
    )


@dp.message(TarotStates.waiting_for_option_b)
async def get_option_b(message: Message, state: FSMContext):
    option_b = (message.text or "").strip()

    if await answer_if_crisis(message):
        return

    if not option_b:
        await message.answer("⚖️ Напиши вариант Б текстом.")
        return

    data = await state.get_data()

    question = (
        f"{data.get('choice_question', '').strip()}\n"
        f"Вариант А: {data.get('option_a', '').strip()}\n"
        f"Вариант Б: {option_b}"
    )

    await start_reading_or_warn(
        state,
        question,
        message.from_user.id,
        message.chat.id,
        message.message_id
    )


async def start_reading_or_warn(
    state: FSMContext,
    question: str,
    user_id: int,
    chat_id: int,
    message_id: int | None = None
) -> None:
    """
    Перед раскладом проверяем, не задавали ли этот же вопрос за сутки.
    Если задавали — мягко предлагаем открыть тот расклад; новый можно
    сделать одной кнопкой.
    """

    data = await state.get_data()

    if data.get("live"):
        # Своя колода: карты уже вытянуты руками, повтор вопроса
        # не проверяем — просто собираем карты по позициям.
        await start_live_cards(state, question, chat_id, message_id)
        return

    previous = await asyncio.to_thread(
        find_recent_same_question,
        user_id,
        question
    )

    if previous is not None:
        await state.update_data(pending_question=question)
        await state.set_state(TarotStates.confirming_repeat)

        spread_title = SPREADS.get(
            previous["spread_name"], {}
        ).get("name", previous["spread_name"])

        await bot.send_message(
            chat_id,
            "🔁 <b>Этот вопрос уже был сегодня</b>\n\n"
            f"В {previous['created_at'][11:16]} — расклад "
            f"«{html.escape(spread_title)}».\n\n"
            "Карты не обязаны отвечать одинаково, но если тянуть их "
            "снова и снова, легко начать искать ответ, который "
            "хочется услышать. Можно вернуться к тому раскладу "
            "или всё же сделать новый.",
            parse_mode="HTML",
            reply_markup=repeat_question_keyboard(previous["id"])
        )
        return

    await perform_reading(state, question, user_id, chat_id, message_id)


@dp.callback_query(TarotStates.confirming_repeat, F.data == "repeat:go")
async def repeat_go(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    question = data.get("pending_question")

    if not question:
        await callback.answer(
            "Расклад устарел. Начни заново: /start",
            show_alert=True
        )
        return

    await callback.answer("🔮 Тяну карты…")

    await perform_reading(
        state,
        question,
        callback.from_user.id,
        chat_id_of(callback)
    )


@dp.message(TarotStates.confirming_repeat)
async def repeat_waiting(message: Message):
    await message.answer(
        "🔁 Выбери кнопку выше: открыть прошлый расклад "
        "или сделать новый. Или /start — в главное меню."
    )


# -------------------------
# Расклад со своей колоды
# -------------------------

REVERSED_WORDS = ("перевернут", "перевёрнут", "перев", "rev", "↓")


def parse_live_card(text: str) -> tuple[list[dict], bool]:
    """«10 мечей перевёрнутая» → (найденные карты, перевёрнута ли)."""

    lowered = text.lower()
    is_reversed = any(word in lowered for word in REVERSED_WORDS)

    cleaned = lowered.replace("↓", " ")

    for word in ("перевернутая", "перевёрнутая", "перевернутый",
                 "перевёрнутый", "перевернута", "перевёрнута",
                 "перевернут", "перевёрнут", "перев.", "перев", "rev"):
        cleaned = cleaned.replace(word, " ")

    for word in ("прямая", "прямой", "прямо"):
        cleaned = cleaned.replace(word, " ")

    return find_cards(cleaned), is_reversed


def live_card_prompt(spread_name: str, index: int) -> str:
    positions = SPREADS[spread_name]["cards"]

    return (
        f"✋ <b>Карта {index + 1} из {len(positions)}</b> — "
        f"«{html.escape(positions[index])}»\n\n"
        "Какая карта выпала? Например: <i>Башня</i>, "
        "<i>10 мечей</i>, <i>дама кубков перевёрнутая</i>."
    )


async def start_live_cards(
    state: FSMContext,
    question: str,
    chat_id: int,
    message_id: int | None
) -> None:
    data = await state.get_data()
    spread_name = data.get("spread_name")

    await state.update_data(
        live_question=question,
        live_message_id=message_id,
        live_cards=[]
    )
    await state.set_state(TarotStates.waiting_for_live_card)

    await bot.send_message(
        chat_id,
        live_card_prompt(spread_name, 0),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "live:start")
async def live_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(live=True)
    await state.set_state(TarotStates.choosing_spread)

    await safe_edit(
        callback,
        "✋ <b>РАСКЛАД СО СВОЕЙ КОЛОДЫ</b>\n\n"
        "Ты тянешь карты сама, а я помогаю их прочитать "
        "и сохраняю расклад в дневник.\n\n"
        "Выбери формат:",
        reply_markup=spread_keyboard(extras=False)
    )
    await callback.answer()


@dp.message(TarotStates.waiting_for_live_card)
async def get_live_card(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if not text:
        await message.answer("✋ Напиши название карты текстом.")
        return

    data = await state.get_data()
    spread_name = data.get("spread_name")
    cards = list(data.get("live_cards") or [])

    if spread_name not in SPREADS:
        await state.clear()
        await message.answer(
            "🔮 Расклад сбился. Начни заново: /new",
            reply_markup=main_menu_keyboard()
        )
        return

    matches, is_reversed = await asyncio.to_thread(parse_live_card, text)

    if not matches:
        await message.answer(
            "✋ Не нашла такую карту. Попробуй иначе: "
            "«Башня», «10 мечей», «туз кубков», «рыцарь пентаклей».",
        )
        return

    if len(matches) > 1:
        names = "\n".join(
            f"• {html.escape(card['name'])}" for card in matches[:12]
        )
        await message.answer(
            f"✋ Подходит несколько карт — уточни:\n\n{names}",
            parse_mode="HTML"
        )
        return

    card = dict(matches[0])

    if any(item["card"]["name"] == card["name"] for item in cards):
        await message.answer(
            f"✋ {html.escape(card['name'])} уже есть в этом раскладе. "
            "Проверь, какая карта выпала на этой позиции.",
            parse_mode="HTML"
        )
        return

    card["reversed"] = is_reversed
    card["meaning"] = (
        card["reversed_meaning"] if is_reversed else card["upright_meaning"]
    )

    positions = SPREADS[spread_name]["cards"]
    cards.append({"position": positions[len(cards)], "card": card})
    await state.update_data(live_cards=cards)

    if len(cards) < len(positions):
        await message.answer(
            f"✓ {html.escape(card['name'])}"
            f"{' · перевёрнутая' if is_reversed else ''}\n\n"
            + live_card_prompt(spread_name, len(cards)),
            parse_mode="HTML"
        )
        return

    await perform_reading(
        state,
        data.get("live_question", ""),
        message.from_user.id,
        message.chat.id,
        message.message_id,
        preset_spread=cards
    )


# -------------------------
# Подбор расклада по вопросу
# -------------------------

@dp.callback_query(F.data == "suggest:start")
async def suggest_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TarotStates.waiting_for_suggest_question)

    await safe_edit(
        callback,
        "✨ <b>ПОДБОР РАСКЛАДА</b>\n\n"
        "Напиши свой вопрос — подскажу, какой формат подойдёт, "
        "и второй раз вводить его не придётся."
    )
    await callback.answer()


@dp.message(TarotStates.waiting_for_suggest_question)
async def suggest_question(message: Message, state: FSMContext):
    question = (message.text or "").strip()

    if not question:
        await message.answer("✨ Напиши вопрос текстом.")
        return

    if await answer_if_crisis(message):
        return

    spread_name, reason = suggest_spread(question)

    await state.update_data(
        prefilled_question=question,
        prefilled_message_id=message.message_id
    )
    await state.set_state(TarotStates.choosing_spread)

    await message.answer(
        "✨ Похоже, подойдёт расклад "
        f"<b>«{html.escape(SPREADS[spread_name]['name'])}»</b> — "
        f"{html.escape(reason)}.",
        parse_mode="HTML",
        reply_markup=suggest_keyboard(
            spread_name,
            SPREADS[spread_name]["name"]
        )
    )


@dp.callback_query(F.data == "suggest:other")
async def suggest_other(callback: CallbackQuery):
    # Вопрос уже сохранён в состоянии — просто показываем все форматы.
    await safe_edit(
        callback,
        "🃏 <b>Выбери формат</b> — вопрос уже записан:",
        reply_markup=spread_keyboard(extras=False)
    )
    await callback.answer()


async def react_to_question(chat_id: int, message_id: int | None) -> None:
    """Ставит 👀 на сообщение с вопросом. Не получилось — не страшно."""

    if message_id is None:
        return

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji="👀")]
        )
    except Exception:
        logger.debug("Не удалось поставить реакцию", exc_info=True)


async def perform_reading(
    state: FSMContext,
    question: str,
    user_id: int,
    chat_id: int,
    message_id: int | None = None,
    preset_spread: list[dict] | None = None
) -> None:
    """
    Вытягивает карты, получает интерпретацию, сохраняет и отправляет.
    Работает и из сообщения, и из кнопки, поэтому отвечает через
    bot.send_* по chat_id, а не через message.answer.
    """

    await state.set_state(
        TarotStates.processing
    )

    total_start = time.perf_counter()

    data = await state.get_data()

    spread_name = data.get("spread_name")
    use_reversed = data.get("reversed_cards", False)

    if spread_name not in SPREADS:
        await state.clear()
        await state.set_state(TarotStates.choosing_spread)

        await bot.send_message(
            chat_id,
            "🔮 Не понял, какой расклад делать. Выбери его заново:",
            reply_markup=spread_keyboard()
        )
        return

    denied = await take_quota(
        user_id,
        is_reading=True
    )

    if denied:
        await state.set_state(TarotStates.choosing_spread)

        await bot.send_message(
            chat_id,
            denied,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    # -------------------------
    # 1. Вытягиваем карты
    # -------------------------

    try:
        draw_start = time.perf_counter()

        # Вместо служебного сообщения «расклад создаётся» — реакция 👀
        # на вопрос: видно, что бот принял его, а чат не засоряется.
        await react_to_question(chat_id, message_id)

        spread = preset_spread or make_spread(
            spread_name,
            reversed_cards=use_reversed
        )
        await state.update_data(
            spread=spread,
            question=question,
            spread_name=spread_name,
            reversed_cards=use_reversed
        )

        draw_time = time.perf_counter() - draw_start

        async with ChatActionSender.upload_photo(bot=bot, chat_id=chat_id):
            image_path = await asyncio.to_thread(
                render_spread,
                spread,
                spread_name
            )

        caption_lines = [
            f"📍 {item['position']} — "
            f"{item['card']['name']} · "
            f"{'перевёрнутая' if item['card']['reversed'] else 'прямая'}"
            for item in spread
        ]

        try:
            await bot.send_photo(
                chat_id,
                photo=FSInputFile(image_path),
                caption=(
                    f"{'✋' if preset_spread else '🔮'} "
                    f"<b>{html.escape(SPREADS[spread_name]['name'])}</b>"
                    f"{' · твоя колода' if preset_spread else ''}\n\n"
                    + "\n".join(
                        html.escape(line)
                        for line in caption_lines
                    )
                ),
                parse_mode="HTML"
            )
        finally:
            image_path.unlink(missing_ok=True)

    except Exception as error:
        log_metrics(
            "ERROR (draw)",
            error=True,
            question=question,
            spread=spread_name
        )

        await bot.send_message(
            chat_id,
            "🔮 Не получилось вытянуть карты для этого расклада. "
            "Попробуй начать заново через /start."
        )

        await state.clear()
        return

    # -------------------------
    # 2. Получаем интерпретацию
    # -------------------------

    try:
        llm_start = time.perf_counter()

        gender = await asyncio.to_thread(
            get_user_gender,
            user_id
        )

        async with typing(chat_id):
            interpretation = await asyncio.to_thread(
                interpret_tarot,
                question=question,
                spread_name=spread_name,
                spread=spread,
                gender=gender
            )

        llm_time = time.perf_counter() - llm_start

    except Exception as error:
        total_time = time.perf_counter() - total_start

        log_metrics(
            "ERROR (LLM)",
            error=True,
            question=question,
            spread=spread_name,
            cards=len(spread),
            draw=seconds(draw_time),
            total=seconds(total_time)
        )

        await bot.send_message(
            chat_id,
            "⚠️ <b>ИНТЕРПРЕТАЦИЯ ВРЕМЕННО НЕДОСТУПНА</b>\n\n"
            "Карты уже выбраны, но сейчас AI "
            "не смог завершить интерпретацию.\n\n"
            "Твой расклад не потерян. "
            "Можно попробовать ещё раз — "
            "карты останутся теми же.",
            parse_mode="HTML",
            reply_markup=llm_error_keyboard()
        )

        return

    # -------------------------
    # 3. Сохраняем чтение
    # -------------------------

    reading_id = None

    try:
        reading_id = await asyncio.to_thread(
            save_reading,
            user_id=user_id,
            question=question,
            spread_name=spread_name,
            spread=spread,
            interpretation=interpretation
        )

    except Exception as error:
        log_metrics(
            "ERROR (DATABASE)",
            error=True,
            question=question,
            spread=spread_name
        )

        # Само чтение всё равно можно показать пользователю.
        # Ошибка сохранения не должна ломать весь расклад.

    # -------------------------
    # 4. Отправляем интерпретацию
    # -------------------------

    try:
        telegram_start = time.perf_counter()

        chunks = await send_llm_response(
            partial(bot.send_message, chat_id),
            interpretation,
            prefix="🔮 <b>Интерпретация расклада</b>\n\n",
            suffix=FOLLOWUP_HINT,
            reply_markup=after_reading_keyboard(reading_id)
        )

        telegram_time = time.perf_counter() - telegram_start
        total_time = time.perf_counter() - total_start

        log_metrics(
            "SUCCESS",
            question=question,
            spread=spread_name,
            cards=len(spread),
            draw=seconds(draw_time),
            llm=seconds(llm_time),
            telegram=seconds(telegram_time),
            total=seconds(total_time),
            length=f"{len(interpretation)} chars",
            chunks=len(chunks)
        )

    except Exception as error:
        total_time = time.perf_counter() - total_start

        log_metrics(
            "ERROR (TELEGRAM)",
            error=True,
            question=question,
            spread=spread_name,
            cards=len(spread),
            draw=seconds(draw_time),
            llm=seconds(llm_time),
            total=seconds(total_time)
        )

    # Переходим в режим уточняющих вопросов: пока пользователь не
    # начнёт новый расклад, любое его текстовое сообщение считается
    # уточняющим вопросом по этому же раскладу.

    await state.set_state(TarotStates.follow_up)

    await state.update_data(
        spread=spread,
        question=question,
        interpretation=interpretation,
        history=[],
        reading_id=reading_id
    )

@dp.message(TarotStates.processing)
async def still_processing(message: Message):
    """
    Подстраховка: расклад уже вытянут, но интерпретация ещё не
    получена (либо только что упала с ошибкой) — без этого хендлера
    сообщение пользователя в этом состоянии просто теряется.
    """

    await message.answer(
        "🔮 Уже пробую получить интерпретацию этого расклада.\n\n"
        "Если выше есть кнопка «🔄 Повторить» — нажми её, "
        "либо напиши /reset, чтобы начать заново."
    )

@dp.callback_query(F.data == "retry_interpretation")
async def retry_interpretation(
    callback: CallbackQuery,
    state: FSMContext
):
    data = await state.get_data()

    spread = data.get("spread")
    spread_name = data.get("spread_name")
    question = data.get("question")

    if not spread or not spread_name or not question:
        await callback.answer(
            "Расклад не найден. Начни новый расклад.",
            show_alert=True
        )
        return

    denied = await take_quota(
        callback.from_user.id,
        is_reading=False
    )

    if denied:
        await callback.answer()

        await callback.message.answer(
            denied,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    await callback.answer("Повторяю интерпретацию…")

    # llm_start нужен и в except — объявляем до try, иначе падение
    # на сообщении выше даст UnboundLocalError вместо ответа пользователю.
    llm_start = time.perf_counter()

    try:
        await callback.message.answer(
            "🔄 <b>ПОВТОРНАЯ ПОПЫТКА</b>\n\n"
            "Карты остаются прежними.\n"
            "Пробую получить интерпретацию ещё раз…",
            parse_mode="HTML"
        )

        gender = await asyncio.to_thread(
            get_user_gender,
            callback.from_user.id
        )

        async with typing(chat_id_of(callback)):
            interpretation = await asyncio.to_thread(
                interpret_tarot,
                question=question,
                spread_name=spread_name,
                spread=spread,
                gender=gender
            )

        llm_time = time.perf_counter() - llm_start

    except Exception as error:
        log_metrics(
            "ERROR (RETRY)",
            error=True,
            question=question,
            spread=spread_name,
            cards=len(spread),
            llm=seconds(time.perf_counter() - llm_start)
        )

        await callback.message.answer(
            "⚠️ <b>Интерпретация снова недоступна.</b>\n\n"
            "Карты сохранены. Можно попробовать ещё раз "
            "или начать новый расклад.",
            parse_mode="HTML",
            reply_markup=llm_error_keyboard()
        )
        return

    # Сохраняем успешную интерпретацию
    reading_id = None

    try:
        reading_id = await asyncio.to_thread(
            save_reading,
            user_id=callback.from_user.id,
            question=question,
            spread_name=spread_name,
            spread=spread,
            interpretation=interpretation
        )

    except Exception as error:
        log_metrics(
            "ERROR (DATABASE RETRY)",
            error=True,
            question=question,
            spread=spread_name
        )

    chunks = await send_llm_response(
        callback.message.answer,
        interpretation,
        prefix="🔮 <b>Интерпретация расклада</b>\n\n",
        suffix=FOLLOWUP_HINT,
        reply_markup=after_reading_keyboard(reading_id)
    )

    await state.set_state(TarotStates.follow_up)

    await state.update_data(
        interpretation=interpretation,
        history=[],
        reading_id=reading_id
    )

    log_metrics(
        "RETRY SUCCESS",
        question=question,
        spread=spread_name,
        cards=len(spread),
        llm=seconds(llm_time),
        length=f"{len(interpretation)} chars",
        chunks=len(chunks)
    )

# Повтор того же уточнения в течение этого времени игнорируем.
FOLLOWUP_DUPLICATE_SECONDS = 60


@dp.message(TarotStates.follow_up)
async def get_followup(
    message: Message,
    state: FSMContext
):
    follow_up_question = message.text

    if not follow_up_question:
        await message.answer(
            "🔮 Пожалуйста, напиши уточняющий вопрос текстом."
        )
        return

    if await answer_if_crisis(message):
        return

    data = await state.get_data()

    # Одно и то же сообщение, отправленное дважды подряд (двойное
    # нажатие, «не дошло?»), не стоит второго ответа и второго запроса
    # к модели.
    normalized = " ".join(follow_up_question.lower().split())
    now_ts = time.time()
    last = data.get("last_followup") or {}

    if (
        last.get("text") == normalized
        and now_ts - last.get("ts", 0) < FOLLOWUP_DUPLICATE_SECONDS
    ):
        logger.info(
            "Повтор уточняющего вопроса проигнорирован: user_id=%s",
            message.from_user.id
        )

        # Молчание выглядит как зависание — коротко показываем,
        # что ответ уже есть (без запроса к модели).
        await message.answer("☝️ Ответила на это сообщение чуть выше.")
        return

    await state.update_data(
        last_followup={"text": normalized, "ts": now_ts}
    )

    spread_name = data.get("spread_name")
    spread = data.get("spread")
    base_question = data.get("question")
    base_interpretation = data.get("interpretation")
    history = data.get("history", [])

    if not spread or not base_interpretation:
        await state.clear()
        await state.set_state(TarotStates.choosing_spread)

        await message.answer(
            "🔮 Этот расклад больше недоступен для уточнений "
            "(например, бот перезапускался). Давай сделаем новый:",
            reply_markup=spread_keyboard()
        )
        return

    denied = await take_quota(
        message.from_user.id,
        is_reading=False
    )

    if denied:
        await message.answer(
            denied,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    try:
        gender = await asyncio.to_thread(
            get_user_gender,
            message.from_user.id
        )

        async with typing(message.chat.id):
            answer = await asyncio.to_thread(
                answer_followup,
                base_question=base_question,
                spread_name=spread_name,
                spread=spread,
                base_interpretation=base_interpretation,
                history=history,
                follow_up_question=follow_up_question,
                gender=gender
            )

    except Exception as error:
        log_metrics(
            "ERROR (FOLLOWUP)",
            error=True,
            question=follow_up_question,
            spread=spread_name
        )

        await message.answer(
            "🔮 Не получилось получить ответ на уточняющий вопрос. "
            "Попробуй ещё раз чуть позже."
        )
        return

    history.append({
        "question": follow_up_question,
        "answer": answer
    })

    await state.update_data(history=history)

    await send_llm_response(
        message.answer,
        answer,
        reply_markup=after_reading_keyboard(data.get("reading_id"))
    )

@dp.callback_query(F.data == "draw_clarifying_card")
async def draw_clarifying_card_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    data = await state.get_data()

    spread = data.get("spread")
    spread_name = data.get("spread_name")
    base_question = data.get("question")
    base_interpretation = data.get("interpretation")

    if not spread:
        await callback.answer(
            "Сначала сделай расклад через /start.",
            show_alert=True
        )
        return

    denied = await take_quota(
        callback.from_user.id,
        is_reading=False
    )

    if denied:
        await callback.answer()

        await callback.message.answer(
            denied,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    await callback.answer()

    use_reversed = data.get("reversed_cards", True)
    exclude_names = {item["card"]["name"] for item in spread}

    try:
        new_card = await asyncio.to_thread(
            draw_single_card,
            exclude_names,
            use_reversed
        )
    except Exception as error:
        log_metrics(
            "ERROR (CLARIFYING DRAW)",
            error=True,
            spread=spread_name
        )

        await callback.message.answer(
            "🔮 Не получилось вытянуть уточняющую карту. "
            "Попробуй ещё раз чуть позже."
        )
        return

    clarifying_count = sum(
        1 for item in spread
        if item["position"].startswith("Уточняющая карта")
    )

    position_label = (
        "Уточняющая карта"
        if clarifying_count == 0
        else f"Уточняющая карта №{clarifying_count + 1}"
    )

    new_item = {
        "position": position_label,
        "card": new_card
    }

    # В state и на картинку кладём расклад вместе с уточняющей картой,
    # а в промпт — исходный, иначе новая карта попадает в него дважды.
    base_spread = spread
    spread = spread + [new_item]

    orientation = (
        "прямая"
        if not new_card["reversed"]
        else "перевёрнутая"
    )

    try:
        image_path = await asyncio.to_thread(
            render_single_card,
            new_card
        )

        try:
            await callback.message.answer_photo(
                photo=FSInputFile(image_path),
                caption=(
                    f"🔎 <b>{html.escape(position_label)}</b>\n\n"
                    f"🃏 {html.escape(new_card['name'])} — {orientation}"
                ),
                parse_mode="HTML"
            )
        finally:
            image_path.unlink(missing_ok=True)

        gender = await asyncio.to_thread(
            get_user_gender,
            callback.from_user.id
        )

        async with typing(chat_id_of(callback)):
            interpretation = await asyncio.to_thread(
                interpret_clarifying_card,
                base_question,
                spread_name,
                base_spread,
                base_interpretation,
                new_item,
                gender=gender
            )

    except Exception as error:
        log_metrics(
            "ERROR (CLARIFYING INTERPRETATION)",
            error=True,
            spread=spread_name
        )

        await callback.message.answer(
            "🔮 Карта вытянута, но не получилось получить её "
            "интерпретацию. Попробуй ещё раз чуть позже."
        )
        return

    history = data.get("history", [])

    history.append({
        "question": f"[Вытянута уточняющая карта: {new_card['name']} ({orientation})]",
        "answer": interpretation
    })

    await state.update_data(
        spread=spread,
        history=history
    )

    await send_llm_response(
        callback.message.answer,
        interpretation
    )

HISTORY_PAGE_SIZE = 10


async def build_history_response(user_id: int, page: int = 0):
    """
    Страница истории: текст и клавиатура. Общее для команды
    /history, кнопки «История» и листалки.
    """

    total = await asyncio.to_thread(count_user_readings, user_id)

    if not total:
        return (
            "📖 <b>История пока пуста.</b>\n\n"
            "Сделай первый расклад, и он появится здесь.",
            history_keyboard([])
        )

    pages = max(1, -(-total // HISTORY_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    offset = page * HISTORY_PAGE_SIZE

    readings = await asyncio.to_thread(
        get_user_readings,
        user_id=user_id,
        limit=HISTORY_PAGE_SIZE,
        offset=offset
    )

    counter = (
        f" · страница {page + 1} из {pages}"
        if pages > 1
        else ""
    )

    text = (
        "📖 <b>История раскладов</b>\n\n"
        f"Всего раскладов: {total}{counter}\n\n"
        "Выбери расклад, чтобы открыть его:"
    )

    keyboard = history_keyboard(
        readings,
        page=page,
        pages=pages,
        start_index=offset + 1
    )

    return (text, keyboard)


@dp.callback_query(F.data.startswith("history_page:"))
async def show_history_page(callback: CallbackQuery):
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Не удалось открыть страницу.")
        return

    text, keyboard = await build_history_response(
        callback.from_user.id,
        page
    )

    # Листаем в том же сообщении, новое плодить незачем.
    await safe_edit(
        callback,
        text,
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(F.data == "history")
async def show_history(
    callback: CallbackQuery
):
    text, keyboard = await build_history_response(callback.from_user.id)

    # Новым сообщением: кнопка «История» висит под интерпретацией,
    # и редактирование стирало бы её из чата.
    await callback.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

    await callback.answer()

def format_resonance(resonance: str | None) -> str:
    if resonance == "yes":
        return "· ✅ отозвалось"

    if resonance == "no":
        return "· ❌ мимо"

    return ""


def format_note(note: str | None) -> str:
    if not note:
        return ""

    return (
        "📝 <b>Заметка:</b>\n"
        f"{html.escape(note)}\n\n"
    )


async def load_own_reading(callback: CallbackQuery, reading_id: int):
    """Читает расклад и проверяет, что он принадлежит этому человеку."""

    reading = await asyncio.to_thread(
        get_reading,
        reading_id=reading_id,
        user_id=callback.from_user.id
    )

    if reading is None:
        await callback.answer(
            "Расклад не найден.",
            show_alert=True
        )

    return reading


def parse_reading_id(data: str) -> int | None:
    try:
        return int(data.split(":")[1])
    except (ValueError, IndexError):
        return None


@dp.callback_query(F.data.startswith("note:"))
async def add_note(callback: CallbackQuery, state: FSMContext):
    reading_id = parse_reading_id(callback.data)

    if reading_id is None:
        await callback.answer("Не удалось открыть расклад.")
        return

    reading = await load_own_reading(callback, reading_id)

    if reading is None:
        return

    # Запоминаем, куда вернуться: заметку можно писать и посреди
    # уточняющих вопросов по свежему раскладу.
    await state.update_data(
        note_reading_id=reading_id,
        state_before_note=await state.get_state()
    )

    await state.set_state(TarotStates.waiting_for_note)

    current = reading["note"]

    await callback.answer()

    await callback.message.answer(
        "📝 <b>Заметка к раскладу</b>\n\n"
        + (
            f"Сейчас записано:\n{html.escape(current)}\n\n"
            if current
            else ""
        )
        + "Напиши, что откликнулось или что произошло потом — "
        "я сохраню это рядом с раскладом.",
        parse_mode="HTML",
        reply_markup=note_cancel_keyboard(reading_id)
    )


@dp.message(TarotStates.waiting_for_note)
async def save_note(message: Message, state: FSMContext):
    note = (message.text or "").strip()

    if not note:
        await message.answer(
            "📝 Заметка должна быть текстом. "
            "Напиши пару строк или нажми «Отмена»."
        )
        return

    data = await state.get_data()
    reading_id = data.get("note_reading_id")

    if reading_id is None:
        await state.set_state(TarotStates.choosing_spread)

        await message.answer(
            "🔮 Не поняла, к какому раскладу заметка. "
            "Открой его через /history.",
            reply_markup=main_menu_keyboard()
        )
        return

    saved = await asyncio.to_thread(
        set_reading_note,
        reading_id,
        message.from_user.id,
        note
    )

    await state.set_state(data.get("state_before_note"))
    await state.update_data(note_reading_id=None)

    if not saved:
        await message.answer(
            "🔮 Не получилось сохранить заметку: расклад не найден."
        )
        return

    await message.answer(
        "📝 <b>Заметка сохранена.</b>",
        parse_mode="HTML",
        reply_markup=reading_keyboard(
            reading_id,
            note=note
        )
    )


@dp.callback_query(F.data.startswith("note_delete:"))
async def delete_note(callback: CallbackQuery, state: FSMContext):
    reading_id = parse_reading_id(callback.data)

    if reading_id is None:
        await callback.answer("Не удалось открыть расклад.")
        return

    await asyncio.to_thread(
        set_reading_note,
        reading_id,
        callback.from_user.id,
        None
    )

    data = await state.get_data()

    if await state.get_state() == TarotStates.waiting_for_note.state:
        await state.set_state(data.get("state_before_note"))
        await state.update_data(note_reading_id=None)

    await callback.answer("Заметка удалена")

    await safe_edit(
        callback,
        "📝 Заметка удалена.",
        reply_markup=reading_keyboard(reading_id)
    )


@dp.callback_query(F.data.startswith("resonance:"))
async def set_resonance(callback: CallbackQuery):
    parts = callback.data.split(":")

    reading_id = parse_reading_id(callback.data)
    choice = parts[2] if len(parts) > 2 else ""

    if reading_id is None or choice not in ("yes", "no", "clear"):
        await callback.answer("Не удалось поставить отметку.")
        return

    reading = await load_own_reading(callback, reading_id)

    if reading is None:
        return

    resonance = None if choice == "clear" else choice

    await asyncio.to_thread(
        set_reading_resonance,
        reading_id,
        callback.from_user.id,
        resonance
    )

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=reading_keyboard(
                    reading_id,
                    note=reading["note"],
                    resonance=resonance
                )
            )
        except TelegramBadRequest as error:
            logger.warning(
                "Не удалось обновить отметку: %s",
                error
            )

    await callback.answer(
        {
            "yes": "Отмечено: отозвалось",
            "no": "Отмечено: мимо",
            "clear": "Отметка снята"
        }[choice]
    )


@dp.callback_query(F.data.startswith("history_reading:"))
async def show_reading(
    callback: CallbackQuery
):
    try:
        reading_id = int(
            callback.data.split(":")[1]
        )
    except (ValueError, IndexError):
        await callback.answer(
            "Не удалось открыть расклад."
        )
        return

    reading = await asyncio.to_thread(
        get_reading,
        reading_id=reading_id,
        user_id=callback.from_user.id
    )

    if reading is None:
        await callback.answer(
            "Расклад не найден."
        )
        return

    spread = json.loads(
        reading["spread"]
    )

    created_at = reading["created_at"]
    question = html.escape(reading["question"])
    interpretation = reading["interpretation"]
    note = reading["note"]
    resonance = reading["resonance"]

    header = (
        "🔮 <b>Расклад из истории</b>\n\n"
        f"📅 {created_at[:10]} {format_resonance(resonance)}\n\n"
        f"❓ <b>Вопрос:</b>\n"
        f"{question}\n\n"
        f"{format_spread(spread)}\n\n"
        f"{format_note(note)}"
        "🔮 <b>Интерпретация</b>"
    )

    # Отправляем новым сообщением, а не редактированием: иначе
    # расклад затирает сообщение, на котором нажали кнопку.
    await send_html(
        callback.message.answer,
        header
    )

    await send_llm_response(
        callback.message.answer,
        interpretation,
        reply_markup=reading_keyboard(
            reading_id,
            note=note,
            resonance=resonance
        )
    )

    await callback.answer()


@dp.callback_query()
async def stale_button(callback: CallbackQuery):
    """
    Любая кнопка, для которой не нашлось хендлера — например,
    «Да/Нет» на старом сообщении. Без этого Telegram крутит
    часики до таймаута.
    """

    await callback.answer(
        "Эта кнопка устарела. Начни заново: /start"
    )


@dp.errors()
async def handle_error(event: ErrorEvent):
    """
    Ловит любое исключение, не пойманное внутри хендлеров,
    чтобы пользователь получал понятный ответ вместо тишины.
    """

    log_metrics(
        "None",
        error=True
    )

    traceback.print_exception(event.exception)

    update = event.update

    try:
        if update.message:
            await update.message.answer(
                "🔮 Что-то пошло не так. "
                "Попробуй начать заново через /start."
            )
        elif update.callback_query:
            await update.callback_query.answer(
                "Что-то пошло не так. Попробуй /start.",
                show_alert=True
            )
    except Exception:
        pass


# -------------------------
# Расписание: карта дня и «как сложилось?»
# -------------------------

# Не беспокоим «как сложилось?» ночью: только с 10:00 до 21:00.
CHECKIN_HOURS = range(10, 21)

SCHEDULER_INTERVAL_SECONDS = 60


async def get_or_draw_daily_card(user_id: int) -> tuple[dict, str]:
    """
    Карта дня одна на весь день: если сегодня её уже тянули
    (по кнопке или по расписанию), возвращаем ту же самую.
    """

    today = datetime.now().date().isoformat()

    saved = await asyncio.to_thread(get_today_daily_card, user_id, today)

    if saved is not None:
        return saved["card"], saved["text"]

    profile = await asyncio.to_thread(get_profile, user_id)
    reversed_pref = await asyncio.to_thread(
        get_reversed_cards_preference,
        user_id
    )

    card = await asyncio.to_thread(
        draw_single_card,
        None,
        reversed_pref is True
    )

    try:
        text = await asyncio.to_thread(
            daily_card_message,
            card,
            profile["gender"]
        )
    except Exception:
        log_metrics("ERROR (DAILY LLM)", error=True, card=card["name"])
        # Без модели всё равно отдаём карту со справочным значением
        # и не сохраняем: при следующем запросе попробуем ещё раз.
        return card, card["meaning"]

    await asyncio.to_thread(save_today_daily_card, user_id, today, card, text)

    return card, text


async def send_daily_card(user_id: int) -> None:
    card, text = await get_or_draw_daily_card(user_id)

    orientation = "перевёрнутая" if card["reversed"] else "прямая"

    caption = (
        "🌅 <b>Карта дня</b>\n\n"
        f"{html.escape(card['name'])} · {orientation}"
    )

    try:
        image_path = await asyncio.to_thread(render_single_card, card)

        try:
            await bot.send_photo(
                user_id,
                photo=FSInputFile(image_path),
                caption=caption,
                parse_mode="HTML"
            )
        finally:
            image_path.unlink(missing_ok=True)

    except TelegramForbiddenError:
        raise
    except Exception:
        log_metrics("ERROR (DAILY IMAGE)", error=True, card=card["name"])
        await bot.send_message(user_id, caption, parse_mode="HTML")

    await send_llm_response(
        partial(bot.send_message, user_id),
        text,
        reply_markup=daily_card_keyboard()
    )


async def send_checkin(reading) -> None:
    spread_title = SPREADS.get(
        reading["spread_name"], {}
    ).get("name", reading["spread_name"])

    question = reading["question"]

    if len(question) > 300:
        question = question[:300].rstrip() + "…"

    await bot.send_message(
        reading["user_id"],
        "📖 <b>Как сложилось?</b>\n\n"
        f"{reading['created_at'][:10]} был расклад "
        f"«{html.escape(spread_title)}» на вопрос:\n"
        f"<i>{html.escape(question)}</i>\n\n"
        "Прошла неделя. Отозвался он или прошёл мимо? "
        "Отметка сохранится в дневнике (/stats).",
        parse_mode="HTML",
        reply_markup=checkin_keyboard(reading["id"])
    )


async def send_monthly_summaries(today) -> None:
    year, month = previous_month(today)
    start, end = month_bounds(year, month)
    key = month_key(year, month)

    recipients = await asyncio.to_thread(
        get_monthly_recipients,
        start,
        end,
        key
    )

    for user_id in recipients:
        await asyncio.to_thread(mark_monthly_sent, user_id, key)

        try:
            text = await build_month_summary(user_id, year, month)
            await send_html(
                partial(bot.send_message, user_id),
                text,
                reply_markup=main_menu_keyboard()
            )
            logger.info("Итоги месяца отправлены: user_id=%s", user_id)
        except TelegramForbiddenError:
            await asyncio.to_thread(set_monthly_enabled, user_id, False)
        except Exception:
            log_metrics("ERROR (MONTHLY)", error=True, user_id=user_id)


async def run_schedule_tick() -> None:
    now = datetime.now()
    today = now.date().isoformat()

    recipients = await asyncio.to_thread(
        get_daily_card_recipients,
        now.hour,
        today
    )

    for user_id in recipients:
        # Отмечаем до отправки: если что-то упадёт посередине,
        # человек не получит две карты за день.
        await asyncio.to_thread(mark_daily_card_sent, user_id, today)

        try:
            await send_daily_card(user_id)
            logger.info("Карта дня отправлена: user_id=%s", user_id)
        except TelegramForbiddenError:
            # Человек заблокировал бота — больше не шлём.
            await asyncio.to_thread(set_daily_hour, user_id, None)
        except Exception:
            log_metrics("ERROR (DAILY)", error=True, user_id=user_id)

    # Итоги месяца — 1-го числа с 11:00 (если бот был выключен в 11,
    # отправим позже в тот же день: дубли отсекает monthly_last_sent).
    if now.day == 1 and 11 <= now.hour < 21:
        await send_monthly_summaries(now.date())

    if now.hour not in CHECKIN_HOURS:
        return

    due = await asyncio.to_thread(
        get_due_checkins,
        now.isoformat(timespec="seconds"),
        today
    )

    for reading in due:
        await asyncio.to_thread(mark_checkin_sent, reading["id"])

        try:
            await send_checkin(reading)
            logger.info("«Как сложилось?» отправлено: reading_id=%s", reading["id"])
        except TelegramForbiddenError:
            await asyncio.to_thread(
                set_checkins_enabled,
                reading["user_id"],
                False
            )
        except Exception:
            log_metrics("ERROR (CHECKIN)", error=True, reading_id=reading["id"])


async def scheduler() -> None:
    """Раз в минуту проверяет, кому пора отправить карту дня или «как сложилось?»."""

    while True:
        try:
            await run_schedule_tick()
        except Exception:
            log_metrics("ERROR (SCHEDULER)", error=True)

        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="new", description="Новый расклад"),
    BotCommand(command="card", description="Значение карты: /card Башня"),
    BotCommand(command="history", description="История раскладов"),
    BotCommand(command="stats", description="Дневник и статистика"),
    BotCommand(command="month", description="Итоги месяца на сегодня"),
    BotCommand(command="export", description="Выгрузить дневник файлом"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="reset", description="Сбросить текущий расклад"),
    BotCommand(command="delete_my_data", description="Удалить все свои данные"),
]


async def main():
    init_database()

    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.warning("Не удалось обновить меню команд", exc_info=True)

    scheduler_task = asyncio.create_task(scheduler())

    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()

