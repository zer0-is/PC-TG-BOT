"""Меню, справка, питание и ИИ."""

import asyncio
import html
import os
import subprocess
import sys

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from openai import APIConnectionError, APIError, APIStatusError

from ..ai import ask_openrouter
from ..config import (
    BASE_DIR,
    IS_WIN,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OS,
    POWER_DELAY,
    log,
)
from ..power import (
    ACTION_NAMES,
    POWER_COMMANDS,
    cancel_pending_power,
    pending_info,
    pending_power,
    schedule_power,
)
from ..runtime import dp, waiting_input
from ..ui import HELP, confirm_kb, main_menu, timer_menu


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
