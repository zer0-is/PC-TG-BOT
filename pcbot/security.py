"""Белый список, подпись кнопок, троттлинг и журнал действий."""

import hashlib
import hmac
import secrets
import time
from datetime import datetime

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from .config import ACTION_LOG_LIMIT, ALLOWED, SIGNING_KEY, log


class WhitelistMiddleware(BaseMiddleware):
    """Молча игнорирует всех, кого нет в белом списке."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.id not in ALLOWED:
            if user is not None:
                log.warning("Отклонён доступ: id=%s username=%s", user.id, user.username)
            return None
        return await handler(event, data)


def sign_data(data: str) -> str:
    """Подписывает callback_data, чтобы кнопку нельзя было подделать."""
    digest = hmac.new(
        SIGNING_KEY.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:16]
    return f"{data}|{digest}"


def unsign_data(payload: str) -> str | None:
    """Возвращает исходные данные, если подпись верна, иначе None."""
    data, separator, digest = payload.rpartition("|")
    if not separator:
        return None
    expected = hmac.new(
        SIGNING_KEY.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:16]
    if not hmac.compare_digest(digest, expected):
        return None
    return data


async def reject(event, text: str) -> None:
    """Отвечает пользователю, не различая сообщение и нажатие кнопки."""
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)


class CallbackSignerMiddleware(BaseMiddleware):
    """Проверяет подпись callback_data и подменяет её исходным значением."""

    async def __call__(self, handler, event, data):
        unsigned = unsign_data(event.data)
        if unsigned is None:
            log.warning("Отклонён callback без верной подписи: %s", event.data[:64])
            await reject(event, "Кнопка устарела или подделана. Открой меню заново.")
            return None
        # CallbackQuery в aiogram — неизменяемая модель (frozen), поэтому
        # подменяем значение в обход pydantic-валидации.
        object.__setattr__(event, "data", unsigned)
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Ограничивает количество запросов от одного пользователя."""

    def __init__(self, limit: int, window: int = 60):
        self.limit = max(1, limit)
        self.window = window
        self._buckets: dict[int, list[float]] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None:
            now = time.monotonic()
            cutoff = now - self.window
            bucket = [stamp for stamp in self._buckets.get(user.id, []) if stamp > cutoff]
            if len(bucket) >= self.limit:
                log.warning("Сработал лимит запросов для id=%s", user.id)
                await reject(event, "Слишком много запросов. Подожди минуту.")
                return None
            bucket.append(now)
            self._buckets[user.id] = bucket
        return await handler(event, data)


action_log: list[dict[str, object]] = []


def log_action(user_id: int, action: str, detail: str = "") -> None:
    action_log.append(
        {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_id,
            "action": action,
            "detail": detail,
        }
    )
    del action_log[:-ACTION_LOG_LIMIT]
    log.info("Действие %s от id=%s: %s", action, user_id, detail)


pending_actions: dict[str, dict[str, object]] = {}


def store_action(user_id: int, action: str, payload: str, ttl: int = 900) -> str:
    """Запоминает действие и возвращает короткий токен для кнопки подтверждения."""
    now = time.time()
    for token, item in list(pending_actions.items()):
        if now - float(item["created"]) > ttl:  # type: ignore[arg-type]
            pending_actions.pop(token, None)
    token = secrets.token_hex(6)
    pending_actions[token] = {
        "user": user_id,
        "action": action,
        "payload": payload,
        "created": now,
    }
    return token


def take_action(token: str, user_id: int) -> tuple[str, str] | None:
    """Забирает отложенное действие, если токен верный и принадлежит пользователю.

    Чужой токен не расходуется: иначе посторонний, угадав токен, мог бы
    «сжечь» подтверждение владельца.
    """
    item = pending_actions.get(token)
    if not item:
        return None
    if item["user"] != user_id:
        return None
    pending_actions.pop(token, None)
    return str(item["action"]), str(item["payload"])


BENIGN_ERRORS = (
    "message is not modified",
    "message is too old",
    "message to edit not found",
    "query is too old",
)
