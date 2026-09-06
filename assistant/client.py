"""Pyrogram userbot assistant for voice chat streaming."""

from __future__ import annotations

import logging

from pyrogram import Client

from bot.config import config

logger = logging.getLogger(__name__)


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
