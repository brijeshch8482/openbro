"""Tests for Telegram bot channel."""

from openbro.channels.telegram_bot import TelegramBot


def test_telegram_bot_init():
    bot = TelegramBot(token="fake_token", allowed_users=[123, 456])
    assert bot.token == "fake_token"
    assert bot.allowed_users == {123, 456}
    assert bot.name == "telegram"


def test_telegram_bot_authorization():
    bot = TelegramBot(token="t", allowed_users=[100])
    assert bot._is_authorized(100) is True
    assert bot._is_authorized(999) is False


def test_telegram_bot_open_when_no_users():
    bot = TelegramBot(token="t", allowed_users=[])
    # Empty whitelist = open
    assert bot._is_authorized(123) is True


def test_telegram_chunking_short():
    bot = TelegramBot(token="t")
    chunks = bot._chunk_message("short message", 4000)
    assert chunks == ["short message"]


def test_telegram_chunking_long():
    bot = TelegramBot(token="t")
    long_text = "a" * 8500
    chunks = bot._chunk_message(long_text, 4000)
    assert len(chunks) == 3
    assert sum(len(c) for c in chunks) == 8500
