"""Keep music chats tidy by removing the bot's own clutter.

A music bot is chatty by nature: every /play leaves a command message, a
"Loading…" status and a Now Playing card. Ten songs later the group is mostly
bot noise and nobody can find the actual conversation.

Two independent, opt-in behaviours — both were already exposed in /settings but
never did anything until this module existed:

* clean_commands — delete the user's invoking command straight away.
* clean_mode     — delete the bot's transient replies after a delay.

Never deletes a Now Playing card while it is still current: that one is the
useful message, and the player buttons hang off it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from aiogram.types import Message

from bot.config import config
from bot.services.database import database

logger = logging.getLogger(__name__)

#: Pending deletions, so a chat that stops mid-countdown can cancel them.
_TASKS: set[asyncio.Task[Any]] = set()


async def _flag(chat_id: int, key: str) -> bool:
    """Read a per-chat toggle, defaulting to off. Never raises."""
    try:
        doc = await database.get_chat(chat_id)
    except Exception as exc:  # a settings lookup must not break playback
        logger.debug("clean flag lookup failed for %s: %s", chat_id, exc)
        return False
    return bool(doc.get(key, False))


async def clean_command(message: Message) -> None:
    """Delete the invoking command when the chat asked for that.

    Private chats are left alone — there is no clutter problem in a one-to-one
    chat, and deleting what someone typed there is just confusing.
    """
    if message.chat.type == "private":
        return
    if not await _flag(message.chat.id, "clean_commands"):
        return
    with contextlib.suppress(Exception):
        await message.delete()


def _forget(task: asyncio.Task[Any]) -> None:
    _TASKS.discard(task)


async def _delete_later(message: Message, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
        with contextlib.suppress(Exception):
            await message.delete()
    except asyncio.CancelledError:
        pass


def schedule_cleanup(message: Message | None, *, delay: int | None = None) -> None:
    """Delete a transient bot message later, if the chat enabled clean mode.

    Fire-and-forget: the caller should not wait five minutes to return. The
    task is tracked so shutdown can cancel it rather than leaving a warning.
    """
    if message is None:
        return

    async def _run() -> None:
        if not await _flag(message.chat.id, "clean_mode"):
            return
        # `delay or default` would swallow a deliberate 0 ("delete at once"),
        # because 0 is falsy — check for None instead.
        wait = config.clean_mode_seconds if delay is None else delay
        await _delete_later(message, wait)

    task = asyncio.create_task(_run())
    _TASKS.add(task)
    task.add_done_callback(_forget)


async def stop() -> None:
    """Cancel every pending deletion — used on shutdown."""
    for task in list(_TASKS):
        task.cancel()
    if _TASKS:
        await asyncio.gather(*_TASKS, return_exceptions=True)
    _TASKS.clear()


def pending() -> int:
    """How many deletions are queued. Exposed for tests and /stats."""
    return len(_TASKS)
