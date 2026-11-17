"""Клавиатуры, меню и тексты интерфейса бота."""

import html
import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import ALLOW_AUDIO, ALLOW_FILES, ALLOW_INPUT, ALLOW_SHELL, RECORD_SECONDS
from .processes import format_rows
from .security import sign_data
from .windows_api import APPS, SITES, TG_LINKS


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
    rows = [
        [b(text="📸 Скриншот", callback_data="screenshot"),
         b(text="📷 Веб-камера", callback_data="webcam")],
        [b(text="📊 Состояние ПК", callback_data="stats"),
         b(text="📋 Процессы", callback_data="procs")],
        [b(text="🖥 Монитор и окна", callback_data="mon"),
         b(text="🎛 Управление", callback_data="ctl")],
        [b(text="📁 Файлы", callback_data="files"),
         b(text="🚀 Программы", callback_data="apps")],
        [b(text="💬 Сообщение", callback_data="msg"),
         b(text="📎 Буфер обмена", callback_data="clip")],
        [b(text="🎵 Звук и музыка", callback_data="sound"),
         b(text="⏲ Таймер", callback_data="timer")],
    ]
    if ALLOW_AUDIO:
        rows.append([b(text="🎬 Запись экрана", callback_data="rec")])
    rows.append([b(text="🔒 Заблокировать", callback_data="ask:lock"),
                 b(text="😴 Спящий режим", callback_data="ask:sleep")])
    rows.append([b(text="🔄 Перезагрузить", callback_data="ask:reboot"),
                 b(text="⏻ Выключить", callback_data="ask:shutdown")])
    if ALLOW_SHELL:
        rows.append([b(text="⚠️ Закрыть все процессы", callback_data="closeall")])
    rows.append([b(text="♻️ Перезапустить бота", callback_data="restart_ask")])
    return kb(rows)


def monitor_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="🪟 Окна", callback_data="wl"), b(text="🌐 Сеть", callback_data="net")],
        [b(text="🔌 Автозапуск", callback_data="startup"),
         b(text="📦 Программы", callback_data="installed")],
        [b(text="🛠 Службы", callback_data="services"),
         b(text="🖥 Состояние ПК", callback_data="stats")],
        [BACK],
    ])


def control_menu() -> InlineKeyboardMarkup:
    rows = [
        [b(text="🔉 Тише", callback_data="vol:-1"),
         b(text="🔇 Mute", callback_data="mute"),
         b(text="🔊 Громче", callback_data="vol:1")],
        [b(text="🔅 Темнее", callback_data="br:-10"),
         b(text="🔆 Ярче", callback_data="br:10")],
        [b(text="🌙 Погасить экран", callback_data="monoff"),
         b(text="🖼 Сменить обои", callback_data="wallpaper")],
        [b(text="🗑 Очистить корзину", callback_data="recycl"),
         b(text="🌐 Сбросить DNS", callback_data="flushdns")],
        [b(text="🎵 Мультимедиа", callback_data="sound"),
         b(text="⚙️ Параметры Windows", callback_data="settings")],
    ]
    if ALLOW_INPUT:
        rows.append([b(text="🖱 Мышь и клавиатура", callback_data="input")])
    rows.append([BACK])
    return kb(rows)


def input_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text="🖱 Левый клик", callback_data="inp:click:left"),
         b(text="🖱 Правый клик", callback_data="inp:click:right")],
        [b(text="⬆️ Прокрутить вверх", callback_data="inp:scroll:1"),
         b(text="⬇️ Прокрутить вниз", callback_data="inp:scroll:-1")],
        [b(text="⌨️ Напечатать текст", callback_data="inp:type"),
         b(text="🎯 Клик по координатам", callback_data="inp:pos")],
        [b(text="⌨️ Сочетание клавиш", callback_data="inp:combo"),
         b(text="🎯 Где курсор", callback_data="inp:where")],
        [b(text="⬅️ Управление", callback_data="ctl"), BACK],
    ])


def recording_menu() -> InlineKeyboardMarkup:
    return kb([
        [b(text=f"🎬 Экран {RECORD_SECONDS} с", callback_data="rec:screen"),
         b(text=f"🎙 Микрофон {RECORD_SECONDS} с", callback_data="rec:mic")],
        [b(text="🎬 Экран 30 с", callback_data="rec:screen30"),
         b(text="🎙 Микрофон 30 с", callback_data="rec:mic30")],
        [BACK],
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


SOUND_HINTS = {
    "vol_up": "🔊 Громче", "vol_down": "🔉 Тише", "mute": "🔇 Звук вкл/выкл",
    "play": "⏯", "next": "⏭ Следующий", "prev": "⏮ Предыдущий",
}


CLOSEALL_WARNING = (
    "⚠️ <b>Закрыть все пользовательские процессы?</b>\n"
    "Несохранённые данные в программах пропадут. "
    "Системные процессы, сам бот и панель управления останутся работать."
)
