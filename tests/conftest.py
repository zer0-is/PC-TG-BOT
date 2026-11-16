"""Общая настройка тестов: переменные окружения и импорт модулей."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Выставляем окружение ДО импорта bot: модуль требует токен и белый список
# и завершает процесс, если их нет. Значения окружения имеют приоритет над
# .env, поэтому реальные секреты в тесты не попадают.
os.environ["BOT_TOKEN"] = "123456:AAHtest-token-for-unit-tests"
os.environ["ALLOWED_USER_IDS"] = "111111, 222222"
os.environ["ALLOW_SHELL"] = "false"
os.environ["ALLOW_FILES"] = "false"
os.environ["SIGNING_KEY"] = "test-signing-key"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["RATE_LIMIT"] = "20"

import bot  # noqa: E402
import control_panel  # noqa: E402

__all__ = ["bot", "control_panel", "ROOT"]
