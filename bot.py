"""
Телеграм-бот для удалённого управления своим ПК.
Работает локально, на Windows / Linux / macOS (часть функций — только Windows).
Принимает команды ТОЛЬКО от пользователей из ALLOWED_USER_IDS.
"""
import asyncio
import ctypes
import hashlib
import hmac
import html
import logging
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import mss
import mss.tools
import psutil
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
load_dotenv(BASE_DIR / ".env")

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


# Ключ подписи callback_data. Если не задан — выводится из токена бота,
# поэтому остаётся стабильным между перезапусками и старые кнопки в чате
# продолжают работать.
SIGNING_KEY = os.getenv("SIGNING_KEY", "").strip() or hashlib.sha256(
    f"pcbot-callback::{TOKEN}".encode("utf-8")
).hexdigest()

RATE_LIMIT = _env_int("RATE_LIMIT", 20)  # команд в минуту на пользователя
ACTION_LOG_LIMIT = _env_int("ACTION_LOG_LIMIT", 300)  # строк в журнале действий

if not TOKEN or not ALLOWED:
    sys.exit("Заполни BOT_TOKEN и ALLOWED_USER_IDS в файле .env (см. .env.example)")

OS = platform.system()  # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = OS == "Windows"
CONSOLE_ENC = "cp866" if IS_WIN else "utf-8"
DOWNLOADS = Path.home() / "Downloads"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pcbot")

bot = Bot(TOKEN)
dp = Dispatcher()

# Кто сейчас должен прислать текст и для чего: {user_id: "msg" | "clip"}
waiting_input: dict[int, str] = {}
# Чтобы фоновые задачи (окно-сообщение) не удалились сборщиком мусора
background: set[asyncio.Task] = set()


def in_background(coro) -> None:
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


async def ask_openrouter(prompt: str) -> str:
    async with AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        timeout=45,
        max_retries=1,
    ) as client:
        response = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
    answer = response.choices[0].message.content
    if not answer or not answer.strip():
        raise RuntimeError("Модель вернула пустой ответ")
    return answer.strip()


# ---------------------------------------------------------------- доступ


class WhitelistMiddleware(BaseMiddleware):
    """Молча игнорирует всех, кого нет в белом списке."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.id not in ALLOWED:
            if user is not None:
                log.warning("Отклонён доступ: id=%s username=%s", user.id, user.username)
            return None
        return await handler(event, data)


dp.message.outer_middleware(WhitelistMiddleware())
dp.callback_query.outer_middleware(WhitelistMiddleware())


# ---------------------------------------------------------------- безопасность


def sign_data(data: str) -> str:
    """Подписывает callback_data, чтобы кнопку нельзя было подделать."""
    digest = hmac.new(
        SIGNING_KEY.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:16]
    return f"{data}|{digest}"


def unsign_data(payload: str) -> str | None:
    """Возвращает исходные данные, если подпись верна, иначе None."""
    data, separator, digest = payload.rpartition("|")
    if not separator:
        return None
    expected = hmac.new(
        SIGNING_KEY.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:16]
    if not hmac.compare_digest(digest, expected):
        return None
    return data


async def reject(event, text: str) -> None:
    """Отвечает пользователю, не различая сообщение и нажатие кнопки."""
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)


class CallbackSignerMiddleware(BaseMiddleware):
    """Проверяет подпись callback_data и подменяет её исходным значением."""

    async def __call__(self, handler, event, data):
        unsigned = unsign_data(event.data)
        if unsigned is None:
            log.warning("Отклонён callback без верной подписи: %s", event.data[:64])
            await reject(event, "Кнопка устарела или подделана. Открой меню заново.")
            return None
        # CallbackQuery в aiogram — неизменяемая модель (frozen), поэтому
        # подменяем значение в обход pydantic-валидации.
        object.__setattr__(event, "data", unsigned)
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Ограничивает количество запросов от одного пользователя."""

    def __init__(self, limit: int, window: int = 60):
        self.limit = max(1, limit)
        self.window = window
        self._buckets: dict[int, list[float]] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None:
            now = time.monotonic()
            cutoff = now - self.window
            bucket = [stamp for stamp in self._buckets.get(user.id, []) if stamp > cutoff]
            if len(bucket) >= self.limit:
                log.warning("Сработал лимит запросов для id=%s", user.id)
                await reject(event, "Слишком много запросов. Подожди минуту.")
                return None
            bucket.append(now)
            self._buckets[user.id] = bucket
        return await handler(event, data)


# Журнал действий: кто, когда и что сделал.
action_log: list[dict[str, object]] = []


def log_action(user_id: int, action: str, detail: str = "") -> None:
    action_log.append(
        {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_id,
            "action": action,
            "detail": detail,
        }
    )
    del action_log[:-ACTION_LOG_LIMIT]
    log.info("Действие %s от id=%s: %s", action, user_id, detail)


# Отложенные действия, ждущие подтверждения: {token: {...}}
pending_actions: dict[str, dict[str, object]] = {}


def store_action(user_id: int, action: str, payload: str, ttl: int = 900) -> str:
    """Запоминает действие и возвращает короткий токен для кнопки подтверждения."""
    now = time.time()
    for token, item in list(pending_actions.items()):
        if now - float(item["created"]) > ttl:  # type: ignore[arg-type]
            pending_actions.pop(token, None)
    token = secrets.token_hex(6)
    pending_actions[token] = {
        "user": user_id,
        "action": action,
        "payload": payload,
        "created": now,
    }
    return token


def take_action(token: str, user_id: int) -> tuple[str, str] | None:
    """Забирает отложенное действие, если токен верный и принадлежит пользователю.

    Чужой токен не расходуется: иначе посторонний, угадав токен, мог бы
    «сжечь» подтверждение владельца.
    """
    item = pending_actions.get(token)
    if not item:
        return None
    if item["user"] != user_id:
        return None
    pending_actions.pop(token, None)
    return str(item["action"]), str(item["payload"])


dp.callback_query.outer_middleware(CallbackSignerMiddleware())
_rate_limiter = RateLimitMiddleware(RATE_LIMIT)
dp.message.outer_middleware(_rate_limiter)
dp.callback_query.outer_middleware(_rate_limiter)


BENIGN_ERRORS = (
    "message is not modified",
    "message is too old",
    "message to edit not found",
    "query is too old",
)


@dp.errors()
async def on_error(event: ErrorEvent):
    """Не считаем ошибкой безобидные ситуации Telegram."""
    message = str(event.exception)
    if isinstance(event.exception, TelegramBadRequest) and any(
        marker in message for marker in BENIGN_ERRORS
    ):
        log.debug("Безобидная ошибка Telegram: %s", message)
        return True
    log.error("Ошибка в обработчике: %s", event.exception, exc_info=event.exception)
    return True


# ---------------------------------------------------------------- питание

