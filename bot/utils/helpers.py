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


async def assistant_user_id() -> int | None:
    """Deprecated shim — see bot.services.assistant.user_id()."""
    from bot.services import assistant

    return await assistant.user_id()


async def ensure_assistant_in_chat(bot: "Bot", chat_id: int) -> str | None:
    """Deprecated shim kept for callers outside the play path.

    Real logic lives in bot.services.assistant, which can also *invite* the
    assistant instead of only reporting that it is missing.
    """
    from bot.services import assistant

    result = await assistant.ensure_present(bot, chat_id)
    if result.ok:
        return None
    return f"{result.title} {result.hint}".strip()


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
