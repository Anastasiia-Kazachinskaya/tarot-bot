import json
import sqlite3
from datetime import datetime
from pathlib import Path


DATABASE_FILE = Path(__file__).parent / "tarot.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)

    # Явно фиксируем UTF-8 на обеих сторонах: text_factory отвечает
    # за то, как Python декодирует TEXT-столбцы обратно в str, а
    # PRAGMA encoding — в какой кодировке SQLite хранит их на диске.
    # Без этого поведение зависит от дефолтов Python/SQLite в
    # конкретной сборке, а не гарантировано явно.
    connection.text_factory = str
    connection.execute("PRAGMA encoding = 'UTF-8'")

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

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                reversed_cards INTEGER
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                readings INTEGER NOT NULL DEFAULT 0,
                llm_requests INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
            """
        )

        # История открывается по user_id и сортируется по дате.
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_readings_user
            ON readings (user_id, created_at DESC)
            """
        )

        # Дневник: заметка к раскладу и отметка «отозвалось».
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(readings)"
            )
        }

        if "note" not in columns:
            connection.execute(
                "ALTER TABLE readings ADD COLUMN note TEXT"
            )

        if "resonance" not in columns:
            connection.execute(
                "ALTER TABLE readings ADD COLUMN resonance TEXT"
            )

        connection.commit()


def get_reversed_cards_preference(user_id: int) -> bool | None:
    """
    Возвращает сохранённую настройку «перевёрнутые карты»:
    True/False — запомненный выбор, None — настройка не задана
    и пользователя нужно спросить.
    """

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT reversed_cards
            FROM user_settings
            WHERE user_id = ?
            """,
            (user_id,)
        )

        row = cursor.fetchone()

    if row is None or row["reversed_cards"] is None:
        return None

    return bool(row["reversed_cards"])


def remember_reversed_cards_choice(
    user_id: int,
    reversed_cards: bool
) -> bool:
    """
    Запоминает выбор «перевёрнутые карты», сделанный по ходу расклада,
    но только если пользователь ещё ничего не настраивал.

    Так режим «спрашивать каждый раз», выбранный в настройках, не
    затирается первым же ответом. Возвращает True, если выбор сохранён.
    """

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO user_settings (user_id, reversed_cards)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, int(reversed_cards))
        )

        connection.commit()

    return cursor.rowcount > 0


def set_reversed_cards_preference(
    user_id: int,
    reversed_cards: bool | None
) -> None:
    """
    Сохраняет настройку «перевёрнутые карты» для пользователя.
    None означает «спрашивать каждый раз».
    """

    value = (
        None
        if reversed_cards is None
        else int(reversed_cards)
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id, reversed_cards)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                reversed_cards = excluded.reversed_cards
            """,
            (user_id, value)
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


def count_user_readings(user_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM readings
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

    return row["total"] if row else 0


def get_user_readings(
    user_id: int,
    limit: int = 10,
    offset: int = 0
) -> list[sqlite3.Row]:
    """
    Страница истории: от самых свежих к старым.
    offset сдвигает окно вглубь истории.
    """

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                id,
                created_at,
                question,
                spread_name,
                note,
                resonance
            FROM readings
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset)
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
                interpretation,
                note,
                resonance
            FROM readings
            WHERE id = ?
              AND user_id = ?
            """,
            (reading_id, user_id)
        )

        return cursor.fetchone()

# =================================================
# Дневник: заметки и отметка «отозвалось»
# =================================================

def set_reading_note(
    reading_id: int,
    user_id: int,
    note: str | None
) -> bool:
    """
    Сохраняет (или стирает, если note=None) заметку к раскладу.
    Возвращает False, если расклад не найден или принадлежит другому.
    """

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE readings
            SET note = ?
            WHERE id = ? AND user_id = ?
            """,
            (note, reading_id, user_id)
        )

        connection.commit()

    return cursor.rowcount > 0