POWER_COMMANDS = {
    "Windows": {
        "shutdown": ["shutdown", "/s", "/t", "0"],
        "reboot": ["shutdown", "/r", "/t", "0"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
    },
    "Linux": {
        "shutdown": ["systemctl", "poweroff"],
        "reboot": ["systemctl", "reboot"],
        "sleep": ["systemctl", "suspend"],
        "lock": ["loginctl", "lock-session"],
    },
    "Darwin": {
        "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
        "reboot": ["osascript", "-e", 'tell app "System Events" to restart'],
        "sleep": ["pmset", "sleepnow"],
        "lock": ["pmset", "displaysleepnow"],
    },
}

ACTION_NAMES = {
    "shutdown": "Выключение",
    "reboot": "Перезагрузка",
    "sleep": "Спящий режим",
    "lock": "Блокировка",
}

pending_power: asyncio.Task | None = None
pending_info: str = ""  # что и когда запланировано (для показа)


async def run_power(action: str, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        log.info("Запланированное действие отменено")
        raise
    cmd = POWER_COMMANDS[OS][action]
    log.info("Выполняю: %s", cmd)
    subprocess.Popen(cmd)


def schedule_power(action: str, delay: int) -> str:
    global pending_power, pending_info
    if pending_power is not None and not pending_power.done():
        pending_power.cancel()
    pending_power = asyncio.create_task(run_power(action, delay))
    at = (datetime.now() + timedelta(seconds=delay)).strftime("%H:%M")
    pending_info = f"{ACTION_NAMES[action]} в {at}"
    return at


async def cancel_pending_power() -> bool:
    global pending_power, pending_info
    task, pending_power = pending_power, None
    pending_info = ""
    if task is None or task.done():
        return False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True


# ---------------------------------------------------------------- Windows-штуки


def need_windows() -> None:
    if not IS_WIN:
        raise RuntimeError("Эта функция работает только на Windows")


VK = {
    "vol_up": 0xAF,
    "vol_down": 0xAE,
    "mute": 0xAD,
    "play": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
}


def press_media_key(name: str, times: int = 1) -> None:
    need_windows()
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(VK[name], 0, 0, 0)
        user32.keybd_event(VK[name], 0, 2, 0)  # KEYEVENTF_KEYUP
        time.sleep(0.02)


def show_message_box(text: str) -> None:
    """Показывает окно поверх всех и ждёт, пока его закроют."""
    need_windows()
    # MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND
    ctypes.windll.user32.MessageBoxW(None, text, "Сообщение из Telegram", 0x40 | 0x1000 | 0x40000 | 0x10000)


CF_UNICODETEXT = 13


def _clipboard_api():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    return user32, kernel32


def _open_clipboard(user32) -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError("Буфер обмена занят другой программой, попробуй ещё раз")


def clipboard_get() -> str:
    need_windows()
    user32, kernel32 = _clipboard_api()
    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> None:
    need_windows()
    user32, kernel32 = _clipboard_api()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    _open_clipboard(user32)
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not handle:
            raise RuntimeError("Не удалось выделить память под буфер")
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise RuntimeError("Не удалось записать в буфер")
    finally:
        user32.CloseClipboard()


def webcam_photo() -> bytes:
    try:
        import cv2  # opencv-python-headless
    except ImportError as e:
        raise RuntimeError("Не установлена библиотека камеры — запусти update.bat") from e
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if IS_WIN else cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Камера не найдена или занята другой программой")
    try:
        for _ in range(15):  # камере нужно пару кадров, чтобы настроить яркость
            cap.read()
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Не удалось снять кадр")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Не удалось сохранить фото")
        return buf.tobytes()
    finally:
        cap.release()


# ---------------------------------------------------------------- программы

# Кнопки быстрого запуска: ключ -> (подпись, варианты пути/ссылки)
APPS = {
    "steam": ("🎮 Steam", ["steam://open/main"]),
    "telegram": ("✈️ Telegram", [r"%APPDATA%\Telegram Desktop\Telegram.exe"]),
    "music": ("🎵 Яндекс Музыка", [r"%LOCALAPPDATA%\Programs\YandexMusic\Яндекс Музыка.exe"]),
    "chrome": (
        "🌐 Chrome",
        [
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
    ),
    "explorer": ("📁 Проводник", ["explorer.exe"]),
    "taskmgr": ("📈 Диспетчер задач", ["taskmgr.exe"]),
}


def open_target(target: str) -> None:
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    elif OS == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def launch_app(key: str) -> None:
    title, variants = APPS[key]
    if key == "telegram" and IS_WIN:
        app_alias = shutil.which("Telegram.exe")
        if app_alias:
            open_target(app_alias)
            return
    for v in variants:
        path = os.path.expandvars(v)
        if "://" in path or not ("\\" in path or "/" in path) or Path(path).exists():
            open_target(path)
            return
    raise RuntimeError(f"{title}: не нашёл программу на компьютере")


# ---------------------------------------------------------------- Steam

SKIP_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "961940"}  # служебные пакеты Steam


def steam_dir() -> Path | None:
    if IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
                if path.exists():
                    return path
        except OSError:
            pass
    for guess in (
        Path(os.path.expandvars(r"%ProgramFiles(x86)%\Steam")),
        Path(os.path.expandvars(r"%ProgramFiles%\Steam")),
        Path.home() / ".steam" / "steam",
    ):
        if guess.exists():
            return guess
    return None


def _vdf_value(text: str, key: str) -> str:
    m = re.search(rf'"{key}"\s+"([^"]*)"', text, re.IGNORECASE)
    return m.group(1) if m else ""


def steam_libraries() -> set[Path]:
    root = steam_dir()
    if not root:
        raise RuntimeError("Steam не найден на компьютере")
    libraries = {root}
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.add(Path(m.group(1).replace("\\\\", "\\")))
    return libraries


def steam_games() -> list[tuple[str, str, int]]:
    """Установленные игры: (appid, название, когда последний раз играл), свежие — первыми."""
    games = {}
    for lib in steam_libraries():
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = _vdf_value(text, "appid")
            name = _vdf_value(text, "name")
            if not appid or not name or appid in SKIP_APPIDS or "redistributable" in name.lower():
                continue
            last = int(_vdf_value(text, "LastPlayed") or 0)
            games[appid] = (appid, name, last)
    return sorted(games.values(), key=lambda g: (-g[2], g[1].lower()))


def run_steam_game(appid: str) -> None:
    open_target(f"steam://rungameid/{appid}")


def running_steam_games() -> list[tuple[int, str, str]]:
    """Запущенные игры из Steam: (pid, имя процесса, папка игры)."""
    commons = [str(lib / "steamapps" / "common").lower() + os.sep for lib in steam_libraries()]
    found = []
    for p in psutil.process_iter(["exe", "name"]):
        exe = (p.info.get("exe") or "").lower()
        for common in commons:
            if exe.startswith(common):
                folder = Path(p.info["exe"]).parts[len(Path(common).parts)]
                found.append((p.pid, p.info["name"] or "?", folder))
                break
    return found


# ---------------------------------------------------------------- Chrome и Telegram

# Сайты для кнопок после запуска Chrome: (подпись, адрес) — можно менять
SITES = [
    ("▶️ YouTube", "https://www.youtube.com"),
    ("💙 ВКонтакте", "https://vk.com"),
    ("🔎 Яндекс", "https://ya.ru"),
    ("🗺 2ГИС", "https://2gis.ru"),
    ("🎬 Кинопоиск", "https://www.kinopoisk.ru"),
    ("🟣 Twitch", "https://www.twitch.tv"),
]

# Чаты/каналы для кнопок после запуска Telegram: (подпись, ссылка) — впиши свои.
# Пример: ("📢 Мой канал", "tg://resolve?domain=имя_канала_без_собаки"),
TG_LINKS = [
]


def open_site(url: str) -> None:
    for v in APPS["chrome"][1]:
        path = os.path.expandvars(v)
        if Path(path).exists():
            subprocess.Popen([path, url])
            return
    open_target(url)  # нет Chrome — откроем в браузере по умолчанию


# ---------------------------------------------------------------- инфо о ПК


def take_screenshot() -> bytes:
    make = getattr(mss, "MSS", None) or mss.mss  # в новых версиях mss.mss устарел
    with make() as sct:
        shot = sct.grab(sct.monitors[0])  # все мониторы
        return mss.tools.to_png(shot.rgb, shot.size)


def _to_number(value: object) -> float | None:
    """'12.3' -> 12.3, '[N/A]' -> None."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def get_cpu_info() -> dict:
    """Подробная информация о процессоре."""
    cores = psutil.cpu_count(logical=False) or 1
    logical = psutil.cpu_count(logical=True) or 1
    model = platform.processor() or ""
    if not model and IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as handle:
                model = str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
        except OSError:
            model = ""
    frequency = 0
    try:
        cpufreq = psutil.cpu_freq()
        frequency = int(cpufreq.current) if cpufreq else 0
    except (AttributeError, NotImplementedError, OSError, ZeroDivisionError):
        frequency = 0
    return {
        "cores": cores,
        "logical": logical,
        "model": model or "Unknown",
        "frequency_mhz": frequency,
    }


def get_ram_info() -> dict:
    """Информация об оперативной памяти."""
    try:
        mem = psutil.virtual_memory()
    except psutil.Error:
        return {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}
    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "used_gb": round(mem.used / (1024**3), 2),
        "free_gb": round(mem.free / (1024**3), 2),
        "percent": mem.percent,
    }


def get_disk_info() -> list[dict]:
    """Информация по всем дискам. Недоступные разделы просто пропускаются."""
    disks = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (psutil.Error, OSError):
            continue
        disks.append(
            {
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "percent": usage.percent,
            }
        )
    return disks

def _nvidia_gpu_info() -> list[dict]:
    """Реальные данные по видеокарте через nvidia-smi, если он установлен."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        result = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append(
            {
                "name": parts[0] or "NVIDIA",
                "load": _to_number(parts[3]),
                "memory_mb": _to_number(parts[1]),
                "memory_used_mb": _to_number(parts[2]),
                "temperature": _to_number(parts[4]),
            }
        )
    return gpus


