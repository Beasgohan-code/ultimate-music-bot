"""Access control + bookkeeping that runs before every handler.

Handles: maintenance mode, global bans, chat blacklist, per-chat disabled
commands, light anti-spam throttling, and recording chats/users so
``/broadcast`` and the statistics have something to work with.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import config
from bot.services.database import database
from bot.services.moderation import moderation
from bot.services.stats import bot_stats
from bot.utils.guards import is_sudo

logger = logging.getLogger(__name__)


class GatekeeperMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._last_seen: dict[int, float] = defaultdict(float)
        self._last_touch: dict[int, float] = defaultdict(float)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message: Message | None = None
        user = None
        chat = None

        if isinstance(event, Message):
            message, user, chat = event, event.from_user, event.chat
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            chat = event.message.chat if event.message else None
        else:
            update = data.get("event_update")
            inner = getattr(update, "message", None) if update else None
            if inner is not None:
                message, user, chat = inner, inner.from_user, inner.chat

        if user is None:
            return await handler(event, data)

        bot_stats.commands_handled += 1
        sudo = is_sudo(user.id)

        # ── maintenance mode ────────────────────────────────────────────
        from bot.handlers.admin import MAINTENANCE

        if MAINTENANCE["on"] and not sudo:
            if isinstance(event, Message) and (event.text or "").startswith("/"):
                try:
                    await event.answer(
                        "🛠 <b>The bot is under maintenance.</b>\nPlease try again shortly.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return None

        # ── global ban / chat blacklist ─────────────────────────────────
        if not sudo:
            if await database.is_banned(user.id):
                return None
            if chat is not None and chat.id != user.id and await database.is_blacklisted(chat.id):
                return None

        # ── throttle command spam ───────────────────────────────────────
        if isinstance(event, Message) and (event.text or "").startswith("/") and not sudo:
            now = time.time()
            if now - self._last_seen[user.id] < config.throttle_seconds:
                return None
            self._last_seen[user.id] = now

        # ── per-chat disabled commands ──────────────────────────────────
        if (
            isinstance(event, Message)
            and chat is not None
            and chat.type in ("group", "supergroup")
            and (event.text or "").startswith("/")
        ):
            command = (event.text or "").split()[0].lstrip("/").split("@")[0].lower()
            if not sudo and await moderation.is_command_disabled(chat.id, command):
                return None

        # ── bookkeeping (throttled to once a minute per entity) ─────────
        await self._record(user, chat)

        return await handler(event, data)

    async def _record(self, user: Any, chat: Any) -> None:
        now = time.time()
        try:
            if now - self._last_touch[user.id] > 60:
                self._last_touch[user.id] = now
                await database.touch_user(user.id, user.full_name)
                if getattr(user, "username", None):
                    await database.set_user_value(user.id, "username", user.username)
            if chat is not None and chat.type in ("group", "supergroup"):
                key = -abs(chat.id)
                if now - self._last_touch[key] > 60:
                    self._last_touch[key] = now
                    await database.touch_chat(chat.id, chat.title or "")
        except Exception as exc:
            logger.debug("Bookkeeping failed: %s", exc)
