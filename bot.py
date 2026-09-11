
import asyncio
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from dotenv import load_dotenv

from keyboards import spread_keyboard, reversed_keyboard
from states import TarotStates
from tarot.deck import make_spread
from llm.cloudru import interpret_tarot


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

    try:
        draw_start = time.perf_counter()

        spread = make_spread(
            spread_name,
            reversed_cards=use_reversed
        )

        draw_time = time.perf_counter() - draw_start

        # Сначала показываем пользователю сами карты.
        await message.answer(
            format_spread(spread),
            parse_mode="Markdown"
        )

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

    try:
        gemini_start = time.perf_counter()

        interpretation = await asyncio.to_thread(
        interpret_tarot,
        question=question,
        spread_name=spread_name,
        spread=spread
    )

        gemini_time = time.perf_counter() - gemini_start

        telegram_start = time.perf_counter()

        await message.answer(
            f"🔮 **Интерпретация расклада**\n\n"
            f"{interpretation}",
            parse_mode="Markdown"
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
            f"Gemini time: {gemini_time:.3f}s\n"
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
            f"Total time: {total_time:.3f}s\n"
            "Status: ERROR\n"
            f"Error: {error}\n"
            "===================================\n"
        )

        await message.answer(
            "🔮 Карты уже вытянуты выше, но мне не удалось "
            "получить их интерпретацию.\n\n"
            "Попробуй повторить запрос чуть позже."
        )

    finally:
        await state.clear()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

