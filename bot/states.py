from aiogram.fsm.state import State, StatesGroup


class TarotStates(StatesGroup):
    choosing_spread = State()
    choosing_reversed = State()
    waiting_for_question = State()
    processing = State()
    follow_up = State()
    waiting_for_note = State()
    waiting_for_option_a = State()
    waiting_for_option_b = State()
    confirming_repeat = State()
    waiting_for_live_card = State()
    waiting_for_suggest_question = State()