def _wmi_gpu_info() -> list[dict]:
    """Видеокарты через WMI — работает с любым GPU, но без загрузки в процентах."""
    try:
        import win32com.client
    except ImportError:
        return []
    try:
        wmi = win32com.client.GetObject("winmgmts:")
        gpus = []
        for gpu in wmi.Query("SELECT * FROM Win32_VideoController"):
            ram = int(gpu.AdapterRAM or 0)
            gpus.append(
                {
                    "name": gpu.Name or "Unknown",
                    "load": None,
                    "memory_mb": ram // (1024**2),
                    "memory_used_mb": None,
                    "temperature": None,
                }
            )
        return gpus
    except Exception:  # noqa: BLE001 — WMI может отдать что угодно
        return []


def get_gpu_info() -> list[dict]:
    """Видеокарты: сначала nvidia-smi (реальная загрузка и VRAM), затем WMI."""
    gpus = _nvidia_gpu_info()
    if gpus:
        return gpus
    return _wmi_gpu_info()

def get_system_uptime() -> str:
    """Время работы системы."""
    try:
        uptime_seconds = time.time() - psutil.boot_time()
    except psutil.Error:
        return "Неизвестно"
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"{days}д {hours}ч {minutes}мин"


def get_windows_info() -> str:
    """Версия и разрядность ОС."""
    if IS_WIN:
        try:
            major, minor, build, _ptype, csd = platform.win32_ver()
            if build >= 22000:
                name = "Windows 11"
            elif major == 10:
                name = "Windows 10"
            else:
                name = f"Windows {major}.{minor}"
            arch = "x64" if platform.architecture()[0] == "64bit" else "x86"
            suffix = f" {csd}" if csd else ""
            return f"{name} {arch} (сборка {build}{suffix})"
        except (OSError, ValueError):
            pass
    return platform.platform()


def get_battery_info() -> dict:
    """Состояние батареи (для ноутбуков)."""
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        battery = None
    if battery is None:
        return {"percent": None, "power_plugged": False, "secs_left": None}
    secs_left = battery.secsleft if battery.secsleft != -1 else None
    return {
        "percent": battery.percent,
        "power_plugged": bool(battery.power_plugged),
        "secs_left": secs_left,
    }

def get_temperatures():
    """Get temperature sensor readings."""
    try:
        temps = psutil.sensors_temperatures()
        return {
            f"{name} {index + 1}": {
                "value": entry.current,
                "high": entry.high,
                "critical": entry.critical,
            }
            for name, entries in (temps or {}).items()
            for index, entry in enumerate(entries)
            if entry.current is not None
        }
    except (AttributeError, NotImplementedError):
        return {}

def system_stats() -> str:
    cpu = psutil.cpu_percent(interval=1)
    cpu_info = get_cpu_info()
    ram_info = get_ram_info()
    disks = get_disk_info()
    gpus = get_gpu_info()
    uptime = get_system_uptime()
    windows = get_windows_info()
    battery = get_battery_info()
    temps = get_temperatures()

    frequency = cpu_info.get("frequency_mhz") or 0
    cpu_text = (
        f"🟢 <b>Загрузка CPU:</b> {cpu:.1f}%\n"
        f"💻 {html.escape(cpu_info['model'])} — {cpu_info['cores']} ядер "
        f"({cpu_info['logical']} потоков)"
        + (f", {frequency} МГц" if frequency else "")
    )
    ram_text = (
        f"🧠 <b>Память:</b> {ram_info['percent']}% "
        f"({ram_info['used_gb']:.1f} / {ram_info['total_gb']:.1f} ГБ)"
    )

    disk_parts = []
    for disk in disks[:3]:
        disk_parts.append(
            f"• <code>{html.escape(disk['mountpoint'])}</code>: "
            f"занято {disk['percent']:.1f}% "
            f"({disk['used_gb']:.1f} / {disk['total_gb']:.1f} ГБ), "
            f"свободно {disk['free_gb']:.1f} ГБ"
        )
    disk_text = "\n".join(disk_parts) if disk_parts else "Диски не найдены."

    gpu_parts = []
    for gpu in gpus[:2]:
        details = []
        if gpu.get("load") is not None:
            details.append(f"загрузка {gpu['load']:.0f}%")
        if gpu.get("memory_mb"):
            used = gpu.get("memory_used_mb")
            memory = f"{gpu['memory_mb']:.0f} МБ"
            if used is not None:
                memory = f"{used:.0f} / {gpu['memory_mb']:.0f} МБ"
            details.append(f"VRAM {memory}")
        if gpu.get("temperature") is not None:
            details.append(f"{gpu['temperature']:.0f} °C")
        suffix = f" ({', '.join(details)})" if details else ""
        gpu_parts.append(f"• {html.escape(gpu['name'])}{suffix}")
    gpu_text = "\n".join(gpu_parts) if gpu_parts else "Информация о GPU недоступна."

    if battery["percent"] is None:
        battery_text = "Данные о батарее недоступны."
    else:
        power = "от сети" if battery["power_plugged"] else "от батареи"
        battery_text = f"{battery['percent']:.0f}% ({power})"
        if battery["secs_left"]:
            battery_text += f", осталось {battery['secs_left'] // 60} мин"

    if temps:
        temp_text = "\n".join(
            f"• {html.escape(name)}: {info['value']:.1f} °C"
            for name, info in list(temps.items())[:3]
        )
    else:
        temp_text = "Датчики температуры недоступны."

    lines = [
        "🖥 <b>Состояние компьютера</b>",
        "",
        cpu_text,
        ram_text,
        "",
        "💿 <b>Диски</b>",
        disk_text,
        "",
        f"🎮 <b>GPU</b>\n{gpu_text}",
        f"🔋 <b>Батарея:</b> {battery_text}",
        f"⏰ <b>Время работы:</b> {uptime}",
        f"🪟 <b>Система:</b> {html.escape(windows)}",
        "🌡 <b>Температура</b>",
        temp_text,
    ]
    if pending_info and pending_power and not pending_power.done():
        lines.extend(["", f"⏳ <b>Запланировано:</b> {html.escape(pending_info)}"])
    return "\n".join(lines)


# Эти процессы закрывать нельзя — Windows упадёт или перезагрузится
PROTECTED = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "dwm.exe", "fontdrvhost.exe", "memory compression", "secure system",
    # оболочка и системные службы: их закрытие ломает рабочий стол
    "explorer.exe", "conhost.exe", "searchhost.exe", "searchapp.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "lockapp.exe", "runtimebroker.exe", "sihost.exe",
    "wudfhost.exe", "ctfmon.exe", "taskhostw.exe", "spoolsv.exe",
    "lsaiso.exe", "securityhealthservice.exe", "audiodg.exe", "wmiprvse.exe",
    "rundll32.exe", "dllhost.exe", "wmiapsrv.exe",
}


def protected_pids() -> set[int]:
    """PID'ы, которые нельзя закрывать: система, сам бот и панель управления."""
    pids = {0, 4, os.getpid()}
    try:
        pids.add(os.getppid())
    except OSError:
        pass
    return pids


def process_rows(n: int | None = 10) -> list[tuple[float, float, int, str]]:
    """Список процессов: (CPU %, RAM %, PID, имя). n=None — без ограничения."""
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.7)
    cores = psutil.cpu_count() or 1
    rows = []
    for p in procs:
        if p.pid in (0, 4):
            continue
        try:
            rows.append((p.cpu_percent(None) / cores, p.memory_percent(), p.pid, p.info["name"] or "?"))
        except psutil.Error:
            continue
    rows.sort(reverse=True)
    return rows if n is None else rows[:n]


def sort_rows(rows: list[tuple[float, float, int, str]], sort: str) -> list[tuple[float, float, int, str]]:
    """Сортирует процессы: по CPU, по RAM или по имени."""
    if sort == "ram":
        return sorted(rows, key=lambda row: (-row[1], row[3].lower()))
    if sort == "name":
        return sorted(rows, key=lambda row: row[3].lower())
    return sorted(rows, key=lambda row: (-row[0], row[3].lower()))


