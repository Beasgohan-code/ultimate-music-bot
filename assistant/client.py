"""Pyrogram userbot assistant for voice chat streaming."""

from __future__ import annotations

import logging

from pyrogram import Client

from bot.config import config

logger = logging.getLogger(__name__)


def session_problem(session_string: str) -> str:
    """Explain why a session string is unusable, or "" when it looks fine.

    Pyrogram base64-decodes the string and struct-unpacks a fixed 271-byte
    record. Anything else dies inside the library as::

        struct.error: unpack requires a buffer of 271 bytes

    which is raised at ``assistant.start()``, kills the whole process, and
    says nothing about which variable is wrong or how to regenerate it. That
    is the single most likely way a fresh deploy fails, because the value is
    long, easy to truncate when copy-pasting, and easy to confuse with the
    bot token. Check it here and say so in words instead.
    """
    import base64
    import binascii
    import struct

    value = (session_string or "").strip()
    if not value:
        return "SESSION_STRING is empty"

    if value.count(":") == 1 and value.split(":")[0].isdigit():
        return (
            "SESSION_STRING looks like a BOT TOKEN, not a session string. "
            "The assistant is a normal user account, not a bot"
        )

    try:
        from pyrogram.storage.storage import Storage

        fmt = Storage.SESSION_STRING_FORMAT
    except Exception:  # pragma: no cover - pyrogram always provides this
        fmt = ">BI?256sQ?"
    expected = struct.calcsize(fmt)

    try:
        # Pyrogram pads the base64 itself; mirror that so a stripped '=' is
        # not reported as corruption.
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return "SESSION_STRING is not valid base64 — it looks truncated or altered"

    if len(raw) != expected:
        return (
            f"SESSION_STRING decodes to {len(raw)} bytes, expected {expected} "
            "— it is truncated or from an incompatible library"
        )
    return ""


async def resolve_session() -> str:
    """The session string to connect with, env first, then the database.

    SESSION_STRING in the environment wins: it is what the operator set
    deliberately, and on a PaaS it is the only copy that survives a redeploy.
    A session generated at runtime through /genstring lands in the database,
    so fall back to that — otherwise the bot would save a session and then
    ignore it, which is the sort of thing nobody notices until they have
    restarted three times.
    """
    if config.session_string:
        return config.session_string

    try:
        from bot.services.sessiongen import stored

        value = await stored()
    except Exception as exc:
        logger.debug("Could not read a stored session: %s", exc)
        return ""

    if value:
        logger.info(
            "Using the assistant session saved by /genstring. Copy it into "
            "SESSION_STRING so it survives a redeploy."
        )
    return value


def create_assistant(session_string: str | None = None) -> Client:
    return Client(
        "ultimate-assistant",
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_string=session_string if session_string is not None else config.session_string,
        in_memory=True,
    )
