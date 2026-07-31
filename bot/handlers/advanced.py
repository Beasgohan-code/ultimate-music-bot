"""Advanced commands: playlist, playnow, mood, favorites, radio, download."""

from __future__ import annotations

import logging
import os
import tempfile

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from bot.keyboards.inline import favorites_kb, mood_kb, player_panel_kb, radio_kb, search_results_kb
from bot.services.autoleave import auto_leave
from bot.services.favorites import favorites_store
from bot.services.history import history_tracker
from bot.services.music import (
    get_mood_tracks,
    get_playlist,
    get_stream_url,
    is_url,
    search_youtube,
)
from bot.services.queue import queue_manager
from bot.services.radio import find_station, get_station, list_stations
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.formatters import (
    error_card,
    favorites_card,
    mood_card,
    now_playing_card,
    radio_card,
    search_results_card,
    success_card,
)
from bot.utils.helpers import ensure_assistant_in_chat, extract_query, is_group_chat, reply_error

logger = logging.getLogger(__name__)
router = Router(name="advanced")


async def _play_track(message: Message, track: dict, *, front: bool = False, force: bool = False) -> None:
    chat_id = message.chat.id
    requester = message.from_user.full_name if message.from_user else "Unknown"
    track["requester"] = requester
    auto_leave.touch(chat_id)

    if is_group_chat(message):
        err = await ensure_assistant_in_chat(message.bot, chat_id)
        if err:
            await reply_error(message, err)
            return

    if force or not stream_manager.is_playing(chat_id):
        await stream_manager.play(chat_id, track)
        await history_tracker.record(chat_id, track)
        bot_stats.streams_started += 1
        loop = await queue_manager.get_loop(chat_id)
        vol = await queue_manager.get_volume(chat_id)
        await message.answer(
            now_playing_card(
                track["title"], track.get("artist", ""), track.get("duration"),
                requester, loop_mode=loop.value, volume=vol,
            ),
            parse_mode="HTML",
            reply_markup=player_panel_kb(True),
        )
    elif front:
        await queue_manager.add_front(chat_id, track)
        await message.answer(
            success_card(f"Added to front of queue: {track['title']}"),
            parse_mode="HTML",
        )
    else:
        pos = await queue_manager.add(chat_id, track)
        await message.answer(
            success_card(f"Queued at #{pos}: {track['title']}"),
            parse_mode="HTML",
        )


@router.message(Command("playlist"))
async def cmd_playlist(message: Message) -> None:
    query = extract_query(message)
    if not query or not is_url(query):
        await reply_error(message, "Usage: /playlist <YouTube playlist URL>")
        return

    status = await message.answer("📂 <b>Loading playlist…</b>", parse_mode="HTML")
    requester = message.from_user.full_name if message.from_user else "Unknown"
    tracks = await get_playlist(query, requester)
    if not tracks:
        await status.edit_text(error_card("Could not load playlist."), parse_mode="HTML")
        return

    chat_id = message.chat.id
    if stream_manager.is_playing(chat_id):
        added = await queue_manager.add_many(chat_id, tracks)
        await status.edit_text(
            success_card(f"Added {added} tracks from playlist to queue."),
            parse_mode="HTML",
        )
    else:
        first = tracks[0]
        resolved = await get_stream_url(first.get("url", query))
        if resolved:
            resolved["requester"] = requester
            await stream_manager.play(chat_id, resolved)
            await history_tracker.record(chat_id, resolved)
            bot_stats.streams_started += 1
            if len(tracks) > 1:
                await queue_manager.add_many(chat_id, tracks[1:])
            await status.edit_text(
                success_card(f"Playing playlist — {len(tracks)} tracks loaded."),
                parse_mode="HTML",
                reply_markup=player_panel_kb(True),
            )
        else:
            await status.edit_text(error_card("Failed to start playlist."), parse_mode="HTML")


