"""Удалённый ввод: мышь и клавиатура. Включается флагом ALLOW_INPUT."""

import asyncio

from aiogram import F
from aiogram.types import CallbackQuery, Message

from ..config import ALLOW_INPUT
from ..input_control import (
    click_at,
    mouse_click,
    mouse_position,
    mouse_scroll,
    press_combo,
    type_text,
)
from ..runtime import dp, waiting_input
from ..ui import input_menu

DISABLED = (
    "🖱 Удалённый ввод выключен.\n"
    "Включи ALLOW_INPUT=true в .env и перезапусти бота."
)


def _parse_point(text: str) -> tuple[int, int]:
    """'640, 480' -> (640, 480); понимает и точку с запятой."""
    cleaned = text.replace(";", ",").replace(" ", "")
    parts = cleaned.split(",")
    if len(parts) != 2:
        raise ValueError("Нужны две координаты: например 640,480")
    return int(parts[0]), int(parts[1])


@dp.callback_query(F.data == "input")
async def cb_input(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    await c.answer()
    if isinstance(c.message, Message):
        await c.message.edit_text(
            "🖱 <b>Мышь и клавиатура</b>\n\n"
            "Клики, прокрутка, ввод текста и сочетания клавиш.\n"
            "Действия применяются к активному окну на ПК.",
            parse_mode="HTML",
            reply_markup=input_menu(),
        )


@dp.callback_query(F.data.startswith("inp:click:"))
async def cb_input_click(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    button = c.data.rsplit(":", 1)[1]
    try:
        text = await asyncio.to_thread(mouse_click, button)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"{text} 🖱")


@dp.callback_query(F.data.startswith("inp:scroll:"))
async def cb_input_scroll(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    amount = int(c.data.rsplit(":", 1)[1])
    try:
        text = await asyncio.to_thread(mouse_scroll, amount)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"{text} 🖱")


@dp.callback_query(F.data == "inp:where")
async def cb_input_where(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    try:
        x, y = await asyncio.to_thread(mouse_position)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"Курсор: {x}, {y}", show_alert=True)


@dp.callback_query(F.data == "inp:type")
async def cb_input_type(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    waiting_input[c.from_user.id] = "input_text"
    await c.answer()
    if isinstance(c.message, Message):
        await c.message.answer(
            "⌨️ Пришли текст — напечатаю его в активном окне ПК.\n"
            "Поддерживаются русские и английские буквы."
        )


@dp.callback_query(F.data == "inp:pos")
async def cb_input_pos(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    waiting_input[c.from_user.id] = "click_pos"
    await c.answer()
    if isinstance(c.message, Message):
        await c.message.answer(
            "🎯 Пришли координаты в формате <code>x,y</code> — например "
            "<code>640,480</code>. Кликну левой кнопкой.",
            parse_mode="HTML",
        )


@dp.callback_query(F.data == "inp:combo")
async def cb_input_combo(c: CallbackQuery):
    if not ALLOW_INPUT:
        await c.answer(DISABLED, show_alert=True)
        return
    waiting_input[c.from_user.id] = "combo"
    await c.answer()
    if isinstance(c.message, Message):
        await c.message.answer(
            "⌨️ Пришли сочетание клавиш — например <code>win+d</code>, "
            "<code>alt+f4</code> или <code>ctrl+shift+esc</code>.",
            parse_mode="HTML",
        )


async def run_input_text(m: Message, text: str) -> None:
    try:
        count = await asyncio.to_thread(type_text, text)
    except Exception as error:  # noqa: BLE001
        await m.answer(f"Не получилось напечатать: {error}")
        return
    await m.answer(f"⌨️ Напечатал символов: {count}")


async def run_click_pos(m: Message, text: str) -> None:
    try:
        x, y = _parse_point(text)
        result = await asyncio.to_thread(click_at, x, y)
    except (ValueError, OSError, RuntimeError) as error:
        await m.answer(f"Не получилось: {error}")
        return
    await m.answer(f"{result} 🖱 ({x}, {y})")


async def run_combo(m: Message, text: str) -> None:
    try:
        combo = await asyncio.to_thread(press_combo, text)
    except (ValueError, OSError, RuntimeError) as error:
        await m.answer(f"Не получилось: {error}")
        return
    await m.answer(f"⌨️ Нажато: <code>{combo}</code>", parse_mode="HTML")
