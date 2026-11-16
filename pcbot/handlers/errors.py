"""Обработка ошибок обработчиков."""


from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

from ..config import log
from ..runtime import dp
from ..security import BENIGN_ERRORS


@dp.errors()
async def on_error(event: ErrorEvent):
    """Не считаем ошибкой безобидные ситуации Telegram."""
    message = str(event.exception)
    if isinstance(event.exception, TelegramBadRequest) and any(
        marker in message for marker in BENIGN_ERRORS
    ):
        log.debug("Безобидная ошибка Telegram: %s", message)
        return True
    log.error("Ошибка в обработчике: %s", event.exception, exc_info=event.exception)
    return True
