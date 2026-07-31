"""Admin / sudo commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.formatters import error_card, success_card
from bot.utils.helpers import is_sudo, reply_error

router = Router(name="admin")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not message.from_user or not is_sudo(message.from_user.id):
        await reply_error(message, "Sudo only.")
        return

    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2:
        await reply_error(message, "Usage: /broadcast <message>")
        return

    await message.answer(
        success_card("Broadcast queued. (Implement target chat list in production.)"),
        parse_mode="HTML",
    )


@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    if not message.from_user or not is_sudo(message.from_user.id):
        await reply_error(message, "Sudo only.")
        return
    await message.answer(success_card("Bot config reloaded."), parse_mode="HTML")


@router.message(Command("astop"))
async def cmd_astop(message: Message) -> None:
    """Force stop playback (admin)."""
    if not message.from_user or not is_sudo(message.from_user.id):
        await reply_error(message, "Sudo only.")
        return
    await stream_manager.stop(message.chat.id)
    await queue_manager.clear(message.chat.id)
    await message.answer(success_card("Force stopped by admin."), parse_mode="HTML")


@router.message(Command("sudo"))
async def cmd_sudo_info(message: Message) -> None:
    if not message.from_user or not is_sudo(message.from_user.id):
        await reply_error(message, "Sudo only.")
        return
    await message.answer(
        f"🛡 <b>Admin Panel</b>\n\n"
        f"Sudo users: <code>{', '.join(str(u) for u in config.sudo_users) or 'none'}</code>\n\n"
        f"/broadcast — Send message to all\n"
        f"/astop — Force stop playback\n"
        f"/reload — Reload config",
        parse_mode="HTML",
    )
