"""Сценарии бота целиком: от сообщения пользователя до ответа и записи в базу."""

import sqlite3

import helpers as H
from helpers import M, D, FakeMessage, FakeCallback, ctx, run, patched, default_fakes


def test_onboarding_sets_profile():
    uid = 101
    st = ctx(uid)

    with patched(**default_fakes()):
        run(M.start(FakeMessage("/start", uid), st))
        assert "1/3" in H.last_text(uid)

        run(M.onboard_gender(FakeCallback("onboard:gender:f", uid)))
        assert "2/3" in H.last_text(uid)

        run(M.onboard_reversed(FakeCallback("onboard:reversed:no", uid)))
        assert "3/3" in H.last_text(uid)

        run(M.onboard_daily(FakeCallback("onboard:daily:9", uid)))
        assert "09:00" in H.last_text(uid)

        run(M.start(FakeMessage("/start", uid), st))
        assert "MY PERSONAL TAROT" in H.last_text(uid)

    profile = D.get_profile(uid)
    assert profile["gender"] == "f"
    assert profile["onboarded"] is True
    assert profile["daily_hour"] == 9
    assert D.get_reversed_cards_preference(uid) is False


def test_settings_toggles():
    uid = 102

    with patched(**default_fakes()):
        run(M.settings_command(FakeMessage("/settings", uid), ctx(uid)))
        assert "Карта дня" in H.last_text(uid)

        run(M.update_daily_hour(FakeCallback("settings:daily_hour:8", uid)))
        assert D.get_profile(uid)["daily_hour"] == 8

        run(M.update_daily_hour(FakeCallback("settings:daily_hour:off", uid)))
        assert D.get_profile(uid)["daily_hour"] is None

        run(M.toggle_checkins(FakeCallback("settings:checkins:toggle", uid)))
        assert D.get_profile(uid)["checkins_enabled"] is False

        run(M.toggle_monthly(FakeCallback("settings:monthly:toggle", uid)))
        assert D.get_profile(uid)["monthly_enabled"] is False


def test_choice_asks_options_separately():
    uid = 103
    st = ctx(uid)
    D.set_user_gender(uid, "f")

    async def scenario():
        await st.update_data(spread_name="choice", reversed_cards=False)
        await st.set_state(M.TarotStates.waiting_for_question)

        await M.get_question(FakeMessage("Позвали на новую работу", uid), st)
        assert await st.get_state() == M.TarotStates.waiting_for_option_a.state

        await M.get_option_a(FakeMessage("остаться", uid), st)
        await M.get_option_b(FakeMessage("уйти", uid), st)

        assert await st.get_state() == M.TarotStates.follow_up.state

    with patched(**default_fakes()):
        run(scenario())

    answer = [t for t in H.texts("msg", uid) if "Интерпретация" in t][-1]
    assert "Вариант А: остаться" in answer
    assert "Вариант Б: уйти" in answer
    assert "gender=f" in answer
    assert any(item[0] == "react" and item[1] == uid for item in H.SENT)
    assert D.count_user_readings(uid) == 1


def test_repeat_question_is_noticed():
    uid = 104

    async def ask(text):
        st = ctx(uid)
        await st.clear()
        await st.update_data(spread_name="one_card", reversed_cards=False)
        await st.set_state(M.TarotStates.waiting_for_question)
        await M.get_question(FakeMessage(text, uid), st)
        return st

    with patched(**default_fakes()):
        run(ask("На что обратить внимание?"))
        st = run(ask("на что  обратить внимание"))
        assert run(st.get_state()) == M.TarotStates.confirming_repeat.state
        assert "уже был сегодня" in H.last_text(uid)

        run(M.repeat_go(FakeCallback("repeat:go", uid), st))

    assert D.count_user_readings(uid) == 2


