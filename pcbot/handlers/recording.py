"""Запись экрана и микрофона. Включается флагом ALLOW_AUDIO."""

import asyncio
import html
from pathlib import Path

from aiogram import F
from aiogram.types import CallbackQuery, FSInputFile, Message

from ..config import ALLOW_AUDIO, RECORD_SECONDS
from ..recording import record_microphone, record_screen
from ..runtime import dp
from ..ui import recording_menu

DISABLED = (
    "🎬 Запись выключена.\n"
    "Включи ALLOW_AUDIO=true в .env и перезапусти бота."
)

# Длительность для каждой кнопки: по умолчанию из настроек и 30 секунд.
DURATIONS = {"screen": RECORD_SECONDS, "mic": RECORD_SECONDS, "screen30": 30, "mic30": 30}
MAX_SECONDS = 120


@dp.callback_query(F.data == "rec")
async def cb_recording_menu(c: CallbackQuery):
    if not ALLOW_AUDIO:
        await c.answer(DISABLED, show_alert=True)
        return
    await c.answer()
    if isinstance(c.message, Message):
        await c.message.edit_text(
            "🎬 <b>Запись</b>\n\n"
            f"Экран пишется в mp4, микрофон — в wav.\n"
            f"Максимум {MAX_SECONDS} секунд за один раз.\n"
            f"Файлы сохраняются в «Загрузки».",
            parse_mode="HTML",
            reply_markup=recording_menu(),
        )


@dp.callback_query(F.data.startswith("rec:"))
async def cb_recording(c: CallbackQuery):
    if not ALLOW_AUDIO:
        await c.answer(DISABLED, show_alert=True)
        return
    kind = c.data.split(":", 1)[1]
    seconds = DURATIONS.get(kind)
    if seconds is None:
        await c.answer("Неизвестный режим записи.", show_alert=True)
        return
    if not isinstance(c.message, Message):
        await c.answer()
        return

    is_screen = kind.startswith("screen")
    await c.answer(f"Записываю {seconds} с…")
    status = await c.message.answer(
        f"🎬 {'Записываю экран' if is_screen else 'Записываю микрофон'}, "
        f"{seconds} с — подожди."
    )
    try:
        path: Path = await asyncio.to_thread(
            record_screen if is_screen else record_microphone, seconds
        )
    except Exception as error:  # noqa: BLE001 — драйверы отвечают чем угодно
        await status.edit_text(f"Не получилось записать: {error}")
        return
    await status.delete()

    caption = (
        f"🎬 Экран · {seconds} с\n<code>{html.escape(path.name)}</code>"
        if is_screen
        else f"🎙 Микрофон · {seconds} с\n<code>{html.escape(path.name)}</code>"
    )
    try:
        if is_screen:
            await c.message.answer_video(
                FSInputFile(path), caption=caption, parse_mode="HTML",
                reply_markup=recording_menu(),
            )
        else:
            await c.message.answer_document(
                FSInputFile(path), caption=caption, parse_mode="HTML",
                reply_markup=recording_menu(),
            )
    except Exception as error:  # noqa: BLE001
        await c.message.answer(f"Файл записан, но отправить не удалось: {error}\n{path}")
