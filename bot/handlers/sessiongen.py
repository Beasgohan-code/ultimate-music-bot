"""`/genstring` — generate the assistant session over a private chat.

Owner-only and DM-only. A Telegram login code grants complete access to an
account, so this refuses to run anywhere it could be read by someone else,
and it deletes the messages carrying the code and password as soon as they
have been used.
"""

from __future__ import annotations

import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.config import config
from bot.services.sessiongen import save, session_generator
from bot.utils.cards import error_card, success_card
from bot.utils.rich import RichCard, b, c, i, plain, send_card

logger = logging.getLogger(__name__)
router = Router(name="sessiongen")


class GenString(StatesGroup):
    phone = State()
    code = State()
    password = State()


def _is_owner(message: Message) -> bool:
    user = message.from_user
    return bool(user and user.id in config.owners)


async def _guard(message: Message) -> bool:
    """Owner + private chat, with a distinct refusal for each failure."""
    if not _is_owner(message):
        return False  # silence: a stranger should not learn the command exists
    if message.chat.type != "private":
        await send_card(
            message,
            error_card(
                "Not here.",
                "A login code grants full access to the account. "
                "Send /genstring to me in a private chat instead.",
            ),
        )
        return False
    return True


async def _scrub(message: Message) -> None:
    """Delete a message that contained a credential."""
    with contextlib.suppress(Exception):
        await message.delete()


@router.message(Command("genstring", "gensession"))
async def cmd_genstring(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return

    if not config.api_id or not config.api_hash:
        await send_card(
            message,
            error_card(
                "API_ID and API_HASH are not set.",
                "Add them from https://my.telegram.org, redeploy, then try again.",
            ),
        )
        return

    await state.clear()
    await session_generator.cancel(message.from_user.id)
    await state.set_state(GenString.phone)

    card = (
        RichCard()
        .heading([plain("🔑 "), b("Assistant Session")], size=1)
        .para(
            [
                plain("Send the phone number of the "),
                b("assistant"),
                plain(" account."),
            ]
        )
        .quote(
            [
                [plain("Include the country code, e.g. "), c("+919876543210")],
                [i("Use a spare account — this grants full access to it.")],
            ]
        )
        .footer("/cancel to stop")
    )
    await send_card(message, card)


@router.message(Command("cancel"), StateFilter(GenString))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await session_generator.cancel(message.from_user.id)
    await send_card(message, success_card("Cancelled.", "Nothing was saved."))


@router.message(StateFilter(GenString.phone), F.text)
async def got_phone(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return

    ok, detail = await session_generator.start(message.from_user.id, message.text)
    if not ok:
        await send_card(message, error_card("Could not send a code.", detail))
        await state.clear()
        return

    await state.set_state(GenString.code)
    card = (
        RichCard()
        .heading([plain("📲 "), b("Code Sent")], size=1)
        .para([plain("Telegram sent a login code to "), c(detail), plain(".")])
        .quote(
            [
                [plain("Send the code here as digits, e.g. "), c("12345")],
                [i("I delete your message straight after reading it.")],
            ]
        )
        .footer("/cancel to stop")
    )
    await send_card(message, card)


@router.message(StateFilter(GenString.code), F.text)
async def got_code(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await _guard(message):
        return

    code = message.text
    await _scrub(message)  # the code is a credential; do not leave it in the chat

    status, detail = await session_generator.submit_code(message.from_user.id, code)

    if status == "password":
        await state.set_state(GenString.password)
        await send_card(
            message,
            RichCard()
            .heading([plain("🔐 "), b("Two-Step Verification")], size=1)
            .para([plain(detail)])
            .quote([[plain("Send the account password here.")],
                    [i("I delete it as soon as it is used.")]])
            .footer("/cancel to stop"),
        )
        return

    if status == "error":
        # A wrong code keeps the attempt alive, so stay in this state.
        await send_card(message, error_card("Sign-in failed.", detail))
        if "expired" in detail or "start again" in detail:
            await state.clear()
        return

    await _persist(message, state, detail)


@router.message(StateFilter(GenString.password), F.text)
async def got_password(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return

    password = message.text
    await _scrub(message)

    status, detail = await session_generator.submit_password(
        message.from_user.id, password
    )
    if status == "error":
        await send_card(message, error_card("Sign-in failed.", detail))
        if "expired" in detail or "start again" in detail:
            await state.clear()
        return

    await _persist(message, state, detail)


async def _persist(message: Message, state: FSMContext, session_string: str) -> None:
    """Save the finished session and report where it landed."""
    await state.clear()
    destinations = await save(session_string)

    card = RichCard().heading([plain("✅ "), b("Session Saved")], size=1)
    if destinations:
        card.quote([[plain("Stored in "), c(dest)] for dest in destinations])
    else:
        card.quote([[i("Nothing could be written — check the logs.")]])

    # Deliberately not printing the string. Telegram chats get backed up,
    # forwarded and screenshotted, and this one credential is the whole
    # account. /genstring can always be run again.
    card.para(
        [i("The session itself is not shown here — it is a full-access credential.")]
    )
    card.quote(
        [
            [b("Restart the bot"), plain(" to connect the assistant.")],
            [
                plain("On Render also paste it into "),
                c("SESSION_STRING"),
                plain(" — the disk is wiped on redeploy."),
            ],
        ]
    )
    card.footer("/genstring again if the session ever stops working")
    await send_card(message, card)
    logger.info("A new assistant session was generated and saved.")