def test_note_button_after_reading():
    uid = 105
    st = ctx(uid)

    async def scenario():
        await st.update_data(spread_name="one_card", reversed_cards=False)
        await st.set_state(M.TarotStates.waiting_for_question)
        await M.get_question(FakeMessage("Что важно сегодня?", uid), st)

    with patched(**default_fakes()):
        run(scenario())

    keyboard = [item[3] for item in H.SENT if item[0] == "msg" and item[1] == uid][-1]
    first_button = keyboard.inline_keyboard[0][0]
    assert first_button.text == "📝 Записать мысль"
    assert first_button.callback_data.startswith("note:")


def test_followup_dedupe_and_crisis():
    uid = 106
    st = ctx(uid)
    calls = []

    def fake_followup(**kw):
        calls.append(kw["follow_up_question"])
        return "ответ"

    async def scenario():
        await st.update_data(
            spread_name="one_card",
            spread=[{"position": "p", "card": {"name": "Шут"}}],
            question="q",
            interpretation="i",
            history=[],
        )
        await st.set_state(M.TarotStates.follow_up)

        await M.get_followup(FakeMessage("мне одиноко", uid), st)
        await M.get_followup(FakeMessage("Мне  одиноко", uid), st)
        assert len(calls) == 1
        assert "выше" in H.last_text(uid)

        await M.get_followup(FakeMessage("я не хочу жить", uid), st)
        assert len(calls) == 1
        assert "112" in H.last_text(uid)

    fakes = default_fakes()
    fakes["answer_followup"] = fake_followup

    with patched(**fakes):
        run(scenario())


def test_state_survives_restart():
    uid = 107
    st = ctx(uid)

    run(st.set_state(M.TarotStates.follow_up))
    run(st.update_data(question="вопрос", history=[{"question": "a", "answer": "b"}]))

    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from bot.storage import SQLiteStorage

    fresh = FSMContext(
        storage=SQLiteStorage(),
        key=StorageKey(bot_id=1, chat_id=uid, user_id=uid),
    )

    assert run(fresh.get_state()) == M.TarotStates.follow_up.state
    assert run(fresh.get_data())["history"][0]["answer"] == "b"


def test_daily_card_button_gives_same_card():
    uid = 108
    generated = []

    fakes = default_fakes()
    fakes["daily_card_message"] = lambda card, gender: (
        generated.append(card["name"]) or f"мысль {card['name']}"
    )

    with patched(**fakes):
        run(M.daily_now(FakeCallback("daily_now", uid)))
        run(M.daily_now(FakeCallback("daily_now", uid)))

    photos = [item[2] for item in H.SENT if item[0] == "photo" and item[1] == uid]
    assert len(photos) == 2 and photos[0] == photos[1]
    assert len(generated) == 1


def test_scheduler_daily_and_checkin():
    uid = 109
    D.set_daily_hour(uid, 9)
    reading_id = D.save_reading(uid, "вопрос", "one_card", [], "интерпретация")

    with sqlite3.connect(D.DATABASE_FILE) as connection:
        connection.execute(
            "UPDATE readings SET checkin_due = '2000-01-01T00:00:00' WHERE id = ?",
            (reading_id,)
        )

    fakes = default_fakes()

    with patched(datetime=H.fixed_now(hour=9, minute=5), **fakes):
        run(M.run_schedule_tick())
        run(M.run_schedule_tick())

    photos = [item for item in H.SENT if item[0] == "photo" and item[1] == uid]
    assert len(photos) == 1

    with patched(datetime=H.fixed_now(hour=12), **fakes):
        run(M.run_schedule_tick())
        run(M.run_schedule_tick())

    checkins = [t for t in H.texts("msg", uid) if "Как сложилось" in t]
    assert len(checkins) == 1


