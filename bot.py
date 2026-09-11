import asyncio
import json
import os
import time
import html
import re
import traceback

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ErrorEvent, Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from keyboards import (
    main_menu_keyboard,
    spread_keyboard,
    reversed_keyboard,
    after_reading_keyboard,
    history_keyboard,
    reading_keyboard,
    llm_error_keyboard
)

from states import TarotStates
from tarot.deck import make_spread, draw_single_card
from tarot.renderer import render_spread, render_single_card
from llm.cloudru import (
    interpret_tarot,
    answer_followup,
    interpret_clarifying_card
)
from tarot.spreads import SPREADS

from database import (
    init_database,
    save_reading,
    get_user_readings,
    get_reading
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")


bot = Bot(token=BOT_TOKEN)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)


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

    await callback.message.edit_text(
        "🔮 <b>MY PERSONAL TAROT</b>\n\n"
        "Не предсказываю будущее.\n"
        "Помогаю посмотреть на ситуацию\n"
        "с другой стороны.\n\n"
        "Выбери, что хочешь сделать:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("spread:"))
async def choose_spread(callback: CallbackQuery, state: FSMContext):
    spread_name = callback.data.split(":")[1]

    if spread_name not in SPREADS:
        await callback.answer("Неизвестный расклад.")
        return

    await state.clear()
    await state.update_data(spread_name=spread_name)
    await state.set_state(TarotStates.choosing_reversed)

    await callback.message.edit_text(
        get_spread_setup_text(spread_name),
        parse_mode="HTML",
        reply_markup=reversed_keyboard()
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
    spread_name = data["spread_name"]

    await state.update_data(
        reversed_cards=use_reversed
    )

    await state.set_state(
        TarotStates.waiting_for_question
    )

    spread_title = html.escape(
        SPREADS[spread_name]["name"].upper()
    )

    await callback.message.edit_text(
        f"🔮 <b>{spread_title}</b>\n\n"
        "Теперь задай свой вопрос.\n\n"
        "Чем конкретнее ситуация, тем полезнее "
        "получится интерпретация.\n\n"
        "<b>Например:</b>\n"
        "«Какой подход к поиску работы сейчас "
        "будет для меня наиболее продуктивным?»",
        parse_mode="HTML"
    )

    await callback.answer()


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

def split_text(text: str, limit: int = 3900) -> list[str]:
    """
    Разбивает длинный текст на части, стараясь
    не разрывать абзацы.
    """

    paragraphs = text.split("\n\n")

    chunks = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        # Если отдельный абзац сам длиннее лимита,
        # режем его дополнительно.
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""

            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]

        if not paragraph:
            continue

        candidate = (
            f"{current}\n\n{paragraph}"
            if current
            else paragraph
        )

        if len(candidate) > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def llm_to_html(text: str) -> str:
    """
    Преобразует ответ LLM в безопасный Telegram HTML.
    """

    text = html.escape(text)

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


FOLLOWUP_HINT = (
    "\n\n💬 Можешь задать уточняющий вопрос по раскладу "
    "— просто напиши его. Или вытяни ещё одну уточняющую "
    "карту кнопкой ниже."
)


async def send_llm_response(
    answer,
    text: str,
    prefix: str = "",
    suffix: str = ""
) -> list[str]:
    """
    Отправляет длинный ответ LLM, разбивая его на части
    и добавляя префикс/суффикс только к первой/последней части.
    Используется и для основной интерпретации, и для повтора,
    и для уточняющих ответов.
    """

    chunks = split_text(text, limit=3900)

    for index, chunk in enumerate(chunks):
        is_first = index == 0
        is_last = index == len(chunks) - 1

        formatted_chunk = llm_to_html(chunk)

        if is_first and prefix:
            formatted_chunk = prefix + formatted_chunk

        if is_last and suffix:
            formatted_chunk = formatted_chunk + suffix

        await answer(
            formatted_chunk,
            parse_mode="HTML",
            reply_markup=(
                after_reading_keyboard()
                if is_last
                else None
            )
        )

    return chunks

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
        "• изображения карт\n\n"
        "Таро здесь используется как инструмент "
        "символического анализа и рефлексии.\n\n"
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
        "❌ «Что меня ждёт?»\n\n"
        "✅ «Какой подход к поиску работы сейчас "
        "будет для меня наиболее продуктивным?»\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/new — новый расклад\n"
        "/history — история\n"
        "/about — о боте\n"
        "/help — помощь\n"
        "/reset — сбросить текущий расклад",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "about")
