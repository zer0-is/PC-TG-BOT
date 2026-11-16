"""Команды оболочки и получение файлов."""

import asyncio
import html
from pathlib import Path

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message

from ..config import ALLOW_FILES, ALLOW_SHELL, CONSOLE_ENC
from ..runtime import dp
from ..security import log_action, store_action, take_action
from ..ui import b, kb, main_menu


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
    token = parts[2]
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
            except TimeoutError:
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
