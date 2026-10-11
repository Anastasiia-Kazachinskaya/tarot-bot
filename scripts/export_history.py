"""
Выгружает историю раскладов из tarot.db в текстовые файлы —
по одному файлу на пользователя.

Использование (из корня репозитория):
    python3 -m scripts.export_history [папка_назначения]

По умолчанию файлы кладутся в ./exports/ (в .gitignore, наружу
не публикуется — в базе реальные вопросы живых людей).
"""

import sys
from pathlib import Path

from bot.database import get_connection
from bot.journal import format_reading


def safe_filename(user_id: int, username: str | None, first_name: str | None) -> str:
    label = username or first_name or ""
    label = "".join(
        char if char.isalnum() else "_"
        for char in label
    ).strip("_")

    return f"{user_id}_{label}.txt" if label else f"{user_id}.txt"


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    connection = get_connection()

    users = connection.execute(
        """
        SELECT user_id, username, first_name, first_seen, last_seen
        FROM users
        ORDER BY user_id
        """
    ).fetchall()

    total_readings = 0

    for user in users:
        readings = connection.execute(
            """
            SELECT
                created_at, question, spread_name,
                spread, interpretation, note, resonance
            FROM readings
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user["user_id"],)
        ).fetchall()

        filename = safe_filename(
            user["user_id"],
            user["username"],
            user["first_name"]
        )

        header = [
            f"Пользователь: {user['first_name'] or '(без имени)'}"
            + (f" (@{user['username']})" if user["username"] else ""),
            f"Telegram ID: {user['user_id']}",
            f"Первая активность: {user['first_seen']}",
            f"Последняя активность: {user['last_seen']}",
            f"Всего раскладов: {len(readings)}",
            "=" * 64,
            "",
        ]

        body = ("\n\n" + "-" * 64 + "\n\n").join(
            format_reading(reading) for reading in readings
        )

        (destination / filename).write_text(
            "\n".join(header) + body,
            encoding="utf-8"
        )

        total_readings += len(readings)

        print(f"{filename}: {len(readings)} раскладов")

    connection.close()

    print(
        f"\nГотово: {len(users)} пользователей, "
        f"{total_readings} раскладов -> {destination}/"
    )


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("exports")
    export(target)
