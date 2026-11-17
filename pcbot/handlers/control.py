"""Управление системой: монитор, окна, сеть, громкость, яркость, корзина."""

import asyncio
import html
import secrets
from pathlib import Path

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ..pc_control import (
    activate_window,
    change_brightness,
    change_volume,
    close_window,
    empty_recycle_bin,
    flush_dns,
    get_brightness,
    installed_programs,
    list_windows,
    minimize_window,
    monitor_off,
    network_info,
    open_settings_page,
    services_summary,
    set_wallpaper,
    startup_entries,
    toggle_mute,
)
from ..runtime import dp
from ..ui import BACK, b, control_menu, kb, monitor_menu

# Список окон запоминаем, чтобы кнопки не несли hwnd в callback_data.
window_state: dict[int, list[tuple[int, str]]] = {}

WINDOW_PAGE = 8
IMAGE_SUFFIXES = ("*.jpg", "*.jpeg", "*.png", "*.bmp")


def pick_wallpaper() -> Path | None:
    """Случайная картинка из «Изображений» или «Загрузок»."""
    candidates: list[Path] = []
    for folder in (Path.home() / "Pictures", Path.home() / "Downloads"):
        if not folder.is_dir():
            continue
        for pattern in IMAGE_SUFFIXES:
            candidates.extend(sorted(folder.rglob(pattern))[:40])
    if not candidates:
        return None
    return secrets.choice(candidates)


def windows_text(items: list[tuple[int, str]]) -> str:
    if not items:
        return "Открытых окон не найдено."
    lines = [f"🪟 <b>Открытые окна</b> ({len(items)})\n"]
    for index, (_hwnd, title) in enumerate(items[:WINDOW_PAGE], start=1):
        lines.append(f"{index}. {html.escape(title[:60])}")
    if len(items) > WINDOW_PAGE:
        lines.append(f"\n… и ещё {len(items) - WINDOW_PAGE}")
    lines.append("\nНажми на название, чтобы переключиться на окно.")
    return "\n".join(lines)


def windows_menu(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = []
    for index, (_hwnd, title) in enumerate(items[:WINDOW_PAGE]):
        rows.append([
            b(text=f"▶️ {title[:26]}", callback_data=f"win:{index}"),
            b(text="➖", callback_data=f"win:m:{index}"),
            b(text="✖", callback_data=f"win:c:{index}"),
        ])
    rows.append([b(text="🔄 Обновить", callback_data="wl"),
                 b(text="⬅️ Монитор", callback_data="mon"), BACK])
    return kb(rows)


@dp.callback_query(F.data == "mon")
async def cb_mon(c: CallbackQuery):
    await c.answer()
    if c.message is None:
        return
    await c.message.edit_text(
        "🖥 <b>Монитор и окна</b>\n\n"
        "Список окон, сеть, автозапуск, установленные программы и службы.",
        parse_mode="HTML",
        reply_markup=monitor_menu(),
    )


@dp.callback_query(F.data == "ctl")
async def cb_ctl(c: CallbackQuery):
    await c.answer()
    if c.message is None:
        return
    brightness = await asyncio.to_thread(get_brightness)
    level = f"{brightness}%" if brightness is not None else "недоступна"
    await c.message.edit_text(
        f"🎛 <b>Управление</b>\n\n🔆 Яркость экрана: {level}",
        parse_mode="HTML",
        reply_markup=control_menu(),
    )


@dp.callback_query(F.data.startswith("vol:"))
async def cb_volume(c: CallbackQuery):
    direction = 1 if c.data.split(":")[1] == "1" else -1
    try:
        text = await asyncio.to_thread(change_volume, direction)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"{text} 🔉")


@dp.callback_query(F.data == "mute")
async def cb_mute(c: CallbackQuery):
    try:
        await asyncio.to_thread(toggle_mute)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer("Звук переключён 🔇")


@dp.callback_query(F.data.startswith("br:"))
async def cb_brightness(c: CallbackQuery):
    delta = int(c.data.split(":")[1])
    try:
        value = await asyncio.to_thread(change_brightness, delta)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"Яркость: {value}%")
    if c.message is not None:
        await c.message.edit_text(
            f"🎛 <b>Управление</b>\n\n🔆 Яркость экрана: {value}%",
            parse_mode="HTML",
            reply_markup=control_menu(),
        )


@dp.callback_query(F.data == "monoff")
async def cb_monitor_off(c: CallbackQuery):
    try:
        await asyncio.to_thread(monitor_off)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer("Экран погашен 🌙")


@dp.callback_query(F.data == "wallpaper")
async def cb_wallpaper(c: CallbackQuery):
    picture = pick_wallpaper()
    if picture is None:
        await c.answer("В «Изображениях» и «Загрузках» нет картинок.", show_alert=True)
        return
    try:
        await asyncio.to_thread(set_wallpaper, picture)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    log_name = html.escape(picture.name)
    await c.answer("Обои сменены 🖼")
    if c.message is not None:
        await c.message.edit_text(
            f"🖼 Новые обои: <b>{log_name}</b>",
            parse_mode="HTML",
            reply_markup=control_menu(),
        )


