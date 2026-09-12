import asyncio
import html
import json
import logging
import os
import re
import time
import traceback

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardMarkup,
    Message,
    FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
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
    settings_keyboard
)

from .states import TarotStates
from .tarot.deck import make_spread, draw_single_card, find_cards
from .tarot.renderer import render_spread, render_single_card
from .llm.cloudru import (
    interpret_tarot,
    answer_followup,
    interpret_clarifying_card
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
    set_reading_note,
    set_reading_resonance,
    get_diary_stats,
    get_usage,
    consume_quota,
    touch_user,
    get_bot_stats
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

storage = MemoryStorage()
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
        "Остаться на нынешней работе или принять оффер?",
        "Снимать квартиру дальше или брать ипотеку?",
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
        "Назови оба варианта: расклад сравнивает их между собой."
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


def get_settings_text(current: bool | None) -> str:
    return (
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        "Использовать перевёрнутые карты в новых раскладах?\n\n"
        f"Текущий режим: <b>{describe_reversed_preference(current)}</b>.\n\n"
        "Если выбрать «Всегда да/нет», в следующий раз "
        "спрашивать не буду — можно поменять здесь в любой момент."
    )

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "🔮 <b>MY PERSONAL TAROT</b>\n\n"
        "Не предсказываю будущее.\n"
        "Помогаю посмотреть на ситуацию с другой стороны.\n\n"
        "Выбери, что хочешь сделать:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

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

    await state.clear()
    await state.update_data(spread_name=spread_name)

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
        await state.set_state(TarotStates.waiting_for_question)

        await safe_edit(callback,
            get_question_prompt_text(
                spread_name,
                describe_saved_preference_note(saved_preference)
            ),
            parse_mode="HTML"
        )

    await callback.answer()

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

    await safe_edit(
        callback,
        get_question_prompt_text(spread_name, note)
    )

    await callback.answer()

@dp.callback_query(F.data == "settings")
async def settings_menu(callback: CallbackQuery):
    current = await asyncio.to_thread(
        get_reversed_cards_preference,
        callback.from_user.id
    )

    await safe_edit(callback,
        get_settings_text(current),
        parse_mode="HTML",
        reply_markup=settings_keyboard(current)
    )

    await callback.answer()

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

    await safe_edit(callback,
        get_settings_text(value),
        parse_mode="HTML",
        reply_markup=settings_keyboard(value)
    )

    await callback.answer("Сохранено")


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
        "• дневник: заметки и отметки\n"
        "• изображения карт\n\n"
        "Таро здесь используется как инструмент "
        "символического анализа и рефлексии.\n\n"
        "Расклады и заметки хранятся, чтобы их можно было "
        "открыть позже в истории. Владельцу бота видно, "
        "сколько раскладов сделано, но не их содержание.\n\n"
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
        "для «Выбора» назови оба варианта, для «Отношений» — "
        "о ком речь, для одной карты хватит короткого фокуса. "
        "Примеры бот показывает на экране вопроса.\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/new — новый расклад\n"
        "/card Башня — значение любой карты\n"
        "/history — история раскладов\n"
        "/stats — дневник и статистика\n"
        "/about — о боте\n"
        "/help — помощь\n"
        "/reset — сбросить текущий расклад\n\n"
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
        "AI интерпретирует их в контексте твоего вопроса.\n\n"
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
    question = message.text

    if not question:
        await message.answer(
            "🔮 Пожалуйста, напиши свой вопрос текстом."
        )
        return

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

        await message.answer(
            "🔮 Не понял, какой расклад делать. Выбери его заново:",
            reply_markup=spread_keyboard()
        )
        return

    denied = await take_quota(
        message.from_user.id,
        is_reading=True
    )

    if denied:
        await state.set_state(TarotStates.choosing_spread)

        await message.answer(
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
        await message.answer(
            "🔮 <b>РАСКЛАД СОЗДАЁТСЯ</b>\n\n"
            "Выбираю карты и собираю их\n"
            "в контексте твоего вопроса…",
            parse_mode="HTML"
        )

        spread = make_spread(
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
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=(
                    f"🔮 <b>{html.escape(SPREADS[spread_name]['name'])}</b>\n\n"
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

        await message.answer(
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

        async with typing(message.chat.id):
            interpretation = await asyncio.to_thread(
                interpret_tarot,
                question=question,
                spread_name=spread_name,
                spread=spread
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

        await message.answer(
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

    try:
        await asyncio.to_thread(
            save_reading,
            user_id=message.from_user.id,
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
            message.answer,
            interpretation,
            prefix="🔮 <b>Интерпретация расклада</b>\n\n",
            suffix=FOLLOWUP_HINT
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
        history=[]
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

        async with typing(chat_id_of(callback)):
            interpretation = await asyncio.to_thread(
                interpret_tarot,
                question=question,
                spread_name=spread_name,
                spread=spread
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
    try:
        await asyncio.to_thread(
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
        suffix=FOLLOWUP_HINT
    )

    await state.set_state(TarotStates.follow_up)

    await state.update_data(
        interpretation=interpretation,
        history=[]
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

    data = await state.get_data()

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
        async with typing(message.chat.id):
            answer = await asyncio.to_thread(
                answer_followup,
                base_question=base_question,
                spread_name=spread_name,
                spread=spread,
                base_interpretation=base_interpretation,
                history=history,
                follow_up_question=follow_up_question
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
        answer
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

        async with typing(chat_id_of(callback)):
            interpretation = await asyncio.to_thread(
                interpret_clarifying_card,
                base_question,
                spread_name,
                base_spread,
                base_interpretation,
                new_item
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


async def main():
    init_database()
    await dp.start_polling(bot)

