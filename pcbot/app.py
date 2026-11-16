"""Сборка приложения: регистрация обработчиков и запуск."""

from aiogram.types import BotCommand

from . import handlers  # noqa: F401 — импорт регистрирует все обработчики в dp
from .config import ALLOW_FILES, ALLOW_SHELL, ALLOWED, log
from .runtime import bot, dp
from .ui import HELP, main_menu


async def main():
    me = await bot.get_me()
    log.info("Бот @%s запущен. Разрешённые id: %s", me.username, sorted(ALLOWED))
    commands = [
        BotCommand(command="menu", description="Открыть панель управления"),
        BotCommand(command="help", description="Справка по командам"),
        BotCommand(command="screenshot", description="Сделать скриншот"),
        BotCommand(command="webcam", description="Фото с веб-камеры"),
        BotCommand(command="stats", description="Состояние компьютера"),
        BotCommand(command="procs", description="Процессы: сортировка и страницы"),
        BotCommand(command="allprocs", description="Все процессы по имени"),
        BotCommand(command="ask", description="Задать вопрос OpenRouter"),
        BotCommand(command="msg", description="Сообщение на экран ПК"),
        BotCommand(command="clip", description="Что в буфере обмена"),
        BotCommand(command="open", description="Открыть путь, программу или URL"),
        BotCommand(command="cancel", description="Отменить запланированное действие"),
    ]
    if ALLOW_SHELL:
        commands.append(BotCommand(command="closeall", description="Закрыть все процессы"))
        commands.append(BotCommand(command="run", description="Выполнить команду оболочки"))
    if ALLOW_FILES:
        commands.append(BotCommand(command="get", description="Получить файл"))
    await bot.set_my_commands(commands)
    for uid in ALLOWED:
        try:
            await bot.send_message(uid, f"🟢 Бот запущен.\n\n{HELP}", parse_mode="HTML",
                                   reply_markup=main_menu())
        except Exception:
            log.exception("Не удалось отправить меню пользователю id=%s", uid)
    await dp.start_polling(bot)