def set_reading_resonance(
    reading_id: int,
    user_id: int,
    resonance: str | None
) -> bool:
    """
    Отмечает, отозвался ли расклад: "yes", "no" или None (снять отметку).
    """

    if resonance not in ("yes", "no", None):
        raise ValueError(f"Неизвестная отметка: {resonance}")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE readings
            SET resonance = ?
            WHERE id = ? AND user_id = ?
            """,
            (resonance, reading_id, user_id)
        )

        connection.commit()

    return cursor.rowcount > 0


def get_diary_stats(user_id: int, top: int = 7) -> dict:
    """
    Считает статистику по дневнику: сколько раскладов, сколько заметок
    и отметок, какие карты выпадали чаще всего.
    """

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT spread, note, resonance, created_at
            FROM readings
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchall()

    counter: dict[str, int] = {}
    reversed_counter: dict[str, int] = {}

    notes = 0
    resonated = 0
    not_resonated = 0

    for row in rows:
        if row["note"]:
            notes += 1

        if row["resonance"] == "yes":
            resonated += 1
        elif row["resonance"] == "no":
            not_resonated += 1

        try:
            spread = json.loads(row["spread"])
        except (TypeError, ValueError):
            continue

        for item in spread:
            card = item.get("card") or {}
            name = card.get("name")

            if not name:
                continue

            counter[name] = counter.get(name, 0) + 1

            if card.get("reversed"):
                reversed_counter[name] = (
                    reversed_counter.get(name, 0) + 1
                )

    top_cards = sorted(
        counter.items(),
        key=lambda pair: (-pair[1], pair[0])
    )[:top]

    return {
        "readings": len(rows),
        "notes": notes,
        "resonated": resonated,
        "not_resonated": not_resonated,
        "first_reading": min(
            (row["created_at"] for row in rows),
            default=None
        ),
        "top_cards": [
            {
                "name": name,
                "count": count,
                "reversed": reversed_counter.get(name, 0)
            }
            for name, count in top_cards
        ]
    }


# =================================================
# Дневной лимит запросов
# =================================================

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_usage(user_id: int) -> tuple[int, int]:
    """Сколько раскладов и запросов к модели сделано сегодня."""

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT readings, llm_requests
            FROM usage
            WHERE user_id = ? AND day = ?
            """,
            (user_id, _today())
        ).fetchone()

    if row is None:
        return (0, 0)

    return (row["readings"], row["llm_requests"])


def consume_quota(
    user_id: int,
    readings_limit: int,
    llm_limit: int,
    is_reading: bool
) -> tuple[bool, str | None, int, int]:
    """
    Проверяет дневной лимит и, если он не исчерпан, сразу засчитывает
    запрос. Считаем до обращения к модели, потому что платим за попытку,
    а не за удачный ответ.

    Возвращает (можно ли, что упёрлось, использовано, лимит).
    """

    day = _today()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT readings, llm_requests
            FROM usage
            WHERE user_id = ? AND day = ?
            """,
            (user_id, day)
        ).fetchone()

        readings = row["readings"] if row else 0
        llm_requests = row["llm_requests"] if row else 0

        if is_reading and readings_limit and readings >= readings_limit:
            return (False, "readings", readings, readings_limit)

        if llm_limit and llm_requests >= llm_limit:
            return (False, "llm", llm_requests, llm_limit)

        connection.execute(
            """
            INSERT INTO usage (user_id, day, readings, llm_requests)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, day) DO UPDATE SET
                readings = readings + excluded.readings,
                llm_requests = llm_requests + 1
            """,
            (user_id, day, 1 if is_reading else 0)
        )

        connection.commit()

    return (True, None, readings + (1 if is_reading else 0), readings_limit)


# =================================================
# Кто пользуется ботом
# =================================================

def touch_user(
    user_id: int,
    username: str | None,
    first_name: str | None
) -> None:
    """
    Отмечает, что человек пользовался ботом. Храним только имя
    и username — ни вопросов, ни интерпретаций здесь нет.
    """

    now = datetime.now().isoformat(timespec="seconds")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (
                user_id, username, first_name, first_seen, last_seen
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
            """,
            (user_id, username, first_name, now, now)
        )

        connection.commit()


def get_bot_stats(limit: int = 20) -> dict:
    """
    Сводка для владельца: кто пользуется ботом и сколько.
    Только агрегаты — содержимое раскладов не читается.
    """

    today = _today()

    with get_connection() as connection:
        users = connection.execute(
            """
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.first_seen,
                u.last_seen,
                (
                    SELECT COUNT(*)
                    FROM readings r
                    WHERE r.user_id = u.user_id
                ) AS readings,
                (
                    SELECT COALESCE(SUM(llm_requests), 0)
                    FROM usage g
                    WHERE g.user_id = u.user_id
                ) AS llm_requests,
                (
                    SELECT COALESCE(SUM(readings), 0)
                    FROM usage g
                    WHERE g.user_id = u.user_id AND g.day = ?
                ) AS readings_today
            FROM users u
            ORDER BY readings DESC, u.last_seen DESC
            LIMIT ?
            """,
            (today, limit)
        ).fetchall()

        totals = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users,
                (SELECT COUNT(*) FROM readings) AS readings,
                (
                    SELECT COALESCE(SUM(llm_requests), 0)
                    FROM usage
                ) AS llm_requests,
                (
                    SELECT COALESCE(SUM(llm_requests), 0)
                    FROM usage WHERE day = ?
                ) AS llm_today
            """,
            (today,)
        ).fetchone()

        spreads = connection.execute(
            """
            SELECT spread_name, COUNT(*) AS count
            FROM readings
            GROUP BY spread_name
            ORDER BY count DESC
            """
        ).fetchall()

    return {
        "users": totals["users"],
        "readings": totals["readings"],
        "llm_requests": totals["llm_requests"],
        "llm_today": totals["llm_today"],
        "top_users": [dict(row) for row in users],
        "spreads": [dict(row) for row in spreads],
    }
