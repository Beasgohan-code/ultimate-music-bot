"""Shared bot utilities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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


async def ensure_assistant_in_chat(bot: "Bot", chat_id: int) -> str | None:
    """Invite assistant to group if needed. Returns error message or None."""
    if not config.assistant_username:
        return "Assistant username not configured. Set ASSISTANT_USERNAME in .env"
    try:
        member = await bot.get_chat_member(chat_id, f"@{config.assistant_username}")
        if member.status in ("left", "kicked"):
            return f"Please add @{config.assistant_username} to this group first."
    except Exception:
        return f"Please add @{config.assistant_username} to this group and promote it."
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
