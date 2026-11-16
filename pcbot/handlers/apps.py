"""Программы, сайты, Steam и мультимедиа."""

import asyncio
import html

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..runtime import dp
from ..security import log_action
from ..ui import (
    BACK,
    SOUND_HINTS,
    apps_menu,
    b,
    kb,
    sites_menu,
    sound_menu,
    steam_menu,
    tg_menu,
)
from ..windows_api import (
    APPS,
    SITES,
    TG_LINKS,
    VK,
    launch_app,
    open_site,
    open_target,
    press_media_key,
    run_steam_game,
    running_steam_games,
    steam_games,
)


@dp.callback_query(F.data == "sound")
async def cb_sound(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("🎵 <b>Звук и музыка</b>", parse_mode="HTML", reply_markup=sound_menu())


@dp.callback_query(F.data.startswith("key:"))
async def cb_key(c: CallbackQuery):
    name = c.data.split(":", 1)[1]
    if name not in VK:
        await c.answer()
        return
    try:
        times = 5 if name in ("vol_up", "vol_down") else 1  # 5 нажатий ≈ 10%
        await asyncio.to_thread(press_media_key, name, times)
        await c.answer(SOUND_HINTS[name])
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.callback_query(F.data == "apps")
async def cb_apps(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("🚀 <b>Что запустить?</b>", parse_mode="HTML", reply_markup=apps_menu())


@dp.callback_query(F.data.startswith("app:"))
async def cb_app(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    if key not in APPS:
        await c.answer()
        return
    try:
        await asyncio.to_thread(launch_app, key)
        await c.answer(f"Запускаю {APPS[key][0]}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)
        return
    log_action(c.from_user.id, "launch", APPS[key][0])
    # После запуска показываем, что можно делать внутри программы
    if key == "steam":
        await show_steam_games(c, 0)
    elif key == "chrome":
        await c.message.edit_text("🌐 <b>Chrome</b> запускается — что открыть?", parse_mode="HTML",
                                  reply_markup=sites_menu())
    elif key == "telegram":
        if TG_LINKS:
            await c.message.edit_text("✈️ <b>Telegram</b> запускается — какой чат открыть?", parse_mode="HTML",
                                      reply_markup=tg_menu())
        else:
            await c.message.edit_text(
                "✈️ <b>Telegram</b> запускается.\n"
                "Список избранных чатов пуст — его можно задать в файле bot.py (TG_LINKS).",
                parse_mode="HTML",
                reply_markup=kb([[b(text="⬅️ Программы", callback_data="apps"), BACK]]),
            )
    elif key == "music":
        await c.message.edit_text("🎵 <b>Яндекс Музыка</b> запускается — управление музыкой:",
                                  parse_mode="HTML", reply_markup=sound_menu())
    else:
        await c.message.edit_text(
            f"✅ <b>{html.escape(APPS[key][0])}</b> запускается.",
            parse_mode="HTML",
            reply_markup=kb([[b(text="⬅️ Программы", callback_data="apps"), BACK]]),
        )


async def show_steam_games(c: CallbackQuery, page: int):
    try:
        games = await asyncio.to_thread(steam_games)
    except Exception as e:  # noqa: BLE001
        await c.message.edit_text(f"Не получилось найти игры: {e}", reply_markup=apps_menu())
        return
    if not games:
        await c.message.edit_text("🎮 В Steam нет установленных игр.", reply_markup=apps_menu())
        return
    await c.message.edit_text(
        f"🎮 <b>Игры в Steam</b> ({len(games)} шт.)\nСверху — в которые играл недавно. Нажми, чтобы запустить:",
        parse_mode="HTML",
        reply_markup=steam_menu(games, page),
    )


@dp.callback_query(F.data == "sgk")
async def cb_steam_running(c: CallbackQuery):
    await c.answer()
    try:
        running = await asyncio.to_thread(running_steam_games)
    except Exception as e:  # noqa: BLE001
        await c.message.edit_text(f"Не получилось: {e}", reply_markup=apps_menu())
        return
    back = [b(text="🔄 Обновить", callback_data="sgk"), b(text="🎮 К играм", callback_data="sgp:0")]
    if not running:
        await c.message.edit_text("🎮 Сейчас ни одна игра из Steam не запущена.", reply_markup=kb([back, [BACK]]))
        return
    rows = [[b(text=f"⛔ {folder[:22]} ({name})"[:60], callback_data=f"kill:{pid}")]
            for pid, name, folder in running[:10]]
    rows += [back, [BACK]]
    await c.message.edit_text("⛔ <b>Какую игру закрыть?</b>\nНесохранённый прогресс в игре пропадёт.",
                              parse_mode="HTML", reply_markup=kb(rows))


@dp.callback_query(F.data.startswith("site:"))
async def cb_site(c: CallbackQuery):
    i = int(c.data.split(":", 1)[1])
    if not 0 <= i < len(SITES):
        await c.answer()
        return
    title, url = SITES[i]
    try:
        await asyncio.to_thread(open_site, url)
        await c.answer(f"Открываю {title}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.callback_query(F.data.startswith("tgl:"))
async def cb_tg_link(c: CallbackQuery):
    i = int(c.data.split(":", 1)[1])
    if not 0 <= i < len(TG_LINKS):
        await c.answer()
        return
    title, link = TG_LINKS[i]
    try:
        await asyncio.to_thread(open_target, link)
        await c.answer(f"Открываю {title}")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.callback_query(F.data.startswith("sgp:"))
async def cb_steam_page(c: CallbackQuery):
    await c.answer()
    await show_steam_games(c, int(c.data.split(":", 1)[1]))


@dp.callback_query(F.data.startswith("sg:"))
async def cb_steam_game(c: CallbackQuery):
    appid = c.data.split(":", 1)[1]
    if not appid.isdigit():
        await c.answer()
        return
    try:
        await asyncio.to_thread(run_steam_game, appid)
        name = next((n for a, n, _ in await asyncio.to_thread(steam_games) if a == appid), "игру")
        await c.answer(f"Запускаю {name} 🎮", show_alert=False)
        await c.message.answer(f"🎮 Запускаю <b>{html.escape(name)}</b>. Steam может пару секунд готовиться.",
                               parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        await c.answer(str(e), show_alert=True)


@dp.message(Command("open"))
async def cmd_open(m: Message, command: CommandObject):
    if not command.args:
        await m.answer("Использование: /open <путь | программа | url>")
        return
    try:
        await asyncio.to_thread(open_target, command.args.strip())
        await m.answer(f"Открыл: {command.args}")
    except Exception as e:  # noqa: BLE001
        await m.answer(f"Ошибка: {e}")
