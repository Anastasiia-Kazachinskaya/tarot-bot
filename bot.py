import asyncio
import json
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from keyboards import (
    spread_keyboard,
    reversed_keyboard,
    after_reading_keyboard,
    history_keyboard,
    reading_keyboard
)
from states import TarotStates
from tarot.deck import make_spread
from tarot.renderer import render_spread
from llm.cloudru import interpret_tarot
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


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.set_state(TarotStates.choosing_spread)

    await message.answer(
        "🔮 Привет.\n\n"
        "Я твой личный AI-таролог.\n\n"
        "Выбери расклад:",
        reply_markup=spread_keyboard()
    )


@dp.callback_query(
    TarotStates.choosing_spread,
    F.data.startswith("spread:")
)
async def choose_spread(
    callback: CallbackQuery,
    state: FSMContext
):
    spread_name = callback.data.split(":")[1]

    await state.update_data(
        spread_name=spread_name
    )

    await state.set_state(
        TarotStates.choosing_reversed
    )

    await callback.message.edit_text(
        "🔮 Отлично.\n\n"
        "Использовать перевёрнутые карты?",
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

    await state.update_data(
        reversed_cards=use_reversed
    )

    await state.set_state(
        TarotStates.waiting_for_question
    )

    await callback.message.edit_text(
        "🔮 Всё готово.\n\n"
        "Теперь задай свой вопрос."
    )

    await callback.answer()


def format_spread(spread: list[dict]) -> str:
    """
    Формирует красивое отображение вытянутых карт
    для пользователя Telegram.
    """

    lines = [
        "🔮 **Твой расклад**",
        ""
    ]

    for index, item in enumerate(spread, start=1):
        position = item["position"]
        card = item["card"]

        orientation = (
            "прямая"
            if not card["reversed"]
            else "перевёрнутая"
        )

        lines.append(
            f"**{index}. {position}**"
        )

        lines.append(
            f"🃏 {card['name']} — {orientation}"
        )

        lines.append("")

    return "\n".join(lines)

@dp.message(Command("reset"))
async def reset(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(TarotStates.choosing_spread)

    await message.answer(
        "🔮 Начнём новый расклад.\n\n"
        "Выбери расклад:",
        reply_markup=spread_keyboard()
    )
@dp.callback_query(F.data == "new_reading")
async def new_reading(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.clear()
    await state.set_state(TarotStates.choosing_spread)

    await callback.message.answer(
        "🔮 Новый расклад.\n\n"
        "Выбери расклад:",
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

    total_start = time.perf_counter()

    data = await state.get_data()

    spread_name = data["spread_name"]
    use_reversed = data["reversed_cards"]

    # -------------------------
    # 1. Вытягиваем карты
    # -------------------------

    try:
        draw_start = time.perf_counter()

        spread = make_spread(
            spread_name,
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

        caption = (
            f"🔮 **{SPREADS[spread_name]['name']}**\n\n"
            + "\n".join(caption_lines)
        )

        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=caption,
                parse_mode="Markdown"
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
            "🔮 Карты уже вытянуты выше, но мне не удалось "
            "получить их интерпретацию.\n\n"
            "Попробуй повторить запрос чуть позже."
        )

        await state.clear()
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

        await message.answer(
            f"🔮 **Интерпретация расклада**\n\n"
            f"{interpretation}",
            parse_mode="Markdown",
            reply_markup=after_reading_keyboard()
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

    finally:
        await state.clear()

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
            "📖 **История пока пуста.**\n\n"
            "Сделай первый расклад, и он появится здесь.",
            parse_mode="Markdown"
        )

        await callback.answer()
        return

    await callback.message.edit_text(
        "📖 **История раскладов**\n\n"
        "Выбери расклад, чтобы открыть его:",
        parse_mode="Markdown",
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
    question = reading["question"]
    interpretation = reading["interpretation"]

    text = (
        "🔮 **Расклад из истории**\n\n"
        f"📅 {created_at[:10]}\n\n"
        f"❓ **Вопрос:**\n"
        f"{question}\n\n"
        f"{format_spread(spread)}\n\n"
        "🔮 **Интерпретация**\n\n"
        f"{interpretation}"
    )

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=reading_keyboard()
    )

    await callback.answer()


async def main():
    init_database()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

