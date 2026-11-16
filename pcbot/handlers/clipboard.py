"""Буфер обмена и сообщение на экране ПК."""

import asyncio
import html
from datetime import datetime

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..runtime import dp, in_background, waiting_input
from ..ui import BACK, clip_menu, kb
from ..windows_api import clipboard_get, show_message_box


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


async def popup_and_report(m: Message, text: str):
    await m.answer("💬 Показал сообщение на экране. Сообщу, когда его закроют.")
    try:
        await asyncio.to_thread(show_message_box, text)
        await m.answer(f"👀 Сообщение прочитали и закрыли ({datetime.now():%H:%M}).")
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Не получилось показать сообщение: {e}")


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
    if not isinstance(c.message, Message):
        return
    await send_clipboard(c.message)


@dp.callback_query(F.data == "clip_set")
async def cb_clip_set(c: CallbackQuery):
    await c.answer()
    waiting_input[c.from_user.id] = "clip"
    await c.message.edit_text("✏️ Пришли текст — положу его в буфер обмена ПК (потом Ctrl+V):",
                              reply_markup=kb([[BACK]]))
