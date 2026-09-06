"""Get the streaming assistant into a chat so playback can start.

Telling someone "add @Assistant to this group" is a poor experience: they have
to find the account, invite it, and retry — and if the bot is wrong about
whether it is already there, they chase a problem they do not have.

FallenMusic's play flow does the obvious thing instead: the bot exports an
invite link and the assistant joins itself. This is that, with the failure
modes named individually rather than dumped as raw exception text.

Everything here is best-effort. If the assistant cannot be verified or
invited, playback is still attempted — a real failure with a real error beats
a guess that blocks a working setup.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bot.config import config

logger = logging.getLogger(__name__)

#: Numeric id of the assistant, read once from its own session.
_assistant_id: int | None = None
_id_lock = asyncio.Lock()

#: Chats where the assistant is known to be present, so repeat plays skip the
#: round-trip. Cleared when a join fails or it is seen leaving.
_known_present: set[int] = set()


@dataclass(frozen=True)
class JoinResult:
    """Outcome of trying to get the assistant into a chat."""

    ok: bool
    #: User-facing explanation when ok is False.
    title: str = ""
    hint: str = ""
    #: True when the assistant had to be invited (worth telling the user).
    joined_now: bool = False


def _client():
    """The live Pyrogram assistant client, or None before setup()."""
    from bot.services.stream import stream_manager

    return getattr(stream_manager, "_user_client", None)


def forget(chat_id: int) -> None:
    """Drop the cached "present" flag for a chat."""
    _known_present.discard(chat_id)


def reset() -> None:
    """Forget everything — used by tests and on assistant reconnect."""
    global _assistant_id
    _assistant_id = None
    _known_present.clear()


async def user_id() -> int | None:
    """Numeric id of the assistant, cached after the first successful read.

    Asked of the assistant's own session because a bot cannot resolve an
    arbitrary @username through the Bot API, and get_chat_member requires an
    int anyway.
    """
    global _assistant_id
    if _assistant_id:
        return _assistant_id

    async with _id_lock:
        if _assistant_id:
            return _assistant_id
        client = _client()
        if client is None:
            return None
        try:
            me = await client.get_me()
        except Exception as exc:
            logger.debug("Assistant id unavailable: %s", exc)
            return None
        _assistant_id = getattr(me, "id", None)
        if _assistant_id:
            logger.info("Streaming assistant id: %s", _assistant_id)
        return _assistant_id


def label() -> str:
    return f"@{config.assistant_username}" if config.assistant_username else "the assistant"


async def _invite_link(bot, chat_id: int) -> tuple[str, str]:
    """Best link for the assistant to join with. Returns (link, error)."""
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as exc:
        return "", f"could not read the chat ({type(exc).__name__})"

    # A public @username is the cleanest route and needs no admin rights.
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}", ""

    existing = getattr(chat, "invite_link", None)
    if existing:
        return existing, ""

    try:
        link = await bot.export_chat_invite_link(chat_id)
        return link, ""
    except Exception as exc:
        text = str(exc).lower()
        if "admin" in text or "rights" in text:
            return "", "needs-invite-permission"
        return "", f"could not create an invite link ({type(exc).__name__})"


async def ensure_present(bot, chat_id: int) -> JoinResult:
    """Make sure the assistant is in `chat_id`, inviting it if necessary."""
    if chat_id in _known_present:
        return JoinResult(ok=True)

    assistant_id = await user_id()
    if assistant_id is None:
        # Unverifiable. Let playback try: a real error is more useful than a
        # fabricated "add the assistant" that may well be wrong.
        logger.debug("Assistant id unknown — skipping the membership check")
        return JoinResult(ok=True)

    status = await _membership(bot, chat_id, assistant_id)
    if status == "present":
        _known_present.add(chat_id)
        return JoinResult(ok=True)

    if status == "banned":
        return JoinResult(
            False,
            f"{label()} is banned from this group.",
            "Unban it in the group's admin settings, then try again.",
        )

    # Not a member — try to invite it rather than asking the user to.
    client = _client()
    if client is None:
        return JoinResult(
            False,
            "The streaming assistant isn't connected.",
            "The bot owner needs to check SESSION_STRING.",
        )

    link, problem = await _invite_link(bot, chat_id)
    if problem == "needs-invite-permission":
        return JoinResult(
            False,
            f"I can't invite {label()} here.",
            "Give me the 'Invite users via link' permission, or add the "
            "assistant to the group yourself.",
        )
    if not link:
        return JoinResult(
            False,
            f"I couldn't work out how to invite {label()}.",
            f"Add it to the group manually — {problem}.",
        )

    return await _join(client, link, chat_id)


async def _membership(bot, chat_id: int, assistant_id: int) -> str:
    """One of: present, absent, banned, unknown."""
    try:
        member = await bot.get_chat_member(chat_id, assistant_id)
    except Exception as exc:
        if "not found" in str(exc).lower():
            return "absent"
        logger.debug("Membership check failed: %s", exc)
        return "unknown"

    status = getattr(member, "status", "")
    status = getattr(status, "value", status)  # ChatMemberStatus enum
    if status in ("left",):
        return "absent"
    if status in ("kicked", "banned"):
        return "banned"
    return "present"


async def _join(client, link: str, chat_id: int) -> JoinResult:
    """Ask the assistant's own session to join, mapping failures to advice."""
    try:
        await client.join_chat(link)
    except Exception as exc:
        name = type(exc).__name__
        text = str(exc).lower()

        if name == "UserAlreadyParticipant" or "already" in text:
            _known_present.add(chat_id)
            return JoinResult(ok=True)

        if "invite_hash_expired" in text or "expired" in text:
            return JoinResult(
                False,
                f"The invite link for {label()} had expired.",
                "Add the assistant to the group manually, or revoke and "
                "recreate the group's invite link.",
            )
        if "flood" in text and "wait" in text:
            return JoinResult(
                False,
                f"Telegram is rate-limiting {label()}.",
                "Wait a minute and try again.",
            )
        if "too much" in text or "channels_too_much" in text:
            return JoinResult(
                False,
                f"{label()} has joined too many chats.",
                "The bot owner needs to remove it from some groups.",
            )
        if "banned" in text or "kicked" in text:
            return JoinResult(
                False,
                f"{label()} is banned from this group.",
                "Unban it, then try again.",
            )

        logger.warning("Assistant could not join %s: %s: %s", chat_id, name, exc)
        return JoinResult(
            False,
            f"I couldn't get {label()} into this group.",
            "Add it manually and try again.",
        )

    # Telegram needs a beat before the new member can join a call.
    await asyncio.sleep(1.5)
    _known_present.add(chat_id)
    logger.info("Assistant joined %s", chat_id)
    return JoinResult(ok=True, joined_now=True)