async def about_callback(callback: CallbackQuery):
    await callback.message.edit_text(
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

@dp.callback_query(F.data == "new_reading")
async def new_reading(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TarotStates.choosing_spread)

    await callback.message.edit_text(
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

    spread_name = data["spread_name"]
    use_reversed = data["reversed_cards"]

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
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (draw)\n"
            f"Error: {error}\n"
            "===================================\n"
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

        interpretation = await asyncio.to_thread(
            interpret_tarot,
            question=question,
            spread_name=spread_name,
            spread=spread
        )

        llm_time = time.perf_counter() - llm_start

    except Exception as error:
        total_time = time.perf_counter() - total_start

        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            f"Cards: {len(spread)}\n"
            f"Draw time: {draw_time:.3f}s\n"
            f"Total time: {total_time:.3f}s\n"
            "Status: ERROR (LLM)\n"
            f"Error: {error}\n"
            "===================================\n"
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
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (DATABASE)\n"
            f"Error: {error}\n"
            "===================================\n"
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

        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            f"Cards: {len(spread)}\n"
            f"Draw time: {draw_time:.3f}s\n"
            f"LLM time: {llm_time:.3f}s\n"
            f"Telegram time: {telegram_time:.3f}s\n"
            f"Total time: {total_time:.3f}s\n"
            f"Response length: {len(interpretation)} chars\n"
            f"Response chunks: {len(chunks)}\n"
            "Status: SUCCESS\n"
            "===================================\n"
        )

    except Exception as error:
        total_time = time.perf_counter() - total_start

        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            f"Cards: {len(spread)}\n"
            f"Draw time: {draw_time:.3f}s\n"
            f"LLM time: {llm_time:.3f}s\n"
            f"Total time: {total_time:.3f}s\n"
            "Status: ERROR (TELEGRAM)\n"
            f"Error: {error}\n"
            "===================================\n"
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

    await callback.answer("Повторяю интерпретацию…")

    try:
        await callback.message.answer(
            "🔄 <b>ПОВТОРНАЯ ПОПЫТКА</b>\n\n"
            "Карты остаются прежними.\n"
            "Пробую получить интерпретацию ещё раз…",
            parse_mode="HTML"
        )

        llm_start = time.perf_counter()

        interpretation = await asyncio.to_thread(
            interpret_tarot,
            question=question,
            spread_name=spread_name,
            spread=spread
        )

        llm_time = time.perf_counter() - llm_start

    except Exception as error:
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            f"Cards: {len(spread)}\n"
            f"LLM time: {time.perf_counter() - llm_start:.3f}s\n"
            "Status: ERROR (RETRY)\n"
            f"Error: {error}\n"
            "===================================\n"
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
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Question: {question}\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (DATABASE RETRY)\n"
            f"Error: {error}\n"
            "===================================\n"
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

    print(
        "\n"
        "========== TAROT METRICS ==========\n"
        f"Question: {question}\n"
        f"Spread: {spread_name}\n"
        f"Cards: {len(spread)}\n"
        f"LLM time: {llm_time:.3f}s\n"
        f"Response length: {len(interpretation)} chars\n"
        f"Response chunks: {len(chunks)}\n"
        "Status: RETRY SUCCESS\n"
        "===================================\n"
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

    spread_name = data["spread_name"]
    spread = data["spread"]
    base_question = data["question"]
    base_interpretation = data["interpretation"]
    history = data.get("history", [])

    try:
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
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Follow-up question: {follow_up_question}\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (FOLLOWUP)\n"
            f"Error: {error}\n"
            "===================================\n"
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

    chunks = split_text(answer, limit=3900)

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1

        await message.answer(
            llm_to_html(chunk),
            parse_mode="HTML",
            reply_markup=(
                after_reading_keyboard()
                if is_last
                else None
            )
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
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (CLARIFYING DRAW)\n"
            f"Error: {error}\n"
            "===================================\n"
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

        interpretation = await asyncio.to_thread(
            interpret_clarifying_card,
            base_question,
            spread_name,
            spread,
            base_interpretation,
            new_item
        )

    except Exception as error:
        print(
            "\n"
            "========== TAROT METRICS ==========\n"
            f"Spread: {spread_name}\n"
            "Status: ERROR (CLARIFYING INTERPRETATION)\n"
            f"Error: {error}\n"
            "===================================\n"
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

    chunks = split_text(interpretation, limit=3900)

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1

        await callback.message.answer(
            llm_to_html(chunk),
            parse_mode="HTML",
            reply_markup=(
                after_reading_keyboard()
                if is_last
                else None
            )
        )

@dp.callback_query(F.data == "history")
async def show_history(
    callback: CallbackQuery
):
    readings = await asyncio.to_thread(
        get_user_readings,
        user_id=callback.from_user.id
    )

    if not readings:
        await callback.message.edit_text(
            "📖 <b>История пока пуста.</b>\n\n"
            "Сделай первый расклад, и он появится здесь.",
            parse_mode="HTML",
            reply_markup=history_keyboard([])
        )

        await callback.answer()
        return

    await callback.message.edit_text(
            "📖 <b>История раскладов</b>\n\n"
            "Выбери расклад, чтобы открыть его:",
            parse_mode="HTML",
            reply_markup=history_keyboard(readings)
        )
    await callback.answer()

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

    header = (
        "🔮 <b>Расклад из истории</b>\n\n"
        f"📅 {created_at[:10]}\n\n"
        f"❓ <b>Вопрос:</b>\n"
        f"{question}\n\n"
        f"{format_spread(spread)}\n\n"
        "🔮 <b>Интерпретация</b>"
    )

    chunks = split_text(interpretation, limit=3900)

    await callback.message.edit_text(
        header,
        parse_mode="HTML",
        reply_markup=(
            reading_keyboard()
            if not chunks
            else None
        )
    )

    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1

        await callback.message.answer(
            llm_to_html(chunk),
            parse_mode="HTML",
            reply_markup=(
                reading_keyboard()
                if is_last
                else None
            )
        )

    await callback.answer()


@dp.errors()
async def handle_error(event: ErrorEvent):
    """
    Ловит любое исключение, не пойманное внутри хендлеров,
    чтобы пользователь получал понятный ответ вместо тишины.
    """

    print(
        "\n"
        "========== UNHANDLED ERROR ==========\n"
        f"Error: {event.exception!r}\n"
        "======================================\n"
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


if __name__ == "__main__":
    asyncio.run(main())

