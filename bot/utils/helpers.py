"""Shared bot utilities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from bot.config import config

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

_search_cache: dict[str, dict] = {}
_suggest_cache: dict[str, dict] = {}


def cache_search_results(results: list[dict]) -> dict[str, dict]:
    mapping = {r["id"]: r for r in results if r.get("id")}
    _search_cache.update(mapping)
    return mapping


def get_cached_track(track_id: str) -> dict | None:
    return _search_cache.get(track_id) or _suggest_cache.get(track_id)


def cache_suggestions(results: list[dict]) -> dict[str, dict]:
    mapping = {r["id"]: r for r in results if r.get("id")}
    _suggest_cache.update(mapping)
    return mapping


def is_sudo(user_id: int) -> bool:
    return user_id in config.sudo_users


def is_group_chat(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


#: Cached numeric id of the assistant account, resolved once from its own
#: session. Usernames are not usable here (see below) and can also change.
_assistant_id: int | None = None


async def assistant_user_id() -> int | None:
    """Numeric id of the streaming assistant, or None if it is unavailable.

    Asks the assistant's own Pyrogram session rather than resolving a
    username through the Bot API: a bot can only resolve a @username it has
    already seen, and the answer is a string the Bot API will not accept
    where a user id is required.
    """
    global _assistant_id
    if _assistant_id:
        return _assistant_id

    from bot.services.stream import stream_manager

    client = getattr(stream_manager, "_user_client", None)
    if client is None:
        return None
    try:
        me = await client.get_me()
    except Exception as exc:  # not connected yet, or session revoked
        logger.debug("Could not read the assistant's own id: %s", exc)
        return None
    _assistant_id = getattr(me, "id", None)
    return _assistant_id


def _assistant_label() -> str:
    return f"@{config.assistant_username}" if config.assistant_username else "the assistant"


async def ensure_assistant_in_chat(bot: "Bot", chat_id: int) -> str | None:
    """Check the assistant can stream here. Returns an error message or None.

    The previous version passed "@username" as get_chat_member's user_id.
    That argument is typed int, so the call was rejected before it left the
    process and the bare except reported "not in group" every single time —
    including when the assistant was sitting right there. Playback could
    never start in any group.
    """
    user_id = await assistant_user_id()
    if user_id is None:
        # Cannot verify. Let playback proceed and fail with a real error
        # instead of inventing one; a wrong "add the assistant" message sends
        # people chasing a problem they do not have.
        logger.debug("Assistant id unavailable — skipping the membership check")
        return None

    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest as exc:
        # "user not found" is Telegram's way of saying "never joined".
        if "not found" in str(exc).lower():
            return (
                f"Please add {_assistant_label()} to this group so it can join "
                "the voice chat."
            )
        logger.debug("Assistant membership check failed: %s", exc)
        return None
    except Exception as exc:
        logger.debug("Assistant membership check failed: %s", exc)
        return None

    if member.status in ("left", "kicked"):
        return (
            f"Please add {_assistant_label()} to this group so it can join "
            "the voice chat."
        )
    return None


def extract_query(message: Message) -> str | None:
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


async def reply_error(message: Message, text: str) -> Message:
    from bot.utils.formatters import error_card

    return await message.answer(error_card(text), parse_mode="HTML")


async def reply_success(message: Message, text: str) -> Message:
    from bot.utils.formatters import success_card

    return await message.answer(success_card(text), parse_mode="HTML")
