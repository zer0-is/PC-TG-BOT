"""Скриншоты, веб-камера и состояние ПК."""

import asyncio

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..config import log
from ..runtime import dp
from ..system import system_stats, take_screenshot
from ..ui import detail_menu, main_menu
from ..windows_api import webcam_photo


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
    if not isinstance(c.message, Message):
        return
    await send_webcam(c.message)
    await c.message.answer("Что дальше?", reply_markup=main_menu())


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
