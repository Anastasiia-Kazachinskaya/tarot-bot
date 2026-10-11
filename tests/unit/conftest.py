"""
Окружение для автотестов: временная база и фиктивные ключи.
Задаётся до импорта бота, потому что bot/* читает переменные при импорте.
Запуск: python -m pytest tests/unit
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="tarot-tests-"))

os.environ["BOT_TOKEN"] = "123456:TEST-token"
os.environ["CLOUD_API_KEY"] = "test-key"
os.environ["DATABASE_PATH"] = str(_tmp / "tarot.db")
os.environ["ADMIN_USER_IDS"] = ""
os.environ["UNLIMITED_USER_IDS"] = ""
os.environ["DAILY_READINGS_LIMIT"] = "100"
os.environ["DAILY_LLM_LIMIT"] = "100"
