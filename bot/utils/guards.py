"""Permission checks, target extraction and formatting helpers for moderation."""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import Chat, ChatPermissions, Message, User

from bot.config import config
from bot.services.database import database

logger = logging.getLogger(__name__)

ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

#: Short cache so a burst of moderation commands doesn't hammer getChatMember.
_admin_cache: dict[int, tuple[float, set[int]]] = {}
_ADMIN_TTL = 120.0


def is_sudo(user_id: int | None) -> bool:
    return bool(user_id) and user_id in config.owners


def is_group(message: Message) -> bool:
    return message.chat.type in GROUP_TYPES


def is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


async def admin_ids(bot: Bot, chat_id: int, *, force: bool = False) -> set[int]:
    """Cached set of admin user ids for a chat."""
    now = time.time()
    cached = _admin_cache.get(chat_id)
    if cached and not force and now - cached[0] < _ADMIN_TTL:
        return cached[1]
    try:
        members = await bot.get_chat_administrators(chat_id)
        ids = {m.user.id for m in members}
    except Exception as exc:
        logger.debug("Could not fetch admins for %s: %s", chat_id, exc)
        ids = cached[1] if cached else set()
    _admin_cache[chat_id] = (now, ids)
    return ids


def invalidate_admin_cache(chat_id: int) -> None:
    _admin_cache.pop(chat_id, None)


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if is_sudo(user_id):
        return True
    if user_id == chat_id:  # anonymous channel-as-user posts
        return True
    return user_id in await admin_ids(bot, chat_id)


async def is_admin_or_auth(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Admins, sudo users, and chat-level authorised users."""
    if await is_admin(bot, chat_id, user_id):
        return True
    return await database.is_auth_user(chat_id, user_id)


async def bot_can(bot: Bot, chat_id: int, permission: str) -> bool:
    """Check one of the bot's own admin rights (e.g. ``can_restrict_members``)."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        if member.status == ChatMemberStatus.CREATOR:
            return True
        return bool(getattr(member, permission, False))
    except Exception as exc:
        logger.debug("bot_can(%s) failed in %s: %s", permission, chat_id, exc)
        return False


async def is_user_admin_protected(bot: Bot, chat_id: int, user_id: int) -> bool:
    """True when the target must not be actioned (admin, owner, or the bot)."""
    if is_sudo(user_id):
        return True
    try:
        me = await bot.get_me()
        if user_id == me.id:
            return True
    except Exception:
        pass
    return user_id in await admin_ids(bot, chat_id)


# ─────────────────────────────────────────────────────────────────────────────
# Target extraction — reply, @username, user id, or text mention
# ─────────────────────────────────────────────────────────────────────────────

_USER_ID_RE = re.compile(r"^\d{5,}$")


async def extract_target(
    message: Message, bot: Bot, *, allow_self: bool = False
) -> tuple[int | None, str, str]:
    """Resolve the user a moderation command targets.

    Returns ``(user_id, display_name, remaining_reason)``.  ``user_id`` is
    ``None`` when nothing could be resolved.
    """
    args = (message.text or message.caption or "").split()[1:]
    reason = ""

    # 1) Reply to a message
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        if message.reply_to_message.sender_chat:
            return (
                message.reply_to_message.sender_chat.id,
                message.reply_to_message.sender_chat.title or "channel",
                " ".join(args).strip(),
            )
        if target:
            return target.id, target.full_name, " ".join(args).strip()

    if not args:
        return None, "", ""

    first, rest = args[0], args[1:]
    reason = " ".join(rest).strip()

    # 2) Text mention entity (users without a username)
    entities = message.entities or message.caption_entities or []
    for ent in entities:
        if ent.type == "text_mention" and ent.user:
            return ent.user.id, ent.user.full_name, reason

    # 3) Numeric id
    if _USER_ID_RE.match(first.lstrip("-")):
        uid = int(first)
        name = str(uid)
        try:
            chat = await bot.get_chat(uid)
            name = chat.full_name or chat.title or str(uid)
        except Exception:
            profile = await database.get_user(uid)
            name = profile.get("name", str(uid))
        return uid, name, reason

    # 4) @username
    if first.startswith("@"):
        username = first[1:]
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, chat.full_name or chat.title or username, reason
        except Exception:
            return None, username, reason

    return None, "", " ".join(args).strip()


def parse_duration(text: str) -> int | None:
    """Parse ``30m`` / ``2h`` / ``7d`` / ``1w`` into seconds (None if invalid)."""
    if not text:
        return None
    match = re.fullmatch(r"(\d+)\s*([smhdw])", text.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    seconds = value * factors[unit]
    return seconds if 30 <= seconds <= 366 * 86400 else None


def split_duration_reason(text: str) -> tuple[int | None, str]:
    """Pull a leading duration token off a reason string."""
    if not text:
        return None, ""
    parts = text.split(maxsplit=1)
    seconds = parse_duration(parts[0])
    if seconds is None:
        return None, text.strip()
    return seconds, (parts[1].strip() if len(parts) > 1 else "")


def humanize_seconds(seconds: int) -> str:
    delta = timedelta(seconds=seconds)
    days, rem = divmod(int(delta.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not parts:
        parts.append(f"{secs}s")
    return " ".join(parts) or "0s"


def time_since(timestamp: float) -> str:
    return humanize_seconds(max(0, int(time.time() - timestamp)))


MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
)

UNMUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


def mention(user: User | None, fallback: str = "user") -> str:
    """HTML mention that works for users without a username."""
    if not user:
        return fallback
    import html as _html

    name = _html.escape(user.full_name or fallback, quote=False)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def mention_id(user_id: int, name: str) -> str:
    import html as _html

    return f'<a href="tg://user?id={user_id}">{_html.escape(name or str(user_id), quote=False)}</a>'


def fill_placeholders(template: str, user: User, chat: Chat, count: int = 0) -> str:
    """Rose-style welcome placeholders: {first} {last} {fullname} {username}…"""
    import html as _html

    def esc(value: Any) -> str:
        return _html.escape(str(value or ""), quote=False)

    username = f"@{user.username}" if user.username else esc(user.first_name)
    return (
        template.replace("{first}", esc(user.first_name))
        .replace("{last}", esc(user.last_name or ""))
        .replace("{fullname}", esc(user.full_name))
        .replace("{username}", username)
        .replace("{mention}", mention(user))
        .replace("{id}", str(user.id))
        .replace("{chatname}", esc(chat.title or ""))
        .replace("{count}", str(count or ""))
    )