@dp.callback_query(F.data == "recycl")
async def cb_recycle(c: CallbackQuery):
    try:
        await asyncio.to_thread(empty_recycle_bin)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer("Корзина очищена 🗑")


@dp.callback_query(F.data == "flushdns")
async def cb_flush_dns(c: CallbackQuery):
    try:
        await asyncio.to_thread(flush_dns)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer("Кэш DNS сброшен 🌐")


@dp.callback_query(F.data == "settings")
async def cb_settings(c: CallbackQuery):
    try:
        await asyncio.to_thread(open_settings_page, "display")
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer("Открываю параметры Windows ⚙️")


@dp.callback_query(F.data == "net")
async def cb_net(c: CallbackQuery):
    await c.answer()
    info = await asyncio.to_thread(network_info)
    lines = ["🌐 <b>Сеть</b>\n"]
    if info["wifi"]:
        lines.append(f"📶 Wi-Fi: <b>{html.escape(info['wifi'])}</b>")
    if info["addresses"]:
        for item in info["addresses"][:4]:
            lines.append(
                f"• {html.escape(item['name'])}: <code>{item['ip']}</code>"
            )
    else:
        lines.append("• Сетевых адресов нет")
    lines.append(f"\n📤 Отправлено: {info['sent_mb']} МБ")
    lines.append(f"📥 Получено: {info['received_mb']} МБ")
    if c.message is not None:
        await c.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb([[b(text="🔄 Обновить", callback_data="net"),
                              b(text="⬅️ Монитор", callback_data="mon"), BACK]]),
        )


@dp.callback_query(F.data == "startup")
async def cb_startup(c: CallbackQuery):
    await c.answer()
    entries = await asyncio.to_thread(startup_entries)
    if entries:
        body = "\n".join(f"• {html.escape(item[:90])}" for item in entries[:15])
        tail = f"\n… и ещё {len(entries) - 15}" if len(entries) > 15 else ""
        text = f"🔌 <b>Автозапуск</b>\n\n{body}{tail}"
    else:
        text = "🔌 Автозапуск пуст или недоступен."
    if c.message is not None:
        await c.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb([[b(text="🔄 Обновить", callback_data="startup"), BACK]]),
        )


@dp.callback_query(F.data == "installed")
async def cb_installed(c: CallbackQuery):
    await c.answer("Читаю реестр…")
    programs = await asyncio.to_thread(installed_programs)
    if programs:
        body = "\n".join(f"• {html.escape(name)}" for name in programs[:30])
        text = f"📦 <b>Установленные программы</b>\n\n{body}"
    else:
        text = "📦 Список программ недоступен."
    if c.message is not None:
        await c.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb([[b(text="⬅️ Монитор", callback_data="mon"), BACK]]),
        )


@dp.callback_query(F.data == "services")
async def cb_services(c: CallbackQuery):
    await c.answer()
    summary = await asyncio.to_thread(services_summary)
    if c.message is not None:
        await c.message.edit_text(
            "🛠 <b>Службы Windows</b>\n\n"
            f"▶️ Работают: <b>{summary['running']}</b>\n"
            f"⏸ Остановлены: <b>{summary['stopped']}</b>\n"
            f"📦 Всего: {summary['total']}",
            parse_mode="HTML",
            reply_markup=kb([[b(text="🔄 Обновить", callback_data="services"), BACK]]),
        )


@dp.callback_query(F.data == "wl")
async def cb_windows(c: CallbackQuery):
    await c.answer()
    items = await asyncio.to_thread(list_windows)
    window_state[c.from_user.id] = items
    if c.message is not None:
        await c.message.edit_text(
            windows_text(items), parse_mode="HTML", reply_markup=windows_menu(items)
        )


@dp.callback_query(F.data.startswith("win:"))
async def cb_window_action(c: CallbackQuery):
    parts = c.data.split(":")
    items = window_state.get(c.from_user.id, [])
    action = parts[1]
    if action == "m" or action == "c":
        index = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
    else:
        index = int(parts[1]) if parts[1].isdigit() else -1
        action = "a"
    if not 0 <= index < len(items):
        await c.answer("Список устарел — обнови его.", show_alert=True)
        return
    hwnd, _title = items[index]
    try:
        if action == "a":
            name = await asyncio.to_thread(activate_window, hwnd)
        elif action == "m":
            name = await asyncio.to_thread(minimize_window, hwnd)
        else:
            name = await asyncio.to_thread(close_window, hwnd)
    except Exception as error:  # noqa: BLE001
        await c.answer(f"Не получилось: {error}", show_alert=True)
        return
    await c.answer(f"{html.escape(name[:40])} — готово")
    items = [item for item in items if item[0] != hwnd] if action == "c" else items
    window_state[c.from_user.id] = items
    if c.message is not None:
        await c.message.edit_text(
            windows_text(items), parse_mode="HTML", reply_markup=windows_menu(items)
        )


@dp.message(F.text == "монитор")
async def cmd_monitor_word(m: Message):
    """Текстовый ярлык для старых привычек."""
    await m.answer(
        "🖥 Монитор и окна:",
        reply_markup=monitor_menu(),
    )


@dp.message(F.text == "управление")
async def cmd_control_word(m: Message):
    await m.answer("🎛 Управление:", reply_markup=control_menu())
