from bot.suggest import suggest_spread
from bot.journal import month_stats, format_month_summary, build_export_text
from bot.storage import _key
from aiogram.fsm.storage.base import StorageKey


def test_suggest_spread():
    assert suggest_spread("мне выбрать магу или работу?")[0] == "choice"
    assert suggest_spread("стоит ли мне ему писать?")[0] == "relationship"
    assert suggest_spread("что мне делать со своей жизнью?")[0] == "seven_cards"
    assert suggest_spread("что меня ждёт завтра")[0] == "three_cards"
    assert suggest_spread("на что обратить внимание?")[0] == "one_card"


def test_month_summary_hides_questions():
    rows = [
        {"spread_name": "one_card", "spread": '[{"card": {"name": "Шут"}}]',
         "resonance": "yes", "note": "заметка", "question": "секретный вопрос"},
        {"spread_name": "one_card", "spread": '[{"card": {"name": "Шут"}}]',
         "resonance": None, "note": None, "question": "ещё вопрос"},
    ]
    stats = month_stats(rows)
    text = format_month_summary(stats, 2026, 10)
    assert "Шут ×2" in text and "Отозвалось: 1" in text
    assert "секретный" not in text


def test_export_text():
    rows = [{"created_at": "2026-10-01T10:00:00", "spread_name": "one_card",
             "question": "q", "spread": "[]", "interpretation": "i",
             "note": None, "resonance": None}]
    assert "Всего раскладов: 1" in build_export_text(rows)


def test_storage_key_is_stable():
    key = StorageKey(bot_id=1, chat_id=5, user_id=5)
    assert _key(key) == _key(StorageKey(bot_id=1, chat_id=5, user_id=5))
