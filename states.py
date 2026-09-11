from aiogram.fsm.state import State, StatesGroup


class TarotStates(StatesGroup):
    choosing_spread = State()
    choosing_reversed = State()
    waiting_for_question = State()
    processing = State()
    follow_up = State()