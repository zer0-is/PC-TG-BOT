"""Настройки и константы: читаются из окружения и .env."""

import hashlib
import logging
import os
import platform
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pcbot")

TOKEN = os.getenv("BOT_TOKEN", "").strip()


ALLOWED = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}


ALLOW_SHELL = os.getenv("ALLOW_SHELL", "false").lower() == "true"


ALLOW_FILES = os.getenv("ALLOW_FILES", "false").lower() == "true"


POWER_DELAY = int(os.getenv("POWER_DELAY", "25"))  # секунд до выполнения действия


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()


OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL", "inclusionai/ling-3.2-flash:free"
).strip()


def _env_int(name: str, default: int) -> int:
    """Читает целое из окружения, молча возвращая значение по умолчанию."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdecimal() else default


SIGNING_KEY = os.getenv("SIGNING_KEY", "").strip() or hashlib.sha256(
    f"pcbot-callback::{TOKEN}".encode()
).hexdigest()


RATE_LIMIT = _env_int("RATE_LIMIT", 20)  # команд в минуту на пользователя


ACTION_LOG_LIMIT = _env_int("ACTION_LOG_LIMIT", 300)  # строк в журнале действий


OS = platform.system()  # 'Windows' | 'Linux' | 'Darwin'


IS_WIN = OS == "Windows"


CONSOLE_ENC = "cp866" if IS_WIN else "utf-8"


DOWNLOADS = Path.home() / "Downloads"

if not TOKEN or not ALLOWED:
    sys.exit("Заполни BOT_TOKEN и ALLOWED_USER_IDS в файле .env (см. .env.example)")
