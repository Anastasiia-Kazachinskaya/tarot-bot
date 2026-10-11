"""
FSM-хранилище aiogram поверх SQLite.

По умолчанию aiogram держит состояние диалога в памяти (MemoryStorage),
и после каждого перезапуска бота — например, при деплое — пользователь
теряет текущий расклад: уточняющие вопросы перестают работать. Здесь
состояние и данные лежат в той же базе, что и расклады.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from .database import get_connection


def _key(key: StorageKey) -> str:
    return ":".join(
        str(part)
        for part in (
            key.bot_id,
            key.chat_id,
            key.thread_id or 0,
            key.user_id,
            key.business_connection_id or "",
            key.destiny,
        )
    )


def init_fsm_table() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm (
                key TEXT PRIMARY KEY,
                state TEXT,
                data TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.commit()


def _read(key: str) -> tuple[str | None, dict[str, Any]]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT state, data FROM fsm WHERE key = ?",
            (key,)
        ).fetchone()

    if row is None:
        return None, {}

    try:
        data = json.loads(row["data"] or "{}")
    except ValueError:
        data = {}

    return row["state"], data


def _write_state(key: str, state: str | None) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO fsm (key, state) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET state = excluded.state
            """,
            (key, state)
        )
        connection.commit()


def _write_data(key: str, data: Mapping[str, Any]) -> None:
    payload = json.dumps(dict(data), ensure_ascii=False)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO fsm (key, data) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data
            """,
            (key, payload)
        )
        connection.commit()


def delete_user_fsm(user_id: int) -> None:
    """Для /delete_my_data: стираем и незаконченный диалог."""

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM fsm WHERE key LIKE ?",
            (f"%:{user_id}:%",)
        )
        connection.commit()


class SQLiteStorage(BaseStorage):
    """Состояние и данные FSM в таблице fsm. Данные — JSON."""

    def __init__(self) -> None:
        init_fsm_table()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await asyncio.to_thread(_write_state, _key(key), value)

    async def get_state(self, key: StorageKey) -> str | None:
        state, _ = await asyncio.to_thread(_read, _key(key))
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            raise TypeError(
                f"Data must be a dict-like object, got {type(data).__name__}"
            )

        await asyncio.to_thread(_write_data, _key(key), data)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, data = await asyncio.to_thread(_read, _key(key))
        return data

    async def close(self) -> None:
        pass
