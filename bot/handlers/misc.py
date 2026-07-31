"""Misc commands: join, speed, info, source, active."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config
from bot.keyboards.inline import main_menu_kb
from bot.services.chat_settings import chat_settings
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.formatters import bq, bold, error_card, format_duration, italic, link
from bot.utils.helpers import extract_query, reply_error

router = Router(name="misc")


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    """Tell user how to add the assistant."""
    assistant = config.assistant_username
    if not assistant:
        await reply_error(message, "Assistant username not configured.")
        return
    await message.answer(
        f"👥 {bold('Add Assistant to Group')}\n\n"
        f"{bq(f'1. Add @{assistant} to your group\n2. Promote with VC manage permission\n3. Start a voice chat\n4. Use /play <song>')}\n\n"
        f"{italic('The assistant account streams audio/video into voice chats.')}",
        parse_mode="HTML",
    )


@router.message(Command("speed"))
async def cmd_speed(message: Message) -> None:
    query = extract_query(message)
    chat_id = message.chat.id
    if not query:
        speed = await chat_settings.get(chat_id, "speed")
        await message.answer(
            f"⚡ <b>Playback speed:</b> <code>{speed}x</code>\n\n"
            f"Usage: /speed 0.5–2.0 (requires replay)",
            parse_mode="HTML",
        )
        return
    try:
        speed = float(query)
        if not 0.5 <= speed <= 2.0:
            raise ValueError
    except ValueError:
        await reply_error(message, "Speed must be between 0.5 and 2.0")
        return

    await chat_settings.set(chat_id, "speed", speed)
    current = await queue_manager.get_current(chat_id)
    if current and stream_manager.is_playing(chat_id):
        current["_speed"] = speed
        await stream_manager.play(chat_id, current)
        await message.answer(f"⚡ Speed set to <b>{speed}x</b> — replaying with new speed.", parse_mode="HTML")
    else:
        await message.answer(f"⚡ Speed saved: <b>{speed}x</b> (applies on next play)", parse_mode="HTML")


@router.message(Command("info"))
async def cmd_info(message: Message) -> None:
    current = await queue_manager.get_current(message.chat.id)
    if not current:
        await reply_error(message, "Nothing is playing.")
        return

    lines = [
        f"ℹ️ {bold('Track Info')}\n",
        f"🎵 {bold(current.get('title', 'Unknown'))}",
        f"👤 {italic(current.get('artist', 'Unknown'))}",
        f"⏱ {format_duration(current.get('duration'))}",
        f"🙋 {current.get('requester', '—')}",
        f"📦 Source: {current.get('source', 'youtube')}",
    ]
    if current.get("url"):
        lines.append(f"🔗 {link('Open', current['url'])}")
    if current.get("is_live"):
        lines.append("📡 Live stream")
    if current.get("is_video"):
        lines.append("🎬 Video mode")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("source"))
async def cmd_source(message: Message) -> None:
    """Show supported media sources."""
    await message.answer(
        f"📦 {bold('Supported Sources')}\n\n"
        f"{bq('YouTube • YouTube Music • Spotify • SoundCloud\n"
             f"Twitch • Vimeo • TikTok • Instagram\n"
             f"Direct URLs • m3u8 live streams\n"
             f"Uploaded files: MP3, M4A, OGG, FLAC, MP4, MKV, WebM')}\n\n"
        f"{italic('Paste any URL or search by name.')}",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
    """Show active playback status."""
    chat_id = message.chat.id
    playing = stream_manager.is_playing(chat_id)
    paused = stream_manager.is_paused(chat_id)
    current = await queue_manager.get_current(chat_id)
    q_len = len(await queue_manager.get_queue(chat_id))

    if playing:
        status = "⏸ Paused" if paused else "▶️ Playing"
    else:
        status = "⏹ Idle"

    now = current["title"] if current else "—"
    await message.answer(
        f"📡 {bold('Active Status')}\n\n"
        f"{bq(f'Status: {status}\nNow: {now}\nQueue: {q_len} tracks')}",
        parse_mode="HTML",
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(
        f"🆔 Chat ID: <code>{message.chat.id}</code>\n"
        f"👤 Your ID: <code>{message.from_user.id if message.from_user else '—'}</code>",
        parse_mode="HTML",
    )
