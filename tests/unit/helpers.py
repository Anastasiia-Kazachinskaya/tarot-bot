"""Подделки Telegram и модели для тестов — без сети и без настоящего бота."""

import asyncio
from contextlib import contextmanager
from datetime import datetime as real_datetime
from pathlib import Path

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from bot import main as M
from bot import database as D

D.init_database()

SENT: list[tuple] = []


class FakeBot:
    id = 1

    async def send_message(self, chat_id, text, **kw):
        SENT.append(("msg", chat_id, text, kw.get("reply_markup")))

    async def send_photo(self, chat_id, photo=None, caption=None, **kw):
        SENT.append(("photo", chat_id, caption, kw.get("reply_markup")))

    async def send_chat_action(self, **kw):
        pass

    async def set_message_reaction(self, chat_id, message_id, reaction=None, **kw):
        SENT.append(("react", chat_id, message_id, reaction[0].emoji))

    async def set_my_commands(self, commands, **kw):
        SENT.append(("commands", len(commands)))


class User:
    def __init__(self, user_id):
        self.id = user_id
        self.username = "tester"
        self.first_name = "Test"


class Chat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    _next_id = 1000

    def __init__(self, text, user_id):
        FakeMessage._next_id += 1
        self.message_id = FakeMessage._next_id
        self.text = text
        self.from_user = User(user_id)
        self.chat = Chat(user_id)

    async def answer(self, text, **kw):
        SENT.append(("answer", self.chat.id, text, kw.get("reply_markup")))

    async def answer_document(self, document, caption=None, **kw):
        SENT.append(("document", self.chat.id, document, caption))

    async def answer_photo(self, photo, caption=None, **kw):
        SENT.append(("photo", self.chat.id, caption, kw.get("reply_markup")))

    async def edit_text(self, text, **kw):
        SENT.append(("edit", self.chat.id, text, kw.get("reply_markup")))

    async def edit_reply_markup(self, **kw):
        SENT.append(("edit_markup", self.chat.id))


class FakeCallback:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = User(user_id)
        self.message = FakeMessage("", user_id)

    async def answer(self, text=None, **kw):
        SENT.append(("toast", self.from_user.id, text))


def ctx(user_id: int) -> FSMContext:
    return FSMContext(
        storage=M.storage,
        key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    )


def run(coro):
    return asyncio.run(coro)


def texts(kind=None, user_id=None) -> list[str]:
    return [
        item[2] for item in SENT
        if (kind is None or item[0] == kind)
        and (user_id is None or item[1] == user_id)
        and isinstance(item[2], str)
    ]


def last_text(user_id, kinds=("msg", "answer", "edit")) -> str:
    for item in reversed(SENT):
        if item[0] in kinds and item[1] == user_id:
            return item[2]
    raise AssertionError(f"нет сообщений для {user_id}")


_png = Path(__file__).with_name("_fake.png")


def _fake_render(*args, **kwargs):
    _png.write_bytes(b"png")
    return _png


@contextmanager
def patched(**replacements):
    """Подменяет атрибуты bot.main на время теста."""

    old = {name: getattr(M, name) for name in replacements}

    for name, value in replacements.items():
        setattr(M, name, value)

    try:
        yield
    finally:
        for name, value in old.items():
            setattr(M, name, value)


def fake_interpret(**kw):
    return f"Интерпретация: {kw['question']} | gender={kw.get('gender')}"


def default_fakes():
    return dict(
        bot=FakeBot(),
        render_spread=_fake_render,
        render_single_card=_fake_render,
        interpret_tarot=fake_interpret,
        answer_followup=lambda **kw: "ответ на уточнение",
        daily_card_message=lambda card, gender: f"Мысль дня: {card['name']}",
        month_themes=lambda questions, gender: "Темы: работа и отношения.",
    )


def fixed_now(**fields):
    """Класс datetime, у которого now() возвращает нужный час/день."""

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now().replace(**fields)

    return FixedDatetime