def test_monthly_summary_on_first_day():
    uid = 110

    for question in ("работа", "отношения", "переезд"):
        D.save_reading(uid, question, "three_cards", [
            {"position": "p", "card": {"name": "Башня", "reversed": False}}
        ], "i")

    from datetime import date
    from bot.journal import month_bounds, previous_month

    today = date.today()
    year, month = today.year, today.month
    next_first = date(year + (month == 12), month % 12 + 1, 1)

    fakes = default_fakes()

    with patched(
        datetime=H.fixed_now(year=next_first.year, month=next_first.month, day=1, hour=11),
        **fakes
    ):
        run(M.run_schedule_tick())
        run(M.run_schedule_tick())

    summaries = [t for t in H.texts("msg", uid) if "Итоги месяца" in t]
    assert len(summaries) == 1
    assert "Раскладов: <b>3</b>" in summaries[0]
    assert "Башня ×3" in summaries[0]
    assert "Темы" in summaries[0]
    assert previous_month(next_first) == (year, month)
    assert month_bounds(year, month)[0].endswith("-01")


def test_export_sends_file():
    uid = 111
    D.save_reading(uid, "первый вопрос", "one_card", [], "интерпретация")

    with patched(**default_fakes()):
        run(M.export_command(FakeMessage("/export", uid), ctx(uid)))

    document = [item for item in H.SENT if item[0] == "document" and item[1] == uid][-1][2]
    assert "первый вопрос" in document.data.decode("utf-8")


def test_live_deck_reading():
    uid = 112
    st = ctx(uid)

    async def scenario():
        await M.live_start(FakeCallback("live:start", uid), st)
        await M.choose_spread(FakeCallback("spread:three_cards", uid), st)
        await M.get_question(FakeMessage("Как пройдёт неделя?", uid), st)
        assert "Карта 1 из 3" in H.last_text(uid)

        await M.get_live_card(FakeMessage("какая-то ерунда", uid), st)
        assert "Не нашла" in H.last_text(uid)

        await M.get_live_card(FakeMessage("Башня", uid), st)
        await M.get_live_card(FakeMessage("Башня", uid), st)
        assert "уже есть" in H.last_text(uid)

        await M.get_live_card(FakeMessage("10 мечей перевёрнутая", uid), st)
        await M.get_live_card(FakeMessage("дама кубков", uid), st)

    with patched(**default_fakes()):
        run(scenario())

    reading = D.get_user_readings(uid)[0]
    full = D.get_reading(reading["id"], uid)
    import json
    cards = json.loads(full["spread"])
    assert [c["card"]["name"] for c in cards] == ["Башня", "Десятка Мечей", "Королева Кубков"]
    assert cards[1]["card"]["reversed"] is True
    assert any("твоя колода" in (item[2] or "") for item in H.SENT if item[0] == "photo")


def test_suggest_flow_reuses_question():
    uid = 113
    st = ctx(uid)
    D.set_reversed_cards_preference(uid, False)

    async def scenario():
        await M.suggest_start(FakeCallback("suggest:start", uid), st)
        await M.suggest_question(FakeMessage("стоит ли мне ему писать?", uid), st)
        assert "Отношения" in H.last_text(uid)

        await M.choose_spread(FakeCallback("spread:relationship", uid), st)

    with patched(**default_fakes()):
        run(scenario())

    readings = D.get_user_readings(uid)
    assert len(readings) == 1
    assert readings[0]["question"] == "стоит ли мне ему писать?"
    assert readings[0]["spread_name"] == "relationship"


def test_delete_my_data():
    uid = 114
    D.save_reading(uid, "q", "one_card", [], "i")
    D.set_daily_hour(uid, 10)
    st = ctx(uid)

    with patched(**default_fakes()):
        run(M.delete_my_data_command(FakeMessage("/delete_my_data", uid), st))
        run(M.delete_my_data_confirm(FakeCallback("delete_data:confirm", uid), st))

    assert D.count_user_readings(uid) == 0
    assert D.get_profile(uid)["daily_hour"] is None


def test_commands_registered_before_state_handlers():
    """Команды не должны перехватываться как ответ на вопрос расклада."""

    import inspect

    source = inspect.getsource(M)

    first_state_handler = source.index("@dp.message(TarotStates.")
    for command in ('Command("settings")', 'Command("export")',
                    'Command("month")', 'Command("delete_my_data")'):
        assert source.index(command) < first_state_handler, command

    last_callback = source.rindex("@dp.callback_query(")
    assert source.index("async def stale_button") > last_callback
