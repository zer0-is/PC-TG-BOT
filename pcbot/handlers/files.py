"""Приём файлов и свободный текст от пользователя."""

import asyncio
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import F
from aiogram.types import Message

from ..config import DOWNLOADS
from ..handlers.clipboard import popup_and_report
from ..handlers.files_mgr import run_search
from ..handlers.input import run_click_pos, run_combo, run_input_text
from ..runtime import bot, dp, in_background, waiting_input
from ..ui import main_menu
from ..windows_api import clipboard_set


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
    # Вложение может быть любым из типов ниже: у всех есть file_id и file_size.
    obj: Any
    name: str
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


@dp.message(F.text)
async def on_text(m: Message):
    text = m.text or ""
    mode = waiting_input.pop(m.from_user.id, None)
    if mode == "msg":
        in_background(popup_and_report(m, text))
    elif mode == "clip":
        try:
            await asyncio.to_thread(clipboard_set, text)
            await m.answer("✅ Текст в буфере обмена ПК — можно вставлять Ctrl+V.",
                           reply_markup=main_menu())
        except Exception as e:  # noqa: BLE001
            await m.answer(f"Не получилось записать в буфер: {e}")
    elif mode == "input_text":
        await run_input_text(m, text)
    elif mode == "click_pos":
        await run_click_pos(m, text)
    elif mode == "combo":
        await run_combo(m, text)
    elif mode == "search":
        await run_search(m, text)
    else:
        await m.answer("Не понял 🤔 Выбери действие в меню:", reply_markup=main_menu())
