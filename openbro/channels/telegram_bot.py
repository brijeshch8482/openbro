"""Telegram bot channel - phone interface for OpenBro."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from rich.console import Console

from openbro.channels.base import Channel
from openbro.memory import MemoryManager
from openbro.utils.config import load_config

if TYPE_CHECKING:
    pass

console = Console()
logger = logging.getLogger("openbro.telegram")


class TelegramBot(Channel):
    name = "telegram"

    def __init__(self, token: str, allowed_users: list[int] | None = None):
        self.token = token
        self.allowed_users = set(allowed_users or [])
        self._app = None
        self._sessions: dict[int, MemoryManager] = {}

    def _check_deps(self):
        try:
            import telegram  # noqa: F401
            from telegram.ext import Application  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "Telegram support requires python-telegram-bot. "
                "Install with: pip install 'openbro[telegram]'"
            ) from e

    def _is_authorized(self, user_id: int) -> bool:
        if not self.allowed_users:
            return True  # No whitelist = open to all (warn user during setup)
        return user_id in self.allowed_users

    def _get_session(self, user_id: int) -> MemoryManager:
        if user_id not in self._sessions:
            self._sessions[user_id] = MemoryManager(
                user_id=str(user_id),
                channel="telegram",
            )
        return self._sessions[user_id]

    def _get_agent(self, user_id: int):
        """Create a per-user agent with their memory bound."""
        from openbro.core.agent import Agent

        memory = self._get_session(user_id)
        return Agent(memory=memory, interactive=False)

    async def _handle_start(self, update, context):
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            await update.message.reply_text(
                f"Sorry bro, you're not authorized.\nYour ID: {user_id}"
            )
            return

        await update.message.reply_text(
            "Hey bhai! Tera OpenBro ready hai. "
            "Type kuch bhi aur main help karunga.\n\n"
            "Commands:\n"
            "/help - show commands\n"
            "/reset - clear chat history\n"
            "/whoami - show your user ID"
        )

    async def _handle_help(self, update, context):
        await update.message.reply_text(
            "OpenBro Telegram Bot\n\n"
            "Just type your message in natural language.\n"
            "Hindi, English, Hinglish - sab chalega.\n\n"
            "Commands:\n"
            "/start - initial greeting\n"
            "/reset - clear current chat history\n"
            "/whoami - show your Telegram user ID"
        )

    async def _handle_whoami(self, update, context):
        user_id = update.effective_user.id
        await update.message.reply_text(f"Your Telegram user ID: {user_id}")

    async def _handle_reset(self, update, context):
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            return
        if user_id in self._sessions:
            self._sessions[user_id].clear_working()
        await update.message.reply_text("Chat history cleared.")

    async def _handle_message(self, update, context):
        user_id = update.effective_user.id
        if not self._is_authorized(user_id):
            await update.message.reply_text(
                f"Not authorized. Your ID: {user_id}\n"
                "Ask the bot owner to add you to allowed_users."
            )
            return

        text = update.message.text or ""
        if not text.strip():
            return

        # Show typing indicator
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing",
        )

        try:
            agent = self._get_agent(user_id)
            # Run the (sync) chat in a thread to avoid blocking the event loop
            response = await asyncio.to_thread(agent.chat, text)
        except Exception as e:
            logger.exception("Agent error")
            response = f"Bro, error aa gaya: {e}"

        # Telegram has a 4096 char limit per message
        for chunk in self._chunk_message(response, 4000):
            await update.message.reply_text(chunk)

    def _chunk_message(self, text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            chunks.append(text[:max_len])
            text = text[max_len:]
        return chunks

    def start(self) -> None:
        self._check_deps()

        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            filters,
        )

        self._app = ApplicationBuilder().token(self.token).build()

        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("whoami", self._handle_whoami))
        self._app.add_handler(CommandHandler("reset", self._handle_reset))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        console.print("[bold cyan]Telegram bot starting...[/bold cyan]")
        if self.allowed_users:
            console.print(f"[dim]Allowed users: {sorted(self.allowed_users)}[/dim]")
        else:
            console.print("[yellow]Warning: no allowed_users set - bot is open to anyone![/yellow]")
        console.print("[green]Bot ready! Press Ctrl+C to stop.[/green]\n")

        self._app.run_polling(allowed_updates=["message"])

    def stop(self) -> None:
        if self._app:
            self._app.stop()


def run_telegram_from_config():
    """Helper: read config and start the bot."""
    config = load_config()
    tg_cfg = config.get("channels", {}).get("telegram", {})

    token = tg_cfg.get("token")
    if not token:
        console.print(
            "[red]Telegram bot token not configured.[/red]\n"
            "Set it with: openbro config set channels.telegram.token YOUR_TOKEN\n"
            "Get a token from @BotFather on Telegram."
        )
        return

    allowed = tg_cfg.get("allowed_users") or []
    bot = TelegramBot(token=token, allowed_users=allowed)
    bot.start()
