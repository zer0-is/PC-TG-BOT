"""Объекты Telegram и общее состояние процесса."""

import asyncio

from aiogram import Bot, Dispatcher

from .config import RATE_LIMIT, TOKEN
from .security import (
    CallbackSignerMiddleware,
    RateLimitMiddleware,
    WhitelistMiddleware,
)

bot = Bot(TOKEN)


dp = Dispatcher()

# Порядок важен: сначала белый список, затем подпись кнопок, затем лимит.
dp.message.outer_middleware(WhitelistMiddleware())
dp.callback_query.outer_middleware(WhitelistMiddleware())
dp.callback_query.outer_middleware(CallbackSignerMiddleware())


waiting_input: dict[int, str] = {}


background: set[asyncio.Task] = set()


def in_background(coro) -> None:
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


_rate_limiter = RateLimitMiddleware(RATE_LIMIT)
dp.message.outer_middleware(_rate_limiter)
dp.callback_query.outer_middleware(_rate_limiter)

