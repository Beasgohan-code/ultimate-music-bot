"""`/cookies` — install and inspect YouTube cookie jars at runtime.

Cookies are the single most effective fix for "playback failed": a datacenter
IP gets refused by YouTube, and a jar from a logged-in browser makes the
request look like a person. The machinery to validate and rotate jars already
existed, but the only way to *install* one was to set COOKIES_DATA and
redeploy — so a broken bot stayed broken until someone had a laptop to hand.

This lets the owner send the file to the bot and have playback work seconds
later. Owner-only and private-chat-only: a cookie jar is a live login to a
Google account, and posting one in a group hands that account to everyone in
it.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config
from bot.services.music import (
    RUNTIME_COOKIE_DIR,
    cookie_pool,
    cookie_status,
    inspect_cookies,
)
from bot.utils.cards import error_card, success_card
from bot.utils.rich import RichCard, b, c, i, plain, send_card

logger = logging.getLogger(__name__)
router = Router(name="cookies")

#: Refuse anything larger. A Netscape jar for one domain is a few KB; a
#: megabyte of it is someone's whole browser profile, or a mistake.
MAX_BYTES = 512 * 1024


def _is_owner(message: Message) -> bool:
    user = message.from_user
    return bool(user and user.id in config.owners)


async def _guard(message: Message) -> bool:
    if not _is_owner(message):
        return False  # silent: a stranger should not learn this exists
    if message.chat.type != "private":
        await send_card(
            message,
            error_card(
                "Not here.",
                "A cookie jar is a live login to a Google account. "
                "Send it to me in a private chat instead.",
            ),
        )
        return False
    return True


def _summary_card() -> RichCard:
    """Current cookie health, as the operator would want to see it."""
    pool = cookie_pool()
    card = RichCard().heading([plain("🍪 "), b("Cookies")], size=1)

    if not pool:
        card.quote(
            [
                [plain("Status: "), c("none usable")],
                [i("YouTube will refuse this server on most requests.")],
            ]
        )
    else:
        rows = []
        for path in pool:
            info = inspect_cookies(path)
            auth = "logged in" if info.get("authenticated") else "anonymous"
            rows.append(
                [Path(path).name, f"{info.get('live', 0)} live", auth]
            )
        card.table(["Jar", "Cookies", "Account"], rows)
        card.quote([[plain("Status: "), c(cookie_status())]])

    card.para([b("To add one:")])
    card.bullets(
        [
            "Install a 'Get cookies.txt' browser extension",
            "Open youtube.com signed in, export for that domain",
            "Send the .txt file here — that is all",
        ],
        ordered=True,
    )
    card.footer("/cookies clear removes every uploaded jar")
    return card


@router.message(Command("cookies", "cookie"))
async def cmd_cookies(message: Message) -> None:
    if not await _guard(message):
        return

    arg = ((message.text or "").split(maxsplit=1) + [""])[1].strip().lower()

    if arg in ("clear", "reset", "remove"):
        removed = 0
        if RUNTIME_COOKIE_DIR.is_dir():
            for path in RUNTIME_COOKIE_DIR.glob("*.txt"):
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", path, exc)
        await send_card(
            message,
            success_card(
                f"Removed {removed} uploaded jar(s).",
                "COOKIES_DATA and COOKIES_DIR are untouched.",
            ),
        )
        return

    await send_card(message, _summary_card())


@router.message(F.document, F.chat.type == "private")
async def got_cookie_file(message: Message, bot: Bot) -> None:
    """Accept a .txt document from the owner as a cookie jar."""
    if not _is_owner(message):
        return

    document = message.document
    name = (document.file_name or "").lower()
    if not name.endswith(".txt"):
        return  # not for us; let other handlers see it

    if (document.file_size or 0) > MAX_BYTES:
        await send_card(
            message,
            error_card(
                "That file is too large for a cookie jar.",
                f"Expected a few KB, got {(document.file_size or 0) // 1024} KB. "
                "Export cookies for youtube.com only.",
            ),
        )
        return

    RUNTIME_COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    target = RUNTIME_COOKIE_DIR / f"upload_{int(time.time())}.txt"

    try:
        file = await bot.get_file(document.file_id)
        await bot.download_file(file.file_path, destination=str(target))
    except Exception as exc:
        logger.warning("Cookie download failed: %s", exc)
        await send_card(
            message, error_card("Could not download that file.", str(exc)[:200])
        )
        return

    # Validate before keeping it. An expired jar behaves exactly like no jar,
    # so silently accepting one would leave playback broken with a green
    # confirmation on screen — the worst possible outcome.
    info = inspect_cookies(str(target))
    live = info.get("live", 0)

    if not live:
        try:
            target.unlink()
        except OSError:
            pass
        await send_card(
            message,
            error_card(
                "That jar has no live cookies.",
                f"{info.get('total', 0)} cookie(s), all expired. Sign in to "
                "YouTube in your browser, then export again — the file is "
                "only valid while that session is.",
            ),
        )
        return

    # Delete the message: it contains a live login, and Telegram keeps it
    # in the chat (and any backup) until told otherwise.
    try:
        await message.delete()
    except Exception as exc:
        logger.debug("Could not delete the uploaded jar message: %s", exc)

    card = (
        RichCard()
        .heading([plain("✅ "), b("Cookies Installed")], size=1)
        .quote(
            [
                [c(f"{live} live cookie(s)"), plain("  •  "),
                 plain("logged in" if info.get("authenticated") else "anonymous")],
                [plain("Jars in rotation: "), c(str(len(cookie_pool())))],
            ]
        )
    )
    if not info.get("authenticated"):
        card.para(
            [
                i(
                    "No login cookie found — this helps with consent and region "
                    "checks but will not beat a hard IP block. Export while "
                    "signed in for the full effect."
                )
            ]
        )
    card.para([b("Try /play now.")])
    card.footer("I deleted your upload — it was a live credential")
    await send_card(message, card)
    logger.info("A cookie jar was installed at runtime (%d live)", live)
