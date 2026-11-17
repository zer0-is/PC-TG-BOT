"""Файловый менеджер в Telegram: папки, поиск, архивы и корзина."""

import asyncio
import html
import tempfile
from pathlib import Path

from aiogram import F
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

from ..files_mgr import (
    delete_to_recycle,
    drives,
    human_size,
    list_dir,
    make_zip,
    parent_of,
    search_files,
)
from ..runtime import dp, waiting_input
from ..ui import BACK, b, kb

MAX_SEND_BYTES = 48 * 1024**2
PAGE_SIZE = 12

# Что показано пользователю сейчас: папка, список записей и режим.
file_state: dict[int, dict] = {}


def _state(user_id: int) -> dict:
    return file_state.setdefault(
        user_id, {"cwd": None, "entries": [], "kind": "drives", "query": None}
    )


def _remember(
    user_id: int,
    base: Path | None,
    entries: list[dict],
    kind: str,
    query: str | None = None,
) -> None:
    file_state[user_id] = {
        "cwd": base,
        "entries": entries,
        "kind": kind,
        "query": query,
    }


def _entry(user_id: int, index: int) -> dict | None:
    entries = _state(user_id)["entries"]
    if 0 <= index < len(entries):
        return entries[index]
    return None


def render_entries(entries: list[dict], kind: str) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки для списка файлов или результатов поиска."""
    rows = []
    for index, entry in enumerate(entries):
        name = entry["name"][:48]
        if entry["is_dir"]:
            rows.append([
                b(text=f"📁 {name}", callback_data=f"files:o:{index}"),
                b(text="🗜", callback_data=f"files:x:{index}"),
            ])
        else:
            rows.append([
                b(text=f"📄 {name}", callback_data=f"files:d:{index}"),
                b(text="🗜", callback_data=f"files:x:{index}"),
                b(text="🗑", callback_data=f"files:del:{index}"),
            ])
    if kind == "search":
        nav = [
            b(text="🔄 Повторить", callback_data="files:r"),
            b(text="💾 Диски", callback_data="files:u"),
            b(text="🔎 Другой запрос", callback_data="files:s"),
        ]
    else:
        nav = [
            b(text="⬆️ Вверх", callback_data="files:p"),
            b(text="💾 Диски", callback_data="files:u"),
            b(text="🔄", callback_data="files:r"),
            b(text="🔎 Поиск", callback_data="files:s"),
        ]
    rows.append(nav)
    rows.append([BACK])
    return "\n".join(render_lines(entries, kind)), kb(rows)


def render_lines(entries: list[dict], kind: str) -> list[str]:
    lines = [
        "🔎 <b>Результаты поиска</b>" if kind == "search" else "📁 <b>Файлы</b>",
    ]
    if not entries:
        lines.append("\nНичего не найдено." if kind == "search" else "\nПапка пуста.")
        return lines
    dirs = sum(1 for entry in entries if entry["is_dir"])
    files = len(entries) - dirs
    lines.append(f"\n📁 Папок: {dirs} · 📄 Файлов: {files}")
    total = sum(entry["size"] for entry in entries if not entry["is_dir"])
    lines.append(f"💾 Размер файлов: {human_size(total)}")
    lines.append("\nИмя — скачать, 🗜 — архив, 🗑 — в корзину.")
    return lines


def render_body(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    state = _state(user_id)
    title_entries = state["entries"]
    text, markup = render_entries(title_entries, state["kind"])
    location = state.get("cwd")
    if location is not None:
        header = f"\n<code>{html.escape(str(location))}</code>"
        text = text.replace("\n", f"{header}\n", 1)
    if state["kind"] == "search" and state.get("query"):
        text = text.replace(
            "\n", f"\nЗапрос: <b>{html.escape(state['query'])}</b>\n", 1
        )
    return text, markup


def render_drives() -> tuple[str, InlineKeyboardMarkup]:
    roots = drives()
    rows = [
        [b(text=f"💾 {root}", callback_data=f"files:v:{index}")]
        for index, root in enumerate(roots)
    ]
    if not roots:
        rows.append([b(text="⬅️ В меню", callback_data="menu")])
    else:
        rows.append([
            b(text="🔎 Поиск", callback_data="files:s"),
            b(text="🏠 Домашняя папка", callback_data="files:h"),
        ])
        rows.append([BACK])
    return "💾 <b>Диски</b>\n\nВыбери диск, чтобы открыть его.", kb(rows)


async def _edit(c: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if isinstance(c.message, Message):
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


async def _show_dir(c: CallbackQuery, path: Path) -> None:
    entries = await asyncio.to_thread(list_dir, path, PAGE_SIZE)
    _remember(c.from_user.id, path, entries, "dir")
    text, markup = render_body(c.from_user.id)
    await _edit(c, text, markup)


async def _show_search(c: CallbackQuery, base: Path, query: str) -> None:
    found = await asyncio.to_thread(search_files, base, query, 20)
    entries = [
        {"name": path.name, "is_dir": path.is_dir(), "size": 0, "path": path}
        for path in found
    ]
    _remember(c.from_user.id, base, entries, "search", query)
    text, markup = render_body(c.from_user.id)
    await _edit(c, text, markup)


@dp.callback_query(F.data.in_({"files", "files:u"}))
async def cb_files(c: CallbackQuery):
    await c.answer()
    text, markup = render_drives()
    await _edit(c, text, markup)


@dp.callback_query(F.data.startswith("files:v:"))
async def cb_files_drive(c: CallbackQuery):
    index = int(c.data.rsplit(":", 1)[1])
    roots = drives()
    if not 0 <= index < len(roots):
        await c.answer("Список дисков изменился — открой заново.", show_alert=True)
        return
    await c.answer()
    await _show_dir(c, roots[index])


@dp.callback_query(F.data == "files:h")
async def cb_files_home(c: CallbackQuery):
    await c.answer()
    await _show_dir(c, Path.home())


@dp.callback_query(F.data.startswith("files:o:"))
async def cb_files_open(c: CallbackQuery):
    entry = _entry(c.from_user.id, int(c.data.rsplit(":", 1)[1]))
    if entry is None or not entry["is_dir"]:
        await c.answer("Папка не найдена — обнови список.", show_alert=True)
        return
    await c.answer()
    await _show_dir(c, entry["path"])


@dp.callback_query(F.data == "files:p")
async def cb_files_parent(c: CallbackQuery):
    state = _state(c.from_user.id)
    parent = parent_of(state["cwd"]) if state.get("cwd") is not None else None
    if parent is None:
        await c.answer()
        text, markup = render_drives()
        await _edit(c, text, markup)
        return
    await c.answer()
    await _show_dir(c, parent)


@dp.callback_query(F.data == "files:r")
async def cb_files_refresh(c: CallbackQuery):
    state = _state(c.from_user.id)
    if state["kind"] == "search" and state.get("query") and state.get("cwd"):
        await c.answer("Ищу снова…")
        await _show_search(c, state["cwd"], state["query"])
        return
    if state.get("cwd") is None:
        await c.answer()
        text, markup = render_drives()
        await _edit(c, text, markup)
        return
    await c.answer()
    await _show_dir(c, state["cwd"])


@dp.callback_query(F.data == "files:s")
async def cb_files_search(c: CallbackQuery):
    waiting_input[c.from_user.id] = "search"
    await c.answer()
    base = _state(c.from_user.id).get("cwd") or Path.home()
    if isinstance(c.message, Message):
        await c.message.answer(
            f"🔎 Пришли часть имени файла — поищу в <code>{html.escape(str(base))}</code>.",
            parse_mode="HTML",
        )


@dp.callback_query(F.data.startswith("files:d:"))
async def cb_files_download(c: CallbackQuery):
    entry = _entry(c.from_user.id, int(c.data.rsplit(":", 1)[1]))
    if entry is None or entry["is_dir"]:
        await c.answer("Файл не найден — обнови список.", show_alert=True)
        return
    path = entry["path"]
    try:
        size = path.stat().st_size
    except OSError as error:
        await c.answer(f"Не удалось прочитать файл: {error}", show_alert=True)
        return
    if size > MAX_SEND_BYTES:
        await c.answer(
            f"Файл {human_size(size)} — Telegram не примет больше 48 МБ.",
            show_alert=True,
        )
        return
    await c.answer("Отправляю…")
    if not isinstance(c.message, Message):
        return
    await c.message.answer_document(
        FSInputFile(path),
        caption=f"📄 <b>{html.escape(path.name)}</b>\n{human_size(size)}",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("files:x:"))
async def cb_files_zip(c: CallbackQuery):
    entry = _entry(c.from_user.id, int(c.data.rsplit(":", 1)[1]))
    if entry is None:
        await c.answer("Не найдено — обнови список.", show_alert=True)
        return
    await c.answer("Упаковываю…")
    if not isinstance(c.message, Message):
        return
    archive_name = (entry["path"].name or "archive") + ".zip"
    with tempfile.TemporaryDirectory(prefix="pcbot_zip_") as folder:
        archive = Path(folder) / archive_name
        try:
            await asyncio.to_thread(make_zip, entry["path"], archive)
        except (OSError, ValueError) as error:
            await c.message.answer(f"Не удалось упаковать: {error}")
            return
        if archive.stat().st_size > MAX_SEND_BYTES:
            await c.message.answer(
                f"Архив получился {human_size(archive.stat().st_size)} — "
                "Telegram не примет больше 48 МБ."
            )
            return
        await c.message.answer_document(
            FSInputFile(archive),
            caption=f"🗜 <b>{html.escape(archive_name)}</b>",
            parse_mode="HTML",
        )


@dp.callback_query(F.data.startswith("files:del:"))
async def cb_files_delete_ask(c: CallbackQuery):
    index = int(c.data.rsplit(":", 1)[1])
    entry = _entry(c.from_user.id, index)
    if entry is None:
        await c.answer("Не найдено — обнови список.", show_alert=True)
        return
    await c.answer()
    markup = kb([[
        b(text="🗑 Да, в корзину", callback_data=f"files:delok:{index}"),
        b(text="↩️ Отмена", callback_data="files:r"),
    ]])
    await _edit(
        c,
        f"Удалить в корзину?\n<code>{html.escape(str(entry['path']))}</code>",
        markup,
    )


@dp.callback_query(F.data.startswith("files:delok:"))
async def cb_files_delete(c: CallbackQuery):
    index = int(c.data.rsplit(":", 1)[1])
    entry = _entry(c.from_user.id, index)
    if entry is None:
        await c.answer("Не найдено — обнови список.", show_alert=True)
        return
    try:
        await asyncio.to_thread(delete_to_recycle, entry["path"])
    except OSError as error:
        await c.answer(f"Не удалось удалить: {error}", show_alert=True)
        return
    await c.answer("Удалено в корзину 🗑")
    state = _state(c.from_user.id)
    if state.get("cwd") is None:
        text, markup = render_drives()
        await _edit(c, text, markup)
        return
    await _show_dir(c, state["cwd"])


async def run_search(m: Message, query: str) -> None:
    """Ищет файлы по запросу пользователя в текущей папке."""
    user_id = m.from_user.id
    query = query.strip()
    if not query:
        await m.answer("Пустой запрос. Нажми 🔎 в файловом менеджере и пришли имя файла.")
        return
    base = _state(user_id).get("cwd") or Path.home()
    found = await asyncio.to_thread(search_files, base, query, 20)
    entries = [
        {"name": path.name, "is_dir": path.is_dir(), "size": 0, "path": path}
        for path in found
    ]
    _remember(user_id, base, entries, "search", query)
    text, markup = render_entries(entries, "search")
    head, _, body = text.partition("\n")
    header = f"\n<code>{html.escape(str(base))}</code>\nЗапрос: <b>{html.escape(query)}</b>"
    await m.answer(f"{head}{header}\n{body}", parse_mode="HTML", reply_markup=markup)
