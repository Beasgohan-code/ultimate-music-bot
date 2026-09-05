"""Playback command handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import player_panel_kb, search_results_kb
from bot.config import config
from bot.services import platforms
from bot.services.autoleave import auto_leave
from bot.services.music import (
    BLOCKED_HINT,
    get_stream_url,
    is_live_url,
    is_url,
    last_error as music_last_error,
    looks_unsupported,
    looks_blocked,
    search_youtube,
)
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.helpers import ensure_assistant_in_chat, extract_query, is_group_chat, reply_error
from bot.utils.cards import (
    error_card,
    import_card,
    now_playing_card,
    search_card,
    success_card,
    voteskip_card,
)
from bot.utils.play_helpers import can_play, play_track
from bot.utils.rich import send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="play")


async def _import_platform_link(message: Message, query: str, *, video: bool, status) -> bool:
    """Handle a Spotify / Apple Music / Deezer link. True when handled.

    A single track just becomes a normal search. An album or playlist is worth
    expanding: play the first track and queue the rest, so pasting a playlist
    link does the obvious thing instead of playing one song from it.
    """
    if not platforms.detect(query):
        blocked = platforms.unsupported_service(query)
        if blocked:
            await send_card(
                message,
                error_card(
                    f"{blocked} links can't be played.",
                    "Its audio is DRM-locked with no public metadata. "
                    "Paste a Spotify, Apple Music, Deezer or YouTube link, "
                    "or just send the song name.",
                ),
                edit=status,
            )
            return True
        return False

    resolved = await platforms.resolve(query)
    if not resolved:
        await send_card(
            message,
            error_card(
                "That link could not be read.",
                "It may be private, region-locked, or a format I don't know yet.",
            ),
            edit=status,
        )
        return True

    queries = resolved.queries()
    if resolved.is_single and len(queries) == 1:
        return False  # fall through: resolve_query() turns it into a search

    await status.edit_text(
        f"⏳ <b>Importing {len(queries)} tracks…</b>", parse_mode="HTML"
    )

    first = await get_stream_url(queries[0], video=video)
    if not first:
        await send_card(
            message,
            error_card(
                "Couldn't find the first track anywhere.",
                "The rest of the list was not imported.",
            ),
            edit=status,
        )
        return True

    # Carry the original artwork through — YouTube's thumbnail for a matched
    # track is often a random video still, the album art is better.
    if resolved.tracks and resolved.tracks[0].get("artwork"):
        first["thumbnail"] = resolved.tracks[0]["artwork"]
    first["source"] = resolved.platform

    started = await play_track(message, first, edit_msg=status)
    if not started:
        return True

    # Resolve the rest concurrently, but stay polite to the extractor.
    queued = 0
    semaphore = asyncio.Semaphore(4)

    async def _add(idx: int, term: str) -> None:
        nonlocal queued
        async with semaphore:
            track = await get_stream_url(term, video=video)
        if not track:
            return
        meta = resolved.tracks[idx] if idx < len(resolved.tracks) else {}
        if meta.get("artwork"):
            track["thumbnail"] = meta["artwork"]
        track["source"] = resolved.platform
        track["requester"] = message.from_user.full_name if message.from_user else ""
        try:
            await queue_manager.add(message.chat.id, track)
            queued += 1
        except ValueError:
            pass  # queue full — the card reports the shortfall

    await asyncio.gather(*(_add(i, q) for i, q in enumerate(queries[1:], start=1)))

    await send_card(message, import_card(resolved, added=1 + queued, queued=queued))
    return True


async def _resolve_and_play(
    message: Message,
    query: str,
    *,
    video: bool = False,
    live: bool = False,
    queue_only: bool = False,
) -> None:
    if not await can_play(message):
        return
    status = await message.answer("⏳ <b>Loading media…</b>", parse_mode="HTML")

    if not live and await _import_platform_link(message, query, video=video, status=status):
        return

    track = await get_stream_url(query, video=video, live=live)
    if not track:
        # "No results" and "YouTube blocked this server" look identical to the
        # user but need completely different fixes, so say which one it is.
        err = music_last_error()
        if looks_blocked(err):
            await send_card(
                message,
                error_card("YouTube refused this request.", BLOCKED_HINT),
                edit=status,
            )
        elif looks_unsupported(err):
            await send_card(
                message,
                error_card(
                    "That link isn't playable media.",
                    "It points at a web page, not a song. Send a track link "
                    "or just the song name.",
                ),
                edit=status,
            )
        else:
            await send_card(
                message,
                error_card(
                    "I could not find or extract that media.",
                    "Try a different search term or paste a direct link.",
                ),
                edit=status,
            )
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


@router.message(Command("song", "mp3", "download"))
async def cmd_song(message: Message) -> None:
    """Download a track and send it as a file (cached by file_id)."""
    query = extract_query(message)
    if not query:
        current = await queue_manager.get_current(message.chat.id)
        query = (current or {}).get("url") or (current or {}).get("title", "")
    if not query:
        await reply_error(message, "Usage: /song <song name or URL>")
        return

    if not config.enable_downloads:
        await send_card(message, error_card("Downloads are disabled on this instance."))
        return

    status = await message.answer("🔎 <b>Finding that track…</b>", parse_mode="HTML")
    track = await get_stream_url(query)
    if not track:
        await send_card(
            message,
            error_card("I could not find that track.", "Try a different name or a direct link."),
            edit=status,
        )
        return

    from bot.services.downloads import DownloadError, cached_file_id, get_or_send_audio

    cached = await cached_file_id(track)
    try:
        await status.edit_text(
            "📤 <b>Sending…</b>" if cached else "⬇️ <b>Downloading…</b>", parse_mode="HTML"
        )
    except Exception:
        pass

    caption = f"🎵 <b>{track.get('title', 'Unknown')}</b>"
    if track.get("artist"):
        caption += f"\n👤 {track['artist']}"
    caption += f"\n\n<i>via {config.bot_name}</i>"

    try:
        await get_or_send_audio(message, track, caption=caption)
    except DownloadError as exc:
        await send_card(message, error_card(str(exc)), edit=status)
        return
    except Exception as exc:
        logger.error("Song delivery failed: %s", exc)
        await send_card(message, error_card("Something went wrong sending that file."), edit=status)
        return

    try:
        await status.delete()
    except Exception:
        pass


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
        if looks_blocked(music_last_error()):
            await send_card(
                message,
                error_card("YouTube refused this request.", BLOCKED_HINT),
                edit=status,
            )
        else:
            await send_card(
                message, error_card("No results found.", "Try different keywords."), edit=status
            )
        return

    from bot.utils.helpers import cache_search_results

    cache_search_results(results)
    await send_card(
        message,
        search_card(query, results),
        reply_markup=search_results_kb(results, prefix="play"),
        edit=status,
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

    if not await _may_skip_now(message):
        return
    await _do_skip(message)


async def _may_skip_now(message: Message) -> bool:
    """True if this user can skip outright; otherwise runs the vote."""
    from bot.services.voteskip import count_listeners, voteskip
    from bot.utils.guards import is_admin_or_auth, is_sudo

    chat_id = message.chat.id
    user = message.from_user
    if not user or not is_group_chat(message):
        return True
    if not await voteskip.enabled(chat_id):
        return True

    current = await queue_manager.get_current(chat_id)
    # Whoever queued the track may always skip their own request.
    if current and current.get("requester") == user.full_name:
        return True
    if is_sudo(user.id) or await is_admin_or_auth(message.bot, chat_id, user.id):
        return True

    listeners = await count_listeners(message.bot, chat_id)
    needed = voteskip.needed(listeners, await voteskip.ratio(chat_id))
    votes, is_new = voteskip.add_vote(chat_id, user.id, current)

    if votes >= needed:
        voteskip.reset(chat_id)
        await send_card(
            message,
            success_card(f"Vote passed — skipping. ({votes}/{needed})"),
        )
        return True

    if not is_new:
        await send_card(message, error_card("You have already voted to skip."))
        return False

    await send_card(
        message,
        voteskip_card(votes, needed, current.get("title", "this track") if current else "this track"),
    )
    return False


async def _do_skip(message: Message) -> None:
    from bot.services.voteskip import voteskip

    chat_id = message.chat.id
    voteskip.reset(chat_id)
    next_track = await stream_manager.skip(chat_id)
    if next_track:
        await send_card(
            message,
            now_playing_card(
                next_track,
                elapsed=0,
                queue_len=await queue_manager.size(chat_id),
                volume=await queue_manager.get_volume(chat_id),
                loop_mode=(await queue_manager.get_loop(chat_id)).value,
            ),
            reply_markup=player_panel_kb(True),
        )
    else:
        await send_card(message, success_card("Queue finished.", "Add more with /play."))


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    await stream_manager.stop(message.chat.id)
    await queue_manager.clear(message.chat.id)
    await message.answer("⏹ <b>Stopped & queue cleared.</b>", parse_mode="HTML")


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    from bot.handlers.callbacks import send_queue_view

    await send_queue_view(message)


@router.message(Command("now", "np", "current"))
async def cmd_now(message: Message) -> None:
    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    if not current:
        await send_card(message, error_card("Nothing is playing right now.", "Start with /play <song>."))
        return
    card = now_playing_card(
        current,
        elapsed=stream_manager.elapsed(chat_id),
        queue_len=await queue_manager.size(chat_id),
        volume=await queue_manager.get_volume(chat_id),
        loop_mode=(await queue_manager.get_loop(chat_id)).value,
    )
    await send_card(message, card, reply_markup=player_panel_kb(stream_manager.is_playing(chat_id)))


@router.message(Command("shuffle"))
async def cmd_shuffle(message: Message) -> None:
    await queue_manager.shuffle(message.chat.id)
    await message.answer("🔀 <b>Queue shuffled!</b>", parse_mode="HTML")


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