def format_rows(rows) -> str:
    out = ["CPU%  RAM%   PID  Имя"]
    for cpu, mem, pid, name in rows:
        out.append(f"{cpu:4.1f} {mem:5.1f} {pid:6d}  {name}")
    return "\n".join(out)


def kill_process(pid: int) -> str:
    if pid in protected_pids():
        raise RuntimeError("Это сам бот или системный процесс — его так не закрыть")
    p = psutil.Process(pid)
    name = p.name()
    if name.lower() in PROTECTED:
        raise RuntimeError(f"{name} — системный процесс, его закрывать нельзя")
    p.terminate()
    try:
        p.wait(timeout=3)
    except psutil.TimeoutExpired:
        p.kill()
    return name


# ---------------------------------------------------------------- клавиатуры


def b(text: str, **kwargs) -> InlineKeyboardButton:
    """Кнопка с подписанным callback_data — подделать её извне невозможно."""
    data = kwargs.get("callback_data")
    if isinstance(data, str):
        kwargs["callback_data"] = sign_data(data)
    return InlineKeyboardButton(text=text, **kwargs)


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


BACK = b(text="⬅️ В меню", callback_data="menu")


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="📸 Скриншот", callback_data="screenshot"), b(text="📷 Веб-камера", callback_data="webcam")],
        [b(text="📊 Состояние ПК", callback_data="stats"), b(text="📋 Процессы", callback_data="procs")],
        [b(text="🎵 Звук и музыка", callback_data="sound"), b(text="🚀 Программы", callback_data="apps")],
        [b(text="💬 Сообщение на экран", callback_data="msg"), b(text="📎 Буфер обмена", callback_data="clip")],
        [b(text="⏲ Таймер выключения", callback_data="timer"), b(text="⚠️ Закрыть все процессы", callback_data="closeall")],
        [b(text="🔒 Заблокировать", callback_data="ask:lock"), b(text="😴 Спящий режим", callback_data="ask:sleep")],
        [b(text="🔄 Перезагрузить", callback_data="ask:reboot"), b(text="⏻ Выключить", callback_data="ask:shutdown")],
        [b(text="♻️ Перезапустить бота", callback_data="restart_ask")],
    ])


def detail_menu(refresh_callback: str) -> InlineKeyboardMarkup:
    return kb([[b(text="🔄 Обновить", callback_data=refresh_callback), BACK]])


def confirm_kb(yes: str, no: str = "cancel_ask") -> InlineKeyboardMarkup:
    return kb([[b(text="✅ Подтвердить", callback_data=yes),
                b(text="↩️ Назад", callback_data=no)]])


def sound_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="🔉 −10%", callback_data="key:vol_down"),
         b(text="🔇 Звук вкл/выкл", callback_data="key:mute"),
         b(text="🔊 +10%", callback_data="key:vol_up")],
        [b(text="⏮", callback_data="key:prev"),
         b(text="⏯ Пауза / Играть", callback_data="key:play"),
         b(text="⏭", callback_data="key:next")],
        [BACK],
    ])


def apps_menu() -> InlineKeyboardMarkup:
    keys = list(APPS)
    rows = [[b(text=APPS[k][0], callback_data=f"app:{k}") for k in keys[i:i + 2]]
            for i in range(0, len(keys), 2)]
    rows.append([BACK])
    return kb(rows)


GAMES_PER_PAGE = 8


def steam_menu(games, page: int) -> InlineKeyboardMarkup:
    pages = max(1, (len(games) + GAMES_PER_PAGE - 1) // GAMES_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = games[page * GAMES_PER_PAGE:(page + 1) * GAMES_PER_PAGE]
    rows = [[b(text=f"▶️ {name[:30]}", callback_data=f"sg:{appid}")] for appid, name, _ in chunk]
    nav = []
    if page > 0:
        nav.append(b(text="◀️", callback_data=f"sgp:{page - 1}"))
    if pages > 1:
        nav.append(b(text=f"{page + 1}/{pages}", callback_data=f"sgp:{page}"))
    if page < pages - 1:
        nav.append(b(text="▶️", callback_data=f"sgp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([b(text="⛔ Закрыть игру", callback_data="sgk")])
    rows.append([b(text="⬅️ Программы", callback_data="apps"), BACK])
    return kb(rows)


def sites_menu() -> InlineKeyboardMarkup:
    rows = [[b(text=t, callback_data=f"site:{i}") for i, (t, _) in list(enumerate(SITES))[j:j + 2]]
            for j in range(0, len(SITES), 2)]
    rows.append([b(text="⬅️ Программы", callback_data="apps"), BACK])
    return kb(rows)


def tg_menu() -> InlineKeyboardMarkup:
    rows = [[b(text=t, callback_data=f"tgl:{i}")] for i, (t, _) in enumerate(TG_LINKS)]
    rows.append([b(text="⬅️ Программы", callback_data="apps"), BACK])
    return kb(rows)


def timer_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="10 мин", callback_data="tmr:10"),
         b(text="30 мин", callback_data="tmr:30"),
         b(text="1 час", callback_data="tmr:60")],
        [b(text="1.5 часа", callback_data="tmr:90"),
         b(text="2 часа", callback_data="tmr:120"),
         b(text="3 часа", callback_data="tmr:180")],
        [b(text="🛑 Отменить таймер", callback_data="cancel"), BACK],
    ])


def clip_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="👀 Что в буфере", callback_data="clip_get"),
         b(text="✏️ Записать в буфер", callback_data="clip_set")],
        [BACK],
    ])


PROC_PAGE_SIZE = 8
PROC_SORTS = (("cpu", "CPU"), ("ram", "RAM"), ("name", "Имя"))


