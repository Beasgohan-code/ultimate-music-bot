"""Playback command handlers."""

from __future__ import annotations

import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import player_panel_kb, search_results_kb
from bot.services.autoleave import auto_leave
from bot.services.music import get_stream_url, is_live_url, is_url, search_youtube
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.formatters import error_card, now_playing_card, search_results_card
from bot.utils.helpers import ensure_assistant_in_chat, extract_query, is_group_chat, reply_error
from bot.utils.play_helpers import play_track

logger = logging.getLogger(__name__)
router = Router(name="play")


async def _resolve_and_play(
    message: Message,
    query: str,
    *,
    video: bool = False,
    live: bool = False,
    queue_only: bool = False,
) -> None:
    status = await message.answer("⏳ <b>Loading media…</b>", parse_mode="HTML")
    track = await get_stream_url(query, video=video, live=live)
    if not track:
        await status.edit_text(error_card("Could not find or extract media."), parse_mode="HTML")
        return
    await play_track(
        message, track,
        queue_only=queue_only or stream_manager.is_playing(message.chat.id),
        edit_msg=status,
    )


@router.message(Command("play"))
async def cmd_play(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /play <song name or URL>")
        return
    await _resolve_and_play(message, query)


@router.message(Command("song"))
async def cmd_song(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /song <song name or URL>")
        return
    await _resolve_and_play(message, query)


@router.message(Command("cplay"))
async def cmd_cplay(message: Message) -> None:
    """Channel/group play — same as play but always queues if something is playing."""
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /cplay <song name or URL>")
        return
    await _resolve_and_play(message, query, queue_only=stream_manager.is_playing(message.chat.id))


@router.message(Command("vplay"))
async def cmd_vplay(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /vplay <video name, URL, or MKV link>")
        return
    await _resolve_and_play(message, query, video=True)


@router.message(Command("vstream"))
async def cmd_vstream(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /vstream <live URL or m3u8 link>")
        return
    live = is_live_url(query) or is_url(query)
    await _resolve_and_play(message, query, video=True, live=live)


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /search <query>")
        return

    status = await message.answer("🔍 <b>Searching…</b>", parse_mode="HTML")
    results = await search_youtube(query, limit=8)
    if not results:
        await status.edit_text(error_card("No results found."), parse_mode="HTML")
        return

    from bot.utils.helpers import cache_search_results

    cache_search_results(results)
    await status.edit_text(
        search_results_card(query, results),
        parse_mode="HTML",
        reply_markup=search_results_kb(results, prefix="play"),
    )


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    if not stream_manager.is_playing(message.chat.id):
        await reply_error(message, "Nothing is playing.")
        return
    await stream_manager.pause(message.chat.id)
    await message.answer("⏸ <b>Paused</b>", parse_mode="HTML", reply_markup=player_panel_kb(True, True))


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not stream_manager.is_paused(message.chat.id):
        await reply_error(message, "Nothing to resume.")
        return
    await stream_manager.resume(message.chat.id)
    await message.answer("▶️ <b>Resumed</b>", parse_mode="HTML", reply_markup=player_panel_kb(True))


@router.message(Command("skip"))
async def cmd_skip(message: Message) -> None:
    chat_id = message.chat.id
    if not stream_manager.is_playing(chat_id):
        await reply_error(message, "Nothing is playing.")
        return
    next_track = await stream_manager.skip(chat_id)
    if next_track:
        loop = await queue_manager.get_loop(chat_id)
        vol = await queue_manager.get_volume(chat_id)
        card = now_playing_card(
            next_track["title"],
            next_track.get("artist", ""),
            next_track.get("duration"),
            next_track.get("requester", ""),
            loop_mode=loop.value,
            volume=vol,
        )
        await message.answer(card, parse_mode="HTML", reply_markup=player_panel_kb(True))
    else:
        await message.answer("⏹ <b>Queue finished.</b>", parse_mode="HTML")


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    await stream_manager.stop(message.chat.id)
    await queue_manager.clear(message.chat.id)
    await message.answer("⏹ <b>Stopped & queue cleared.</b>", parse_mode="HTML")


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    from bot.handlers.callbacks import send_queue_view

    await send_queue_view(message)


@router.message(Command("now"))
async def cmd_now(message: Message) -> None:
    current = await queue_manager.get_current(message.chat.id)
    if not current:
        await reply_error(message, "Nothing is playing right now.")
        return
    loop = await queue_manager.get_loop(message.chat.id)
    vol = await queue_manager.get_volume(message.chat.id)
    card = now_playing_card(
        current["title"],
        current.get("artist", ""),
        current.get("duration"),
        current.get("requester", ""),
        video=current.get("is_video", False),
        is_live=current.get("is_live", False),
        loop_mode=loop.value,
        volume=vol,
    )
    await message.answer(card, parse_mode="HTML", reply_markup=player_panel_kb(stream_manager.is_playing(message.chat.id)))


@router.message(Command("shuffle"))
async def cmd_shuffle(message: Message) -> None:
    await queue_manager.shuffle(message.chat.id)
    await message.answer("🔀 <b>Queue shuffled!</b>", parse_mode="HTML")


@router.message(Command("loop"))
async def cmd_loop(message: Message) -> None:
    mode = await queue_manager.toggle_loop(message.chat.id)
    await message.answer(f"🔁 Loop mode: <b>{mode.value.title()}</b>", parse_mode="HTML")


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    count = await queue_manager.clear(message.chat.id)
    await message.answer(f"🗑 Cleared <b>{count}</b> track(s) from queue.", parse_mode="HTML")


@router.message(Command("volume"))
async def cmd_volume(message: Message) -> None:
    query = extract_query(message)
    chat_id = message.chat.id
    if not query:
        vol = await queue_manager.get_volume(chat_id)
        await message.answer(f"🔊 Current volume: <b>{vol}%</b>\n\nUsage: /volume 1-200", parse_mode="HTML")
        return
    try:
        vol = int(query)
    except ValueError:
        await reply_error(message, "Volume must be a number between 1 and 200.")
        return
    vol = await stream_manager.change_volume(chat_id, vol)
    await message.answer(f"🔊 Volume set to <b>{vol}%</b>", parse_mode="HTML")


@router.message(F.audio | F.voice | F.video | F.document)
async def handle_media_file(message: Message, bot: Bot) -> None:
    """Play uploaded audio/video files (MP3, MKV, MP4, etc.)."""
    if not is_group_chat(message):
        return

    file = message.audio or message.voice or message.video or message.document
    if not file:
        return

    mime = getattr(file, "mime_type", "") or ""
    fname = getattr(file, "file_name", "") or "media"
    allowed = mime.startswith(("audio/", "video/")) or fname.lower().endswith(
        (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".mp4", ".mkv", ".webm", ".avi", ".mov")
    )
    if not allowed:
        return

    err = await ensure_assistant_in_chat(bot, message.chat.id)
    if err:
        return

    status = await message.reply("⏳ <b>Processing file…</b>", parse_mode="HTML")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, fname)
    await bot.download(file, destination=path)

    from bot.services.music import resolve_telegram_file

    title = getattr(file, "title", None) or getattr(file, "file_name", "Uploaded Media")
    track = await resolve_telegram_file(path, title)
    track["requester"] = message.from_user.full_name if message.from_user else "Unknown"
    track["is_video"] = mime.startswith("video/") or fname.lower().endswith((".mp4", ".mkv", ".webm"))

    chat_id = message.chat.id
    if stream_manager.is_playing(chat_id):
        pos = await queue_manager.add(chat_id, track)
        await status.edit_text(f"✅ File queued at <b>#{pos}</b>", parse_mode="HTML")
    else:
        await play_track(message, track, force=True, edit_msg=status)
