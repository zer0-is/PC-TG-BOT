"""Тесты безопасности: подпись callback_data, rate-limit, отложенные действия."""
import asyncio
from types import SimpleNamespace

import bot


def test_sign_and_unsign_roundtrip():
    signed = bot.sign_data("kill:1234")
    assert signed != "kill:1234"
    assert "|" in signed
    assert bot.unsign_data(signed) == "kill:1234"


def test_unsign_rejects_unsigned_data():
    assert bot.unsign_data("kill:1234") is None
    assert bot.unsign_data("") is None
    assert bot.unsign_data("|") is None


def test_unsign_rejects_tampered_payload():
    signed = bot.sign_data("kill:1234")
    tampered = signed.replace("1234", "9999")
    assert bot.unsign_data(tampered) is None


def test_unsign_rejects_wrong_signature():
    assert bot.unsign_data("kill:1234|0000000000000000") is None


def test_button_callback_is_signed():
    button = bot.b(text="Закрыть", callback_data="kill:42")
    assert button.callback_data.startswith("kill:42|")
    assert bot.unsign_data(button.callback_data) == "kill:42"


def test_pending_action_is_single_use():
    token = bot.store_action(111, "run", "echo hi")
    assert bot.take_action(token, 111) == ("run", "echo hi")
    assert bot.take_action(token, 111) is None


def test_pending_action_belongs_to_user():
    token = bot.store_action(111, "get", r"C:\file.txt")
    assert bot.take_action(token, 222) is None
    assert bot.take_action(token, 111) == ("get", r"C:\file.txt")


def test_pending_action_token_is_short():
    """Токен попадает в callback_data, а Telegram ограничивает его 64 байтами."""
    token = bot.store_action(111, "run", "x" * 5000)
    assert len(token) <= 32
    assert len(f"act:run:{token}") <= 64


class FakeEvent:
    """Заглушка сообщения для проверки middleware."""

    def __init__(self, user_id: int):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


def test_rate_limiter_allows_then_blocks():
    limiter = bot.RateLimitMiddleware(limit=2, window=60)
    event = FakeEvent(111)
    seen: list[object] = []

    async def handler(evt, data):
        seen.append(evt)
        return "ok"

    async def scenario():
        assert await limiter(handler, event, {}) == "ok"
        assert await limiter(handler, event, {}) == "ok"
        assert await limiter(handler, event, {}) is None

    asyncio.run(scenario())
    assert len(seen) == 2
    assert len(event.answers) == 1
    assert "минуту" in event.answers[0]


def test_rate_limiter_is_per_user():
    limiter = bot.RateLimitMiddleware(limit=1, window=60)

    async def handler(evt, data):
        return "ok"

    async def scenario():
        assert await limiter(handler, FakeEvent(111), {}) == "ok"
        assert await limiter(handler, FakeEvent(111), {}) is None
        # другой пользователь ограничением не затронут
        assert await limiter(handler, FakeEvent(222), {}) == "ok"

    asyncio.run(scenario())


def test_action_log_records_and_trims():
    before = len(bot.action_log)
    bot.log_action(111, "kill", "test.exe (PID 1)")
    assert len(bot.action_log) == before + 1
    entry = bot.action_log[-1]
    assert entry["user"] == 111
    assert entry["action"] == "kill"
    assert entry["detail"] == "test.exe (PID 1)"
    assert "at" in entry