@router.message(Command("playnow"))
async def cmd_playnow(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /playnow <song>")
        return
    status = await message.answer("⏳ <b>Loading…</b>", parse_mode="HTML")
    track = await get_stream_url(query)
    if not track:
        await status.edit_text(error_card("Track not found."), parse_mode="HTML")
        return
    await status.delete()
    await _play_track(message, track, force=True)


@router.message(Command("playnext"))
async def cmd_playnext(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await reply_error(message, "Usage: /playnext <song>")
        return
    status = await message.answer("⏳ <b>Loading…</b>", parse_mode="HTML")
    track = await get_stream_url(query)
    if not track:
        await status.edit_text(error_card("Track not found."), parse_mode="HTML")
        return
    await status.delete()
    await _play_track(message, track, front=True)


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    query = extract_query(message)
    if not query or not query.isdigit():
        await reply_error(message, "Usage: /remove <queue position>")
        return
    idx = int(query) - 1
    removed = await queue_manager.remove_at(message.chat.id, idx)
    if removed:
        await message.answer(
            success_card(f"Removed: {removed['title']}"),
            parse_mode="HTML",
        )
    else:
        await reply_error(message, "Invalid queue position.")


@router.message(Command("mood"))
async def cmd_mood(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await message.answer(
            mood_card(),
            parse_mode="HTML",
            reply_markup=mood_kb(),
        )
        return

    status = await message.answer(f"🎭 <b>Loading {query} mood…</b>", parse_mode="HTML")
    tracks = await get_mood_tracks(query, limit=8)
    if not tracks:
        await status.edit_text(error_card("No tracks found for this mood."), parse_mode="HTML")
        return

    from bot.utils.helpers import cache_search_results
    cache_search_results(tracks)
    await status.edit_text(
        search_results_card(f"{query} mood", tracks),
        parse_mode="HTML",
        reply_markup=search_results_kb(tracks),
    )


@router.message(Command("radio"))
async def cmd_radio(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await message.answer(radio_card(), parse_mode="HTML", reply_markup=radio_kb())
        return

    station = find_station(query)
    if not station:
        await reply_error(message, f"Station '{query}' not found. Try /radio")
        return

    status = await message.answer(f"📻 <b>Tuning to {station['name']}…</b>", parse_mode="HTML")
    track = await get_stream_url(station["url"], live=station["url"].startswith("http") and ".m3u8" not in station["url"])
    if not track:
        track = {
            "title": station["name"],
            "artist": station["genre"],
            "url": station["url"],
            "stream_url": station["url"],
            "is_live": True,
            "requester": message.from_user.full_name if message.from_user else "Unknown",
        }
    else:
        track["title"] = station["name"]
        track["is_live"] = True
    await status.delete()
    await _play_track(message, track, force=True)


@router.message(Command("fav"))
async def cmd_fav(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    current = await queue_manager.get_current(message.chat.id)
    if not current:
        await reply_error(message, "Nothing playing to favorite.")
        return
    added = await favorites_store.add(user_id, current)
    if added:
        await message.answer(success_card(f"Added to favorites: {current['title']}"), parse_mode="HTML")
    else:
        await reply_error(message, "Already in favorites.")


@router.message(Command("favs"))
async def cmd_favs(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    favs = await favorites_store.list(user_id)
    await message.answer(
        favorites_card(favs),
        parse_mode="HTML",
        reply_markup=favorites_kb(favs) if favs else None,
    )


@router.message(Command("unfav"))
async def cmd_unfav(message: Message) -> None:
    query = extract_query(message)
    user_id = message.from_user.id if message.from_user else 0
    if not query or not query.isdigit():
        await reply_error(message, "Usage: /unfav <number from /favs>")
        return
    removed = await favorites_store.remove(user_id, int(query) - 1)
    if removed:
        await message.answer(success_card(f"Removed: {removed['title']}"), parse_mode="HTML")
    else:
        await reply_error(message, "Invalid favorite number.")


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    from bot.utils.formatters import history_card

    history = await history_tracker.get_chat_history(message.chat.id)
    await message.answer(history_card(history), parse_mode="HTML")


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    import time
    start = time.monotonic()
    msg = await message.answer("🏓 Pinging…")
    latency = (time.monotonic() - start) * 1000
    stats = await bot_stats.summary()
    await msg.edit_text(
        f"🏓 <b>Pong!</b> <code>{latency:.0f}ms</code>\n"
        f"⏱ Uptime: <code>{stats['uptime']}</code>\n"
        f"📊 Commands: <code>{stats['commands']}</code>  •  Streams: <code>{stats['streams']}</code>",
        parse_mode="HTML",
    )


@router.message(Command("download"))
async def cmd_download(message: Message) -> None:
    query = extract_query(message)
    if not query:
        current = await queue_manager.get_current(message.chat.id)
        if current:
            query = current.get("url") or current.get("title", "")
        else:
            await reply_error(message, "Usage: /download <song or URL>")
            return

    status = await message.answer("⬇️ <b>Downloading…</b>", parse_mode="HTML")
    track = await get_stream_url(query)
    if not track or not track.get("stream_url", "").startswith("http"):
        await status.edit_text(error_card("Could not download this track."), parse_mode="HTML")
        return

    tmp = tempfile.mkdtemp()
    out_path = os.path.join(tmp, f"{track['title'][:50]}.mp3")

    import asyncio
    import yt_dlp

    def _dl() -> str | None:
        opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": out_path.replace(".mp3", ".%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([track.get("url") or query])
            for f in os.listdir(tmp):
                if f.endswith(".mp3"):
                    return os.path.join(tmp, f)
        except Exception:
            return None
        return None

    path = await asyncio.get_event_loop().run_in_executor(None, _dl)
    if not path or not os.path.isfile(path):
        await status.edit_text(error_card("Download failed."), parse_mode="HTML")
        return

    await status.delete()
    await message.answer_audio(
        FSInputFile(path),
        title=track["title"],
        performer=track.get("artist", ""),
        caption=f"⬇️ {track['title']}",
    )
