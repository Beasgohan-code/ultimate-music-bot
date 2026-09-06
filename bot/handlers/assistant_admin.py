"""Assistant account management — profile, dialogs, and housekeeping.

Ported from FallenMusic's ``Modules/assistant.py``, ``leaveall.py`` and
``cleaner.py``.  The assistant is a full user account, so the bot cannot change
it through the Bot API; every command here drives the Pyrogram client instead.

FallenMusic's originals assumed the client always exists and wrapped each call
in a bare ``except`` that reported a generic failure.  Here a missing assistant
is reported as exactly that, and Telegram's own error text is surfaced, because
"failed to change pfp" tells an operator nothing about *why*.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import CACHE_DIR, DOWNLOAD_DIR, config
from bot.services import assistant
from bot.services.stream import stream_manager
from bot.utils.cards import error_card, success_card
from bot.utils.guards import is_sudo
from bot.utils.rich import RichCard, b, c, i, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="assistant-admin")


async def _sudo_only(message: Message) -> bool:
    if not message.from_user or not is_sudo(message.from_user.id):
        await send_html(message, "🚫 <b>This command is for the bot owner only.</b>")
        return False
    return True


def _client():
    """The assistant's Pyrogram client, or None when it isn't configured."""
    from bot.services import stream as stream_module

    return getattr(stream_module, "_user_client", None)


async def _need_client(message: Message):
    client = _client()
    if client is None:
        await send_card(
            message,
            error_card(
                "No assistant account is configured.",
                "Set SESSION_STRING so the bot has a user account to drive.",
            ),
        )
        return None
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("setpfp", "asspfp"))
async def cmd_setpfp(message: Message) -> None:
    if not await _sudo_only(message):
        return
    client = await _need_client(message)
    if client is None:
        return

    reply = message.reply_to_message
    if not reply or not reply.photo:
        await send_card(
            message,
            error_card("Reply to a photo.", "That photo becomes the assistant's avatar."),
        )
        return

    status = await message.answer("🖼 <b>Updating the assistant's photo…</b>", parse_mode="HTML")
    path = CACHE_DIR / f"pfp_{int(time.time())}.jpg"
    try:
        await message.bot.download(reply.photo[-1].file_id, destination=path)
        await client.set_profile_photo(photo=str(path))
        await status.edit_text(
            success_card(f"{assistant.label()} has a new profile photo.").to_html(),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("setpfp failed: %s", exc)
        await status.edit_text(
            error_card("Couldn't change the photo.", str(exc)).to_html(), parse_mode="HTML"
        )
    finally:
        with contextlib.suppress(Exception):
            path.unlink(missing_ok=True)


@router.message(Command("delpfp", "delasspfp"))
async def cmd_delpfp(message: Message) -> None:
    if not await _sudo_only(message):
        return
    client = await _need_client(message)
    if client is None:
        return

    try:
        photos = [p async for p in client.get_chat_photos("me")]
        if not photos:
            await send_card(message, error_card("The assistant has no profile photo to delete."))
            return
        await client.delete_profile_photos(photos[0].file_id)
        await send_card(
            message, success_card(f"Removed {assistant.label()}'s profile photo.")
        )
    except Exception as exc:
        logger.warning("delpfp failed: %s", exc)
        await send_card(message, error_card("Couldn't delete the photo.", str(exc)))


@router.message(Command("setbio", "assbio"))
async def cmd_setbio(message: Message) -> None:
    if not await _sudo_only(message):
        return
    client = await _need_client(message)
    if client is None:
        return

    text = _payload(message)
    if not text:
        await send_card(
            message,
            error_card("Give me the new bio.", "Usage: /setbio <text> — or reply to a message."),
        )
        return
    if len(text) > 70:  # Telegram's limit; failing early beats a cryptic API error
        await send_card(
            message,
            error_card(f"That bio is {len(text)} characters.", "Telegram allows at most 70."),
        )
        return

    try:
        await client.update_profile(bio=text)
        await send_card(message, success_card(f"{assistant.label()}'s bio is now: {text}"))
    except Exception as exc:
        await send_card(message, error_card("Couldn't change the bio.", str(exc)))


@router.message(Command("setname", "assname"))
async def cmd_setname(message: Message) -> None:
    if not await _sudo_only(message):
        return
    client = await _need_client(message)
    if client is None:
        return

    text = _payload(message)
    if not text:
        await send_card(
            message,
            error_card("Give me the new name.", "Usage: /setname <text> — or reply to a message."),
        )
        return

    first, _, last = text.partition(" ")
    try:
        await client.update_profile(first_name=first[:64], last_name=last[:64])
        # The cached label is now stale.
        assistant.reset()
        await send_card(message, success_card(f"The assistant is now called {text}."))
    except Exception as exc:
        await send_card(message, error_card("Couldn't change the name.", str(exc)))


def _payload(message: Message) -> str:
    """Text after the command, or the replied-to message's text."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        return parts[1].strip()
    reply = message.reply_to_message
    if reply and (reply.text or reply.caption):
        return (reply.text or reply.caption or "").strip()
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Dialogs
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("leaveall", "assleaveall"))
async def cmd_leaveall(message: Message) -> None:
    """Make the assistant leave every group it isn't actively needed in.

    FallenMusic hard-coded the chats to keep. Here anything with a live stream
    is skipped automatically, so this can't kill a playing voice chat.
    """
    if not await _sudo_only(message):
        return
    client = await _need_client(message)
    if client is None:
        return

    busy = set(stream_manager.active_chats)
    status = await message.answer(
        f"🚪 <b>{assistant.label()} is leaving chats…</b>", parse_mode="HTML"
    )

    left = failed = skipped = 0
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.type.name in ("PRIVATE", "BOT"):
                continue
            if chat.id in busy:
                skipped += 1
                continue
            try:
                await client.leave_chat(chat.id)
                left += 1
                assistant.forget(chat.id)
            except Exception as exc:
                # Pyrogram raises its own FloodWait, not aiogram's.
                wait = getattr(exc, "value", None)
                if isinstance(wait, int) and wait <= 60:
                    await asyncio.sleep(wait)
                    with contextlib.suppress(Exception):
                        await client.leave_chat(chat.id)
                        left += 1
                        continue
                failed += 1
            await asyncio.sleep(0.4)
    except Exception as exc:
        logger.warning("leaveall iteration failed: %s", exc)

    rows = [["Left", c(str(left))], ["Failed", c(str(failed))]]
    if skipped:
        rows.append(["Skipped (playing)", c(str(skipped))])
    card = RichCard().heading([plain("🚪 "), b("Assistant Cleanup")], size=1).table(
        ["Result", "Count"], rows
    )
    try:
        await status.edit_text(card.to_html(), parse_mode="HTML")
    except Exception:
        await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Housekeeping
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("rmdownloads", "clearcache"))
async def cmd_clearcache(message: Message) -> None:
    """Delete cached downloads and rendered thumbnails.

    FallenMusic shelled out to ``rm -rf *.webm *.jpg *.png`` from the working
    directory, which deletes whatever happens to be there. This walks only the
    two directories the bot actually owns and reports the space reclaimed.
    """
    if not await _sudo_only(message):
        return

    freed = 0
    removed = 0
    for folder in (DOWNLOAD_DIR, CACHE_DIR):
        if not folder.exists():
            continue
        for entry in folder.iterdir():
            try:
                if entry.is_dir():
                    freed += sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    freed += entry.stat().st_size
                    entry.unlink()
                removed += 1
            except Exception:
                logger.debug("Could not remove %s", entry, exc_info=True)

    megabytes = freed / (1024 * 1024)
    card = (
        RichCard()
        .heading([plain("🧹 "), b("Cache Cleared")], size=1)
        .table(
            ["Metric", "Value"],
            [["Entries removed", c(str(removed))], ["Space freed", c(f"{megabytes:.1f} MB")]],
        )
    )
    await send_card(message, card)


@router.message(Command("assistant", "assinfo"))
async def cmd_assistant_info(message: Message) -> None:
    """Show who the assistant is and whether it is reachable."""
    if not await _sudo_only(message):
        return

    uid = await assistant.user_id()
    client = _client()
    rows = [
        ["Configured", c("yes" if client is not None else "no")],
        ["User id", c(str(uid) if uid else "unknown")],
        ["Name", c(assistant.label())],
        ["Active chats", c(str(len(stream_manager.active_chats)))],
    ]
    if config.assistant_username:
        rows.append(["Username", c(f"@{config.assistant_username.lstrip('@')}")])

    card = (
        RichCard()
        .heading([plain("🤝 "), b("Assistant")], size=1)
        .table(["Field", "Value"], rows)
    )
    if client is None:
        card.paragraph([i("Set SESSION_STRING to enable voice chat playback.")])
    await send_card(message, card)
