import json
import sqlite3
from datetime import datetime
from pathlib import Path


DATABASE_FILE = Path(__file__).parent / "tarot.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                question TEXT NOT NULL,
                spread_name TEXT NOT NULL,
                spread TEXT NOT NULL,
                interpretation TEXT NOT NULL
            )
            """
        )

        connection.commit()


def save_reading(
    user_id: int,
    question: str,
    spread_name: str,
    spread: list[dict],
    interpretation: str
) -> int:

    created_at = datetime.now().isoformat(timespec="seconds")

    spread_json = json.dumps(
        spread,
        ensure_ascii=False
    )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO readings (
                user_id,
                created_at,
                question,
                spread_name,
                spread,
                interpretation
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                created_at,
                question,
                spread_name,
                spread_json,
                interpretation
            )
        )

        connection.commit()

        return cursor.lastrowid


def get_user_readings(
    user_id: int,
    limit: int = 10
) -> list[sqlite3.Row]:

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                id,
                created_at,
                question,
                spread_name
            FROM readings
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        )

        return cursor.fetchall()


def get_reading(
    reading_id: int,
    user_id: int
) -> sqlite3.Row | None:

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                id,
                created_at,
                question,
                spread_name,
                spread,
                interpretation
            FROM readings
            WHERE id = ?
              AND user_id = ?
            """,
            (reading_id, user_id)
        )

        return cursor.fetchone()