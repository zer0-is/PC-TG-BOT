"""Процессы: список, сортировка и закрытие."""

import asyncio
import html

import psutil
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..config import ALLOW_SHELL, log
from ..processes import close_all_processes, kill_process, process_rows, sort_rows
from ..runtime import dp
from ..security import log_action
from ..ui import (
    CLOSEALL_WARNING,
    PROC_SORTS,
    confirm_kb,
    detail_menu,
    main_menu,
    procs_menu,
    procs_text,
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