def procs_menu(rows, page: int = 0, sort: str = "cpu") -> InlineKeyboardMarkup:
    """Клавиатура списка процессов: закрытие, сортировка, страницы."""
    pages = max(1, math.ceil(len(rows) / PROC_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = rows[page * PROC_PAGE_SIZE:(page + 1) * PROC_PAGE_SIZE]
    buttons: list[list] = []
    line: list = []
    for _cpu, _mem, pid, name in chunk:
        line.append(b(text=f"❌ {name[:18]}", callback_data=f"kill:{pid}"))
        if len(line) == 2:
            buttons.append(line)
            line = []
    if line:
        buttons.append(line)
    buttons.append(
        [
            b(text=f"{'✓ ' if sort == key else ''}{label}", callback_data=f"procs:{key}:0")
            for key, label in PROC_SORTS
        ]
    )
    nav = []
    if page > 0:
        nav.append(b(text="◀️", callback_data=f"procs:{sort}:{page - 1}"))
    nav.append(b(text=f"{page + 1}/{pages}", callback_data=f"procs:{sort}:{page}"))
    if page < pages - 1:
        nav.append(b(text="▶️", callback_data=f"procs:{sort}:{page + 1}"))
    buttons.append(nav)
    buttons.append([BACK])
    return kb(buttons)


def procs_text(rows, page: int = 0, sort: str = "cpu") -> str:
    """Текст страницы списка процессов."""
    pages = max(1, math.ceil(len(rows) / PROC_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = rows[page * PROC_PAGE_SIZE:(page + 1) * PROC_PAGE_SIZE]
    return (
        f"<pre>{html.escape(format_rows(chunk))}</pre>\n"
        f"Страница {page + 1}/{pages}, всего {len(rows)}. "
        "Нажми ❌, чтобы закрыть программу."
    )


HELP = (
    "🖥 <b>Панель управления</b>\n"
    "Выбери действие кнопкой ниже.\n\n"
    "📎 <b>Пришли файл, фото или видео</b> — сохраню в «Загрузки» на ПК (до 20 МБ).\n\n"
    "<b>Команды</b>\n"
    "/menu — меню\n"
    "/help — эта справка\n"
    "/stats — состояние ПК\n"
    "/procs — процессы (сортировка и страницы)\n"
    "/allprocs — все процессы по имени\n"
    "/screenshot — скриншот экрана\n"
    "/webcam — фото с веб-камеры\n"
    "/ask <code>вопрос</code> — спросить ИИ через OpenRouter\n"
    "/msg <code>текст</code> — показать сообщение на экране ПК\n"
    "/clip — что в буфере обмена\n"
    "/open <code>путь | программа | URL</code> — открыть\n"
    "/cancel — отменить запланированное действие\n"
    + ("/closeall — закрыть все пользовательские процессы\n" if ALLOW_SHELL else "")
    + ("/run <code>команда</code> — выполнить команду в оболочке\n" if ALLOW_SHELL else "")
    + ("/get <code>путь</code> — получить файл с ПК\n" if ALLOW_FILES else "")
)

# ---------------------------------------------------------------- меню


@dp.message(Command("start", "help", "menu"))
async def cmd_start(m: Message):
    waiting_input.pop(m.from_user.id, None)
    await m.answer(HELP, parse_mode="HTML", reply_markup=main_menu())


@dp.message(Command("ask"))
async def cmd_ask(m: Message, command: CommandObject):
    if not command.args:
        await m.answer("Использование: /ask <вопрос>")
        return
    if not OPENROUTER_API_KEY:
        await m.answer(
            "OpenRouter пока не настроен. Создай ключ на "
            "https://openrouter.ai/keys и добавь его в .env:\n"
            "<code>OPENROUTER_API_KEY=твой_ключ</code>",
            parse_mode="HTML",
        )
        return

    prompt = command.args.strip()
    if len(prompt) > 4000:
        await m.answer("Вопрос слишком длинный. Максимум — 4000 символов.")
        return

    status = await m.answer("🤖 Спрашиваю OpenRouter…")
    try:
        answer = await ask_openrouter(prompt)
    except APIStatusError as e:
        log.error("OpenRouter вернул HTTP %s", e.status_code)
        if e.status_code in (401, 403):
            message = "Ключ OpenRouter недействителен или не имеет доступа. Проверь его в .env."
        elif e.status_code == 402:
            message = (
                f"OpenRouter отклонил запрос к модели {html.escape(OPENROUTER_MODEL)}: "
                "для неё нужны доступные кредиты. Пополни баланс OpenRouter или укажи "
                "доступную бесплатную модель в .env через OPENROUTER_MODEL."
            )
        elif e.status_code == 429:
            message = "OpenRouter ограничил запрос: проверь лимит и баланс аккаунта."
        else:
            message = f"OpenRouter вернул ошибку HTTP {e.status_code}. Попробуй позже."
        await status.edit_text(message)
        return
    except APIConnectionError as e:
        log.warning("Не удалось подключиться к OpenRouter: %s", type(e).__name__)
        await status.edit_text("Не удалось подключиться к OpenRouter. Проверь интернет и попробуй позже.")
        return
    except APIError as e:
        log.error("Ошибка OpenRouter API: %s", type(e).__name__)
        await status.edit_text("Ошибка при запросе к OpenRouter. Попробуй позже.")
        return
    except RuntimeError as e:
        log.error("Пустой ответ OpenRouter: %s", e)
        await status.edit_text("Модель не вернула текстовый ответ. Попробуй задать вопрос иначе.")
        return

    if len(answer) > 3900:
        answer = answer[:3900].rstrip() + "\n\n… (ответ сокращён)"
    await status.edit_text(answer)


@dp.message(F.text.casefold() == "меню")
async def cmd_old_menu_button(m: Message):
    await cmd_start(m)


@dp.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    waiting_input.pop(c.from_user.id, None)
    await c.answer()
    await c.message.edit_text(HELP, parse_mode="HTML", reply_markup=main_menu())


# ---------------------------------------------------------------- скриншот / камера


@dp.message(Command("screenshot"))
async def cmd_screenshot(m: Message):
    try:
        png = await asyncio.to_thread(take_screenshot)
        await m.answer_document(BufferedInputFile(png, "screenshot.png"))
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Не удалось сделать скриншот: {e}")


@dp.callback_query(F.data == "screenshot")
async def cb_screenshot(c: CallbackQuery):
    await c.answer("Делаю скриншот…")
    try:
        png = await asyncio.to_thread(take_screenshot)
        await c.message.answer_document(BufferedInputFile(png, "screenshot.png"))
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось сделать скриншот по кнопке")
        await c.message.answer(f"Не удалось сделать скриншот: {e}")
        return
    await c.message.answer("📸 Готово.", reply_markup=main_menu())


async def send_webcam(target: Message):
    try:
        jpg = await asyncio.to_thread(webcam_photo)
        await target.answer_photo(BufferedInputFile(jpg, "webcam.jpg"), caption="📷 Веб-камера")
    except Exception as e:  # noqa: BLE001
        await target.answer(f"Не получилось снять с камеры: {e}")


@dp.message(Command("webcam"))
async def cmd_webcam(m: Message):
    await send_webcam(m)


@dp.callback_query(F.data == "webcam")
async def cb_webcam(c: CallbackQuery):
    await c.answer("Снимаю…")
    await send_webcam(c.message)
    await c.message.answer("Что дальше?", reply_markup=main_menu())


# ---------------------------------------------------------------- состояние / процессы


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    await m.answer(await asyncio.to_thread(system_stats), parse_mode="HTML")


@dp.callback_query(F.data == "stats")
async def cb_stats(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text(
        await asyncio.to_thread(system_stats),
        parse_mode="HTML",
        reply_markup=detail_menu("stats"),
    )


async def show_processes(c: CallbackQuery, sort: str = "cpu", page: int = 0) -> None:
    """Показывает страницу списка процессов с кнопками сортировки."""
    rows = await asyncio.to_thread(process_rows, None)
    rows = sort_rows(rows, sort)
    try:
        await c.message.edit_text(
            procs_text(rows, page, sort),
            parse_mode="HTML",
            reply_markup=procs_menu(rows, page, sort),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            log.warning("Не удалось обновить список процессов: %s", error)


@dp.message(Command("procs"))
async def cmd_procs(m: Message):
    rows = await asyncio.to_thread(process_rows, None)
    rows = sort_rows(rows, "cpu")
    await m.answer(
        procs_text(rows, 0, "cpu"),
        parse_mode="HTML",
        reply_markup=procs_menu(rows, 0, "cpu"),
    )


@dp.callback_query(F.data == "procs")
async def cb_procs(c: CallbackQuery):
    await c.answer("Собираю список…")
    await show_processes(c, "cpu", 0)


@dp.callback_query(F.data.startswith("procs:"))
async def cb_procs_nav(c: CallbackQuery):
    parts = c.data.split(":")
    sort = parts[1] if len(parts) > 1 and parts[1] in dict(PROC_SORTS) else "cpu"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    await c.answer()
    await show_processes(c, sort, page)


@dp.callback_query(F.data.startswith("kill:"))
async def cb_kill_ask(c: CallbackQuery):
    raw = c.data.split(":", 1)[1]
    if not raw.isdigit():
        await c.answer("Некорректный PID.", show_alert=True)
        return
    pid = int(raw)
    await c.answer()
    try:
        name = psutil.Process(pid).name()
    except psutil.Error:
        await c.message.edit_text("Этот процесс уже закрыт.", reply_markup=detail_menu("procs"))
        return
    await c.message.edit_text(
        f"❌ Закрыть <b>{html.escape(name)}</b> (PID {pid})?\nНесохранённые данные в ней пропадут.",
        parse_mode="HTML",
        reply_markup=confirm_kb(f"killok:{pid}", "procs"),
    )


@dp.callback_query(F.data.startswith("killok:"))
async def cb_kill(c: CallbackQuery):
    raw = c.data.split(":", 1)[1]
    if not raw.isdigit():
        await c.answer("Некорректный PID.", show_alert=True)
        return
    pid = int(raw)
    await c.answer()
    try:
        name = await asyncio.to_thread(kill_process, pid)
        text = f"✅ {html.escape(name)} закрыт."
        log_action(c.from_user.id, "kill", f"{name} (PID {pid})")
    except psutil.NoSuchProcess:
        text = "Этот процесс уже закрыт."
    except psutil.AccessDenied:
        text = "Нет прав закрыть этот процесс (нужен администратор)."
    except Exception as e:  # noqa: BLE001
        text = f"Не получилось: {html.escape(str(e))}"
    await c.message.edit_text(text, parse_mode="HTML", reply_markup=detail_menu("procs"))


@dp.message(Command("allprocs"))
async def cmd_allprocs(m: Message):
    rows = await asyncio.to_thread(process_rows, None)
    rows = sort_rows(rows, "name")
    await m.answer(
        procs_text(rows, 0, "name"),
        parse_mode="HTML",
        reply_markup=procs_menu(rows, 0, "name"),
    )


@dp.callback_query(F.data == "allprocs")
async def cb_allprocs(c: CallbackQuery):
    await c.answer()
    rows = await asyncio.to_thread(process_rows, None)
    rows = sort_rows(rows, "name")
    await c.message.edit_text(
        procs_text(rows, 0, "name"),
        parse_mode="HTML",
        reply_markup=procs_menu(rows, 0, "name"),
    )


def close_all_processes() -> dict:
    """Закрыть все пользовательские процессы (не системные)."""
    result = {"success": [], "errors": [], "protected": []}
    protected = protected_pids()
    for p in psutil.process_iter(["name", "pid"]):
        if p.pid in (0, 4):
            continue
        try:
            name = p.name()
            if name.lower() in PROTECTED or p.pid in protected:
                result["protected"].append({"pid": p.pid, "name": name})
                continue
            kill_process(p.pid)
            result["success"].append({"pid": p.pid, "name": name})
        except psutil.NoSuchProcess:
            result["errors"].append({"pid": p.pid, "name": "?", "reason": "уже закрыт"})
        except psutil.AccessDenied:
            result["errors"].append({"pid": p.pid, "name": "?", "reason": "нет прав"})
        except Exception as e:  # noqa: BLE001
            result["errors"].append({"pid": p.pid, "name": "?", "reason": str(e)})
    return result


CLOSEALL_WARNING = (
    "⚠️ <b>Закрыть все пользовательские процессы?</b>\n"
    "Несохранённые данные в программах пропадут. "
    "Системные процессы, сам бот и панель управления останутся работать."
)


@dp.message(Command("closeall"))
async def cmd_closeall(m: Message):
    if not ALLOW_SHELL:
        await m.answer("Эта функция отключена (ALLOW_SHELL=false)", reply_markup=main_menu())
        return
    await m.answer(
        CLOSEALL_WARNING,
        parse_mode="HTML",
        reply_markup=confirm_kb("closeall_ok", "menu"),
    )


@dp.callback_query(F.data == "closeall")
async def cb_closeall(c: CallbackQuery):
    """Только запрашивает подтверждение — ничего не закрывает."""
    await c.answer()
    if not ALLOW_SHELL:
        await c.message.edit_text(
            "Эта функция отключена (ALLOW_SHELL=false).",
            reply_markup=main_menu(),
        )
        return
    await c.message.edit_text(
        CLOSEALL_WARNING,
        parse_mode="HTML",
        reply_markup=confirm_kb("closeall_ok", "menu"),
    )


@dp.callback_query(F.data == "closeall_ok")
async def cb_closeall_ok(c: CallbackQuery):
    await c.answer("Закрываю процессы…")
    if not ALLOW_SHELL:
        await c.message.edit_text(
            "Эта функция отключена (ALLOW_SHELL=false).",
            reply_markup=main_menu(),
        )
        return
    log_action(c.from_user.id, "closeall")
    try:
        result = await asyncio.to_thread(close_all_processes)
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось закрыть процессы")
        await c.message.edit_text(
            f"❌ Ошибка: {html.escape(str(e))}",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    success = result["success"]
    protected = result["protected"]
    errors = result["errors"]
    text = (
        f"⚠️ <b>Закрыто процессов:</b> {len(success)}\n"
        f"🛡️ Защищённых процессов: {len(protected)}\n"
        f"❌ Ошибок: {len(errors)}\n"
    )
    if protected:
        text += "\n🏛 <b>Системные процессы оставлены:</b>\n"
        for item in protected[:5]:
            text += f"• {html.escape(str(item['name']))} (PID {item['pid']})\n"
        if len(protected) > 5:
            text += f"… и ещё {len(protected) - 5}\n"
    if errors:
        text += "\n⚠️ <b>Ошибки:</b>\n"
        for item in errors[:3]:
            name = html.escape(str(item.get("name", "?")))
            pid = item.get("pid", "?")
            reason = html.escape(str(item.get("reason", "неизвестно")))
            text += f"• {name} (PID {pid}): {reason}\n"
        if len(errors) > 3:
            text += f"… и ещё {len(errors) - 3}\n"
    if success:
        text += f"\n✅ Успешно закрыто {len(success)} процессов."
    await c.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu())

 # ---------------------------------------------------------------- звук
# ---------------------------------------------------------------- звук


@dp.callback_query(F.data == "sound")
async def cb_sound(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("🎵 <b>Звук и музыка</b>", parse_mode="HTML", reply_markup=sound_menu())


SOUND_HINTS = {
    "vol_up": "🔊 Громче", "vol_down": "🔉 Тише", "mute": "🔇 Звук вкл/выкл",
    "play": "⏯", "next": "⏭ Следующий", "prev": "⏮ Предыдущий",
}


@dp.callback_query(F.data.startswith("key:"))
async def cb_key(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    if name not in VK:
        await c.answer()
        return
    try:
        times = 5 if name in ("vol_up", "vol_down") else 1  # 5 нажатий ≈ 10%
        await asyncio.to_thread(press_media_key, name, times)
        await c.answer(SOUND_HINTS[name])
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


# ---------------------------------------------------------------- программы


@dp.callback_query(F.data == "apps")
async def cb_apps(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("🚀 <b>Что запустить?</b>", parse_mode="HTML", reply_markup=apps_menu())


@dp.callback_query(F.data.startswith("app:"))
async def cb_app(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    if key not in APPS:
        await c.answer()
        return
    try:
        await asyncio.to_thread(launch_app, key)
        await c.answer(f"Запускаю {APPS[key][0]}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)
        return
    log_action(c.from_user.id, "launch", APPS[key][0])
    # После запуска показываем, что можно делать внутри программы
    if key == "steam":
        await show_steam_games(c, 0)
    elif key == "chrome":
        await c.message.edit_text("🌐 <b>Chrome</b> запускается — что открыть?", parse_mode="HTML",
                                  reply_markup=sites_menu())
    elif key == "telegram":
        if TG_LINKS:
            await c.message.edit_text("✈️ <b>Telegram</b> запускается — какой чат открыть?", parse_mode="HTML",
                                      reply_markup=tg_menu())
        else:
            await c.message.edit_text(
                "✈️ <b>Telegram</b> запускается.\n"
                "Список избранных чатов пуст — его можно задать в файле bot.py (TG_LINKS).",
                parse_mode="HTML",
                reply_markup=kb([[b(text="⬅️ Программы", callback_data="apps"), BACK]]),
            )
    elif key == "music":
        await c.message.edit_text("🎵 <b>Яндекс Музыка</b> запускается — управление музыкой:",
                                  parse_mode="HTML", reply_markup=sound_menu())
    else:
        await c.message.edit_text(
            f"✅ <b>{html.escape(APPS[key][0])}</b> запускается.",
            parse_mode="HTML",
            reply_markup=kb([[b(text="⬅️ Программы", callback_data="apps"), BACK]]),
        )


async def show_steam_games(c: CallbackQuery, page: int):
    try:
        games = await asyncio.to_thread(steam_games)
    except Exception as e:  # noqa: BLE001
        await c.message.edit_text(f"Не получилось найти игры: {e}", reply_markup=apps_menu())
        return
    if not games:
        await c.message.edit_text("🎮 В Steam нет установленных игр.", reply_markup=apps_menu())
        return
    await c.message.edit_text(
        f"🎮 <b>Игры в Steam</b> ({len(games)} шт.)\nСверху — в которые играл недавно. Нажми, чтобы запустить:",
        parse_mode="HTML",
        reply_markup=steam_menu(games, page),
    )


@dp.callback_query(F.data == "sgk")
async def cb_steam_running(c: CallbackQuery):
    await c.answer()
    try:
        running = await asyncio.to_thread(running_steam_games)
    except Exception as e:  # noqa: BLE001
        await c.message.edit_text(f"Не получилось: {e}", reply_markup=apps_menu())
        return
    back = [b(text="🔄 Обновить", callback_data="sgk"), b(text="🎮 К играм", callback_data="sgp:0")]
    if not running:
        await c.message.edit_text("🎮 Сейчас ни одна игра из Steam не запущена.", reply_markup=kb([back, [BACK]]))
        return
    rows = [[b(text=f"⛔ {folder[:22]} ({name})"[:60], callback_data=f"kill:{pid}")]
            for pid, name, folder in running[:10]]
    rows += [back, [BACK]]
    await c.message.edit_text("⛔ <b>Какую игру закрыть?</b>\nНесохранённый прогресс в игре пропадёт.",
                              parse_mode="HTML", reply_markup=kb(rows))


@dp.callback_query(F.data.startswith("site:"))
async def cb_site(c: CallbackQuery):
    i = int(c.data.split(":", 1)[1])
    if not 0 <= i < len(SITES):
        await c.answer()
        return
    title, url = SITES[i]
    try:
        await asyncio.to_thread(open_site, url)
        await c.answer(f"Открываю {title}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.callback_query(F.data.startswith("tgl:"))
async def cb_tg_link(c: CallbackQuery):
    i = int(c.data.split(":", 1)[1])
    if not 0 <= i < len(TG_LINKS):
        await c.answer()
        return
    title, link = TG_LINKS[i]
    try:
        await asyncio.to_thread(open_target, link)
        await c.answer(f"Открываю {title}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.callback_query(F.data.startswith("sgp:"))
async def cb_steam_page(c: CallbackQuery):
    await c.answer()
    await show_steam_games(c, int(c.data.split(":", 1)[1]))


@dp.callback_query(F.data.startswith("sg:"))
async def cb_steam_game(c: CallbackQuery):
    appid = c.data.split(":", 1)[1]
    if not appid.isdigit():
        await c.answer()
        return
    try:
        await asyncio.to_thread(run_steam_game, appid)
        name = next((n for a, n, _ in await asyncio.to_thread(steam_games) if a == appid), "игру")
        await c.answer(f"Запускаю {name} 🎮", show_alert=False)
        await c.message.answer(f"🎮 Запускаю <b>{html.escape(name)}</b>. Steam может пару секунд готовиться.",
                               parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.message(Command("open"))
async def cmd_open(m: Message, command: CommandObject):
    if not command.args:
        await m.answer("Использование: /open <путь | программа | url>")
        return
    try:
        await asyncio.to_thread(open_target, command.args.strip())
        await m.answer(f"Открыл: {command.args}")
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Ошибка: {e}")


# ---------------------------------------------------------------- сообщение на экран


async def popup_and_report(m: Message, text: str):
    await m.answer("💬 Показал сообщение на экране. Сообщу, когда его закроют.")
    try:
        await asyncio.to_thread(show_message_box, text)
        await m.answer(f"👀 Сообщение прочитали и закрыли ({datetime.now():%H:%M}).")
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Не получилось показать сообщение: {e}")


@dp.message(Command("msg"))
async def cmd_msg(m: Message, command: CommandObject):
    if not command.args:
        waiting_input[m.from_user.id] = "msg"
        await m.answer("Напиши текст, который показать на экране ПК:")
        return
    in_background(popup_and_report(m, command.args))


@dp.callback_query(F.data == "msg")
async def cb_msg(c: CallbackQuery):
    await c.answer()
    waiting_input[c.from_user.id] = "msg"
    await c.message.edit_text("💬 Напиши текст, который показать на экране ПК:",
                              reply_markup=kb([[BACK]]))


# ---------------------------------------------------------------- буфер обмена


async def send_clipboard(target: Message):
    try:
        text = await asyncio.to_thread(clipboard_get)
    except Exception as e:  # noqa: BLE001
        await target.answer(f"Не получилось прочитать буфер: {e}")
        return
    if not text:
        await target.answer("📎 В буфере нет текста.", reply_markup=clip_menu())
    elif len(text) > 3500:
        await target.answer_document(BufferedInputFile(text.encode("utf-8"), "clipboard.txt"))
    else:
        await target.answer(f"📎 В буфере:\n<pre>{html.escape(text)}</pre>", parse_mode="HTML",
                            reply_markup=clip_menu())


@dp.message(Command("clip"))
async def cmd_clip(m: Message):
    await send_clipboard(m)


@dp.callback_query(F.data == "clip")
async def cb_clip(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("📎 <b>Буфер обмена ПК</b>", parse_mode="HTML", reply_markup=clip_menu())


@dp.callback_query(F.data == "clip_get")
async def cb_clip_get(c: CallbackQuery):
    await c.answer()
    await send_clipboard(c.message)


@dp.callback_query(F.data == "clip_set")
async def cb_clip_set(c: CallbackQuery):
    await c.answer()
    waiting_input[c.from_user.id] = "clip"
    await c.message.edit_text("✏️ Пришли текст — положу его в буфер обмена ПК (потом Ctrl+V):",
                              reply_markup=kb([[BACK]]))


# ---------------------------------------------------------------- таймер и питание


@dp.callback_query(F.data == "timer")
async def cb_timer(c: CallbackQuery):
    await c.answer()
    now = ""
    if pending_info and pending_power and not pending_power.done():
        now = f"\nСейчас запланировано: {pending_info}"
    await c.message.edit_text(f"⏲ <b>Выключить ПК через…</b>{now}", parse_mode="HTML",
                              reply_markup=timer_menu())


@dp.callback_query(F.data.startswith("tmr:"))
async def cb_timer_set(c: CallbackQuery):
    if OS not in POWER_COMMANDS:
        await c.answer("Не поддерживается на этой ОС", show_alert=True)
        return
    minutes = int(c.data.split(":", 1)[1])
    at = schedule_power("shutdown", minutes * 60)
    await c.answer("Таймер поставлен")
    await c.message.edit_text(
        f"⏲ ПК выключится в <b>{at}</b> (через {minutes} мин).\n"
        "Отменить — «🛑 Отмена таймера» или /cancel.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data.startswith("ask:"))
async def cb_ask(c: CallbackQuery):
    action = c.data.split(":", 1)[1]
    await c.answer()
    if action not in ACTION_NAMES or action not in POWER_COMMANDS.get(OS, {}):
        await c.message.edit_text("Эта операция не поддерживается на твоей ОС.",
                                  reply_markup=main_menu())
        return
    await c.message.edit_text(
        f"⚠️ <b>{ACTION_NAMES[action]}</b>\nТочно выполнить действие?",
        parse_mode="HTML",
        reply_markup=confirm_kb(f"do:{action}"),
    )


@dp.callback_query(F.data == "cancel_ask")
async def cb_cancel_ask(c: CallbackQuery):
    await c.answer("Отменено")
    await c.message.edit_text("Действие отменено.", reply_markup=main_menu())


@dp.callback_query(F.data.startswith("do:"))
async def cb_do(c: CallbackQuery):
    action = c.data.split(":", 1)[1]
    await c.answer()
    if action not in POWER_COMMANDS.get(OS, {}):
        await c.message.edit_text("Эта операция не поддерживается на твоей ОС.",
                                  reply_markup=main_menu())
        return
    delay = POWER_DELAY if action in ("shutdown", "reboot") else 3
    schedule_power(action, delay)
    await c.message.edit_text(
        f"⏳ <b>{ACTION_NAMES[action]}</b> через {delay} сек.\n"
        "Чтобы отменить, нажми «Отмена таймера» или отправь /cancel.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message):
    if await cancel_pending_power():
        await m.answer("Запланированное действие отменено ✅")
    else:
        await m.answer("Нечего отменять.")


@dp.callback_query(F.data == "cancel")
async def cb_cancel(c: CallbackQuery):
    await c.answer()
    cancelled = await cancel_pending_power()
    text = "✅ Запланированное действие отменено." if cancelled else "ℹ️ Нет запланированных действий."
    await c.message.edit_text(text, reply_markup=main_menu())


# ---------------------------------------------------------------- перезапуск бота


@dp.callback_query(F.data == "restart_ask")
async def cb_restart_ask(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("♻️ Перезапустить бота? Это займёт секунд 10.",
                              reply_markup=confirm_kb("restart", "menu"))


@dp.callback_query(F.data == "restart")
async def cb_restart(c: CallbackQuery):
    await c.answer()
    if os.getenv("PCBOT_GUI_CHILD") == "1":
        await c.message.edit_text(
            "Перезапусти бота через локальную панель управления.",
            reply_markup=main_menu(),
        )
        return
    if IS_WIN and getattr(sys, "frozen", False):
        await c.message.edit_text("♻️ Перезапускаюсь… Через несколько секунд придёт «Бот запущен».")
        subprocess.Popen(
            [sys.executable],
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        await asyncio.sleep(1)
        os._exit(0)

    launcher = BASE_DIR / "start_bot.bat"
    if not (IS_WIN and launcher.exists()):
        await c.message.edit_text("Перезапуск работает только на Windows с start_bot.bat.",
                                  reply_markup=main_menu())
        return
    await c.message.edit_text("♻️ Перезапускаюсь… Через несколько секунд придёт «Бот запущен».")
    subprocess.Popen(f'start "PC bot" /min cmd /c "{launcher}"', shell=True, cwd=str(BASE_DIR))
    await asyncio.sleep(1)
    os._exit(0)


# ---------------------------------------------------------------- оболочка и файлы


@dp.message(Command("run"))
async def cmd_run(m: Message, command: CommandObject):
    if not ALLOW_SHELL:
        await m.answer("Выполнение команд выключено. Включи ALLOW_SHELL=true в .env.")
        return
    if not command.args:
        await m.answer("Использование: /run <команда>")
        return
    payload = command.args.strip()
    token = store_action(m.from_user.id, "run", payload)
    await m.answer(
        f"💻 <b>Выполнить команду?</b>\n<code>{html.escape(payload)}</code>\n"
        "Команда выполнится в оболочке от твоего имени.",
        parse_mode="HTML",
        reply_markup=kb([
            [b(text="✅ Выполнить", callback_data=f"act:run:{token}"),
             b(text="↩️ Отмена", callback_data="menu")]
        ]),
    )


@dp.message(Command("get"))
async def cmd_get(m: Message, command: CommandObject):
    if not ALLOW_FILES:
        await m.answer("Отправка файлов выключена. Включи ALLOW_FILES=true в .env.")
        return
    if not command.args:
        await m.answer("Использование: /get <путь к файлу>")
        return
    path = Path(command.args.strip().strip('"')).expanduser()
    if not path.is_file():
        await m.answer("Файл не найден.")
        return
    size = path.stat().st_size
    if size > 50 * 2**20:
        await m.answer("Файл больше 50 МБ — Telegram не пропустит.")
        return
    token = store_action(m.from_user.id, "get", str(path))
    await m.answer(
        f"📤 <b>Отправить файл?</b>\n<code>{html.escape(str(path))}</code>\n"
        f"Размер: {size / 1024:.1f} КБ",
        parse_mode="HTML",
        reply_markup=kb([
            [b(text="✅ Отправить", callback_data=f"act:get:{token}"),
             b(text="↩️ Отмена", callback_data="menu")]
        ]),
    )


@dp.callback_query(F.data.startswith("act:"))
async def cb_pending_action(c: CallbackQuery):
    """Подтверждение отложенного действия: запуск команды или отправка файла."""
    parts = c.data.split(":")
    if len(parts) != 3:
        await c.answer("Некорректное действие.", show_alert=True)
        return
    action, token = parts[1], parts[2]
    pending = take_action(token, c.from_user.id)
    if pending is None:
        await c.answer("Действие устарело или уже выполнено.", show_alert=True)
        return
    kind, payload = pending
    await c.answer()

    if kind == "run":
        if not ALLOW_SHELL:
            await c.message.edit_text("Выполнение команд выключено.", reply_markup=main_menu())
            return
        log_action(c.from_user.id, "run", payload)
        await c.message.edit_text("⏳ Выполняю…", reply_markup=main_menu())
        try:
            proc = await asyncio.create_subprocess_shell(
                payload,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await c.message.answer("Таймаут 30 с, процесс убит.", reply_markup=main_menu())
                return
            text = out.decode(CONSOLE_ENC, errors="replace").strip() or "(пустой вывод)"
            if len(text) > 3500:
                await c.message.answer_document(BufferedInputFile(text.encode(), "output.txt"))
            else:
                await c.message.answer(
                    f"<pre>{html.escape(text)}</pre>", parse_mode="HTML", reply_markup=main_menu()
                )
        except Exception as e:  # noqa: BLE001
            await c.message.answer(f"Ошибка: {html.escape(str(e))}", reply_markup=main_menu())
        return

    if kind == "get":
        if not ALLOW_FILES:
            await c.message.edit_text("Отправка файлов выключена.", reply_markup=main_menu())
            return
        path = Path(payload)
        log_action(c.from_user.id, "get", str(path))
        if not path.is_file():
            await c.message.answer("Файл не найден.", reply_markup=main_menu())
            return
        if path.stat().st_size > 50 * 2**20:
            await c.message.answer("Файл больше 50 МБ — Telegram не пропустит.", reply_markup=main_menu())
            return
        try:
            await c.message.answer_document(FSInputFile(path))
        except Exception as e:  # noqa: BLE001
            await c.message.answer(f"Не получилось отправить: {html.escape(str(e))}", reply_markup=main_menu())
        return

    await c.answer("Неизвестное действие.", show_alert=True)


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).name).strip(" .")
    return name or "file"


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    stem, suffix, i = path.stem, path.suffix, 1
    while path.exists():
        path = folder / f"{stem} ({i}){suffix}"
        i += 1
    return path


@dp.message(F.document | F.photo | F.video | F.audio | F.voice | F.video_note)
async def receive_file(m: Message):
    # Файл тоже завершает ожидание ввода — иначе следующий текст уйдёт
    # в буфер обмена или в окно сообщения.
    waiting_input.pop(m.from_user.id, None)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if m.document:
        obj, name = m.document, m.document.file_name or f"file_{stamp}"
    elif m.photo:
        obj, name = m.photo[-1], f"photo_{stamp}.jpg"
    elif m.video:
        obj, name = m.video, m.video.file_name or f"video_{stamp}.mp4"
    elif m.audio:
        obj, name = m.audio, m.audio.file_name or f"audio_{stamp}.mp3"
    elif m.voice:
        obj, name = m.voice, f"voice_{stamp}.ogg"
    else:
        obj, name = m.video_note, f"circle_{stamp}.mp4"
    if (obj.file_size or 0) > 20 * 2**20:
        await m.answer("Файл больше 20 МБ — бот такие скачать не может (ограничение Telegram).")
        return
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = unique_path(DOWNLOADS, safe_name(name))
    try:
        await bot.download(obj.file_id, destination=path)
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Не получилось сохранить: {e}")
        return
    await m.answer(f"✅ Сохранил на ПК:\n<code>{html.escape(str(path))}</code>", parse_mode="HTML")


# ---------------------------------------------------------------- текст после кнопки


@dp.message(F.text)
async def on_text(m: Message):
    mode = waiting_input.pop(m.from_user.id, None)
    if mode == "msg":
        in_background(popup_and_report(m, m.text))
    elif mode == "clip":
        try:
            await asyncio.to_thread(clipboard_set, m.text)
            await m.answer("✅ Текст в буфере обмена ПК — можно вставлять Ctrl+V.",
                           reply_markup=main_menu())
        except Exception as e:  # noqa: BLE001
            await m.answer(f"Не получилось записать в буфер: {e}")
    else:
        await m.answer("Не понял 🤔 Выбери действие в меню:", reply_markup=main_menu())


# ---------------------------------------------------------------- запуск


async def main():
    me = await bot.get_me()
    log.info("Бот @%s запущен. Разрешённые id: %s", me.username, sorted(ALLOWED))
    commands = [
        BotCommand(command="menu", description="Открыть панель управления"),
        BotCommand(command="help", description="Справка по командам"),
        BotCommand(command="screenshot", description="Сделать скриншот"),
        BotCommand(command="webcam", description="Фото с веб-камеры"),
        BotCommand(command="stats", description="Состояние компьютера"),
        BotCommand(command="procs", description="Процессы: сортировка и страницы"),
        BotCommand(command="allprocs", description="Все процессы по имени"),
        BotCommand(command="ask", description="Задать вопрос OpenRouter"),
        BotCommand(command="msg", description="Сообщение на экран ПК"),
        BotCommand(command="clip", description="Что в буфере обмена"),
        BotCommand(command="open", description="Открыть путь, программу или URL"),
        BotCommand(command="cancel", description="Отменить запланированное действие"),
    ]
    if ALLOW_SHELL:
        commands.append(BotCommand(command="closeall", description="Закрыть все процессы"))
        commands.append(BotCommand(command="run", description="Выполнить команду оболочки"))
    if ALLOW_FILES:
        commands.append(BotCommand(command="get", description="Получить файл"))
    await bot.set_my_commands(commands)
    for uid in ALLOWED:
        try:
            await bot.send_message(uid, f"🟢 Бот запущен.\n\n{HELP}", parse_mode="HTML",
                                   reply_markup=main_menu())
        except Exception:
            log.exception("Не удалось отправить меню пользователю id=%s", uid)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
