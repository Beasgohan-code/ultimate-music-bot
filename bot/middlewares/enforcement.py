"""Passive enforcement: locks, blacklisted words, anti-flood, custom filters.

Runs before handlers.  Returning ``None`` from :meth:`__call__` swallows the
update, which is how a deleted/blocked message stops propagating.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject

from bot.services.database import database
from bot.services.moderation import moderation
from bot.utils.guards import MUTED_PERMISSIONS, is_admin_or_auth, is_sudo

logger = logging.getLogger(__name__)


def _detect_types(message: Message) -> set[str]:
    """Which lock categories this message falls under."""
    kinds: set[str] = set()
    if message.text and not message.text.startswith("/"):
        kinds.add("text")
    if message.photo:
        kinds |= {"photo", "media"}
    if message.video:
        kinds |= {"video", "media"}
    if message.animation:
        kinds |= {"gif", "media"}
    if message.audio:
        kinds.add("audio")
    if message.voice:
        kinds.add("voice")
    if message.document:
        kinds.add("document")
    if message.sticker:
        kinds.add("sticker")
    if message.poll:
        kinds.add("poll")
    if message.game:
        kinds.add("game")
    if message.location or message.venue:
        kinds.add("location")
    if message.contact:
        kinds.add("contact")
    if message.dice:
        kinds.add("emojigame")
    if message.forward_origin:
        kinds.add("forward")
    if message.via_bot:
        kinds.add("inline")

    entities = (message.entities or []) + (message.caption_entities or [])
    for ent in entities:
        if ent.type in ("url", "text_link"):
            kinds.add("url")
        elif ent.type in ("mention", "text_mention"):
            kinds.add("mention")
    return kinds


class EnforcementMiddleware(BaseMiddleware):
    """Applies per-chat moderation rules to incoming messages."""

    def __init__(self) -> None:
        self._flood: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=64))
        self._last_flood_action: dict[tuple[int, int], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = data.get("event_update").message if data.get("event_update") else None
        if message is None and isinstance(event, Message):
            message = event
        if not isinstance(message, Message) or not message.from_user:
            return await handler(event, data)

        chat = message.chat
        bot: Bot = data["bot"]
        user = message.from_user

        if chat.type not in ("group", "supergroup"):
            return await handler(event, data)

        # Global bans / blacklisted chats short-circuit everything.
        if await database.is_banned(user.id) and not is_sudo(user.id):
            return None
        if await database.is_blacklisted(chat.id) and not is_sudo(user.id):
            return None

        if await is_admin_or_auth(bot, chat.id, user.id):
            return await handler(event, data)

        if await self._apply_locks(message, bot):
            return None
        if await self._apply_blacklist(message, bot):
            return None
        if await self._apply_flood(message, bot):
            return None

        result = await handler(event, data)
        await self._apply_filters(message)
        return result

    # ── locks ───────────────────────────────────────────────────────────
    async def _apply_locks(self, message: Message, bot: Bot) -> bool:
        locks = await moderation.locks(message.chat.id)
        if not any(locks.values()):
            return False
        kinds = _detect_types(message)
        hit = next((k for k in kinds if locks.get(k)), None)
        if not hit:
            return False
        try:
            await message.delete()
        except Exception as exc:
            logger.debug("Lock delete failed in %s: %s", message.chat.id, exc)
            return False
        return True

    # ── blacklisted words ───────────────────────────────────────────────
    async def _apply_blacklist(self, message: Message, bot: Bot) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False
        word = await moderation.match_blacklist(message.chat.id, text)
        if not word:
            return False

        mode = await moderation.blacklist_mode(message.chat.id)
        try:
            await message.delete()
        except Exception:
            pass

        user = message.from_user
        if not user:
            return True
        try:
            if mode == "warn":
                count, limit, action = await moderation.add_warn(
                    message.chat.id, user.id, f"Blacklisted word: {word}"
                )
                await message.answer(
                    f"⚠️ <b>{user.full_name}</b> used a blacklisted word — "
                    f"warning <code>{count}/{limit}</code>.",
                    parse_mode="HTML",
                )
                if count >= limit:
                    await moderation.reset_warns(message.chat.id, user.id)
                    if action == "ban":
                        await bot.ban_chat_member(message.chat.id, user.id)
                    elif action == "kick":
                        await bot.ban_chat_member(message.chat.id, user.id)
                        await bot.unban_chat_member(message.chat.id, user.id, only_if_banned=True)
                    else:
                        await bot.restrict_chat_member(
                            message.chat.id, user.id, permissions=MUTED_PERMISSIONS
                        )
            elif mode == "mute":
                await bot.restrict_chat_member(
                    message.chat.id, user.id, permissions=MUTED_PERMISSIONS
                )
            elif mode == "kick":
                await bot.ban_chat_member(message.chat.id, user.id)
                await bot.unban_chat_member(message.chat.id, user.id, only_if_banned=True)
            elif mode == "ban":
                await bot.ban_chat_member(message.chat.id, user.id)
        except Exception as exc:
            logger.debug("Blacklist action failed: %s", exc)
        return True

    # ── anti-flood ──────────────────────────────────────────────────────
    async def _apply_flood(self, message: Message, bot: Bot) -> bool:
        limit = await moderation.flood_limit(message.chat.id)
        if limit <= 0:
            return False

        key = (message.chat.id, message.from_user.id)
        now = time.time()
        bucket = self._flood[key]
        # Only count messages sent within 12 seconds of each other.
        if bucket and now - bucket[-1] > 12:
            bucket.clear()
        bucket.append(now)
        if len(bucket) < limit:
            return False

        # Don't re-punish within a 30s window.
        if now - self._last_flood_action.get(key, 0) < 30:
            return False
        self._last_flood_action[key] = now
        bucket.clear()

        action = await moderation.flood_action(message.chat.id)
        user = message.from_user
        try:
            if action == "ban":
                await bot.ban_chat_member(message.chat.id, user.id)
            elif action == "kick":
                await bot.ban_chat_member(message.chat.id, user.id)
                await bot.unban_chat_member(message.chat.id, user.id, only_if_banned=True)
            else:
                await bot.restrict_chat_member(
                    message.chat.id, user.id, permissions=MUTED_PERMISSIONS
                )
            await message.answer(
                f"🌊 <b>{user.full_name}</b> was {action}ed for flooding "
                f"({limit} messages in a row).",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.debug("Flood action failed: %s", exc)
        return True

    # ── custom filters ──────────────────────────────────────────────────
    async def _apply_filters(self, message: Message) -> None:
        text = message.text or message.caption or ""
        if not text or text.startswith("/"):
            return
        content = await moderation.match_filter(message.chat.id, text)
        if not content:
            return
        from bot.handlers.grouptools import _send_content

        await _send_content(message, content)
