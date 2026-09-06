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
from bot.utils.cards import success_card, track_info_card
from bot.utils.formatters import bq, bold, error_card, format_duration, italic, link
from bot.utils.rich import send_card
from bot.utils.helpers import extract_query, reply_error

router = Router(name="misc")


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    """Tell user how to add the assistant."""
    assistant = config.assistant_username
    if not assistant:
        await reply_error(message, "Assistant username not configured.")
        return
    steps = (
        f"1. Add @{assistant} to your group\n"
        f"2. Promote it with the 'manage voice chats' permission\n"
        f"3. Start a voice chat\n"
        f"4. Use /play <song>"
    )
    await message.answer(
        f"👥 {bold('Add Assistant to Group')}\n\n"
        f"{bq(steps)}\n\n"
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
        await send_card(
            message,
            success_card(f"Speed set to {speed}x", "Replaying from the top at the new speed."),
        )
    else:
        await send_card(
            message,
            success_card(f"Speed saved: {speed}x", "It applies to the next track you play."),
        )


@router.message(Command("info"))
async def cmd_info(message: Message) -> None:
    current = await queue_manager.get_current(message.chat.id)
    if not current:
        await reply_error(message, "Nothing is playing.")
        return

    # track_info_card already renders every one of these fields as rich
    # blocks; hand-assembling the same lines here meant /info was the one
    # place the styling never reached.
    elapsed = stream_manager.elapsed(message.chat.id)
    await send_card(message, track_info_card(current, elapsed))


@router.message(Command("source"))
async def cmd_source(message: Message) -> None:
    """Show supported media sources."""
    await message.answer(
        f"📦 {bold('Supported Sources')}\n\n"
        f"{bq('YouTube, YouTube Music, Spotify, Apple Music, Deezer, SoundCloud, Bandcamp, Twitch, Vimeo, TikTok, Instagram, direct URLs, m3u8 live streams, and uploaded MP3/MKV/MP4 files.')}\n\n"
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
    summary = f"Status: {status}\nNow: {now}\nQueue: {q_len} tracks"
    await message.answer(
        f"📡 {bold('Active Status')}\n\n{bq(summary)}",
        parse_mode="HTML",
    )
