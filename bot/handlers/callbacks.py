"""Inline button callback handlers."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import player_panel_kb, queue_pagination_kb
from bot.services.music import get_stream_url
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.formatters import error_card, now_playing_card, queue_card
from bot.utils.helpers import get_cached_track

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

PER_PAGE = 8


async def send_player_panel(message: Message) -> None:
    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    loop = await queue_manager.get_loop(chat_id)
    vol = await queue_manager.get_volume(chat_id)

    if current:
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
    else:
        from bot.utils.formatters import bq, bold

        card = f"🎛 {bold('Control Panel')}\n\n{bq('No track playing. Use /play or /song to start.')}"

    await message.answer(
        card,
        parse_mode="HTML",
        reply_markup=player_panel_kb(
            stream_manager.is_playing(chat_id),
            stream_manager.is_paused(chat_id),
        ),
    )


async def send_queue_view(message: Message, page: int = 0) -> None:
    chat_id = message.chat.id
    tracks = await queue_manager.get_queue(chat_id)
    current = await queue_manager.get_current(chat_id)
    all_tracks = ([current] if current else []) + tracks
    total_pages = max(1, (len(all_tracks) + PER_PAGE - 1) // PER_PAGE)

    text = queue_card(all_tracks, page, PER_PAGE)
    if current:
        text = f"▶️ <b>Now:</b> {current['title']}\n\n{text}"

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=queue_pagination_kb(page, total_pages),
    )


@router.callback_query(F.data == "ctrl:panel")
async def cb_panel(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    current = await queue_manager.get_current(chat_id)
    loop = await queue_manager.get_loop(chat_id)
    vol = await queue_manager.get_volume(chat_id)

    if current:
        card = now_playing_card(
            current["title"],
            current.get("artist", ""),
            current.get("duration"),
            current.get("requester", ""),
            video=current.get("is_video", False),
            loop_mode=loop.value,
            volume=vol,
        )
    else:
        from bot.utils.formatters import bq, bold

        card = f"🎛 {bold('Control Panel')}\n\n{bq('No track playing.')}"

    await query.message.edit_text(
        card,
        parse_mode="HTML",
        reply_markup=player_panel_kb(
            stream_manager.is_playing(chat_id),
            stream_manager.is_paused(chat_id),
        ),
    )
    await query.answer()


@router.callback_query(F.data == "ctrl:pause")
async def cb_pause(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    if stream_manager.is_playing(chat_id):
        await stream_manager.pause(chat_id)
        await query.answer("⏸ Paused")
        await cb_panel(query)
    else:
        await query.answer("Nothing playing", show_alert=True)


@router.callback_query(F.data == "ctrl:resume")
async def cb_resume(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    if stream_manager.is_paused(chat_id):
        await stream_manager.resume(chat_id)
        await query.answer("▶️ Resumed")
        await cb_panel(query)
    else:
        await query.answer("Not paused", show_alert=True)


@router.callback_query(F.data == "ctrl:skip")
async def cb_skip(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    next_track = await stream_manager.skip(chat_id)
    if next_track:
        await query.answer(f"⏭ {next_track['title'][:30]}")
    else:
        await query.answer("Queue finished")
    await cb_panel(query)


@router.callback_query(F.data == "ctrl:stop")
async def cb_stop(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    await stream_manager.stop(chat_id)
    await queue_manager.clear(chat_id)
    await query.answer("⏹ Stopped")
    await cb_panel(query)


@router.callback_query(F.data == "ctrl:loop")
async def cb_loop(query: CallbackQuery) -> None:
    mode = await queue_manager.toggle_loop(query.message.chat.id)
    await query.answer(f"🔁 Loop: {mode.value}")
    await cb_panel(query)


@router.callback_query(F.data == "ctrl:shuffle")
async def cb_shuffle(query: CallbackQuery) -> None:
    await queue_manager.shuffle(query.message.chat.id)
    await query.answer("🔀 Shuffled!")


@router.callback_query(F.data == "ctrl:clear")
async def cb_clear(query: CallbackQuery) -> None:
    count = await queue_manager.clear(query.message.chat.id)
    await query.answer(f"🗑 Cleared {count} tracks")


@router.callback_query(F.data == "ctrl:vol_up")
async def cb_vol_up(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    vol = await queue_manager.get_volume(chat_id)
    new_vol = await stream_manager.change_volume(chat_id, min(200, vol + 10))
    await query.answer(f"🔊 {new_vol}%")
    await cb_panel(query)


@router.callback_query(F.data == "ctrl:vol_down")
async def cb_vol_down(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    vol = await queue_manager.get_volume(chat_id)
    new_vol = await stream_manager.change_volume(chat_id, max(1, vol - 10))
    await query.answer(f"🔊 {new_vol}%")
    await cb_panel(query)


@router.callback_query(F.data == "ctrl:queue")
async def cb_queue(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    tracks = await queue_manager.get_queue(chat_id)
    current = await queue_manager.get_current(chat_id)
    all_tracks = ([current] if current else []) + tracks
    text = queue_card(all_tracks)
    await query.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=queue_pagination_kb(0, max(1, len(all_tracks))),
    )
    await query.answer()


@router.callback_query(F.data.startswith("queue:page:"))
async def cb_queue_page(query: CallbackQuery) -> None:
    page = int(query.data.split(":")[-1])
    chat_id = query.message.chat.id
    tracks = await queue_manager.get_queue(chat_id)
    current = await queue_manager.get_current(chat_id)
    all_tracks = ([current] if current else []) + tracks
    total_pages = max(1, (len(all_tracks) + PER_PAGE - 1) // PER_PAGE)
    text = queue_card(all_tracks, page, PER_PAGE)
    await query.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=queue_pagination_kb(page, total_pages),
    )
    await query.answer()


@router.callback_query(F.data == "ctrl:lyrics")
async def cb_lyrics(query: CallbackQuery) -> None:
    current = await queue_manager.get_current(query.message.chat.id)
    if not current:
        await query.answer("Nothing playing", show_alert=True)
        return
    from bot.services.lyrics import get_lyrics
    from bot.utils.formatters import lyrics_card

    result = await get_lyrics(
        f"{current.get('artist', '')} - {current['title']}",
        artist=current.get("artist", ""),
        title=current["title"],
    )
    if result:
        art, tit, lyrics = result
        await query.message.answer(lyrics_card(tit, art, lyrics), parse_mode="HTML")
        await query.answer()
    else:
        await query.answer("Lyrics not found", show_alert=True)


@router.callback_query(F.data == "ctrl:suggest")
async def cb_suggest(query: CallbackQuery) -> None:
    current = await queue_manager.get_current(query.message.chat.id)
    if not current:
        await query.answer("Play something first", show_alert=True)
        return
    from bot.services.music import get_suggestions
    from bot.keyboards.inline import suggestions_kb
    from bot.utils.formatters import suggestions_card
    from bot.utils.helpers import cache_suggestions

    suggestions = await get_suggestions(current["title"])
    cache_suggestions(suggestions)
    await query.message.answer(
        suggestions_card(current["title"], suggestions),
        parse_mode="HTML",
        reply_markup=suggestions_kb(suggestions),
    )
    await query.answer()


@router.callback_query(F.data == "ctrl:cancel")
async def cb_cancel(query: CallbackQuery) -> None:
    await query.message.delete()
    await query.answer("Cancelled")


@router.callback_query(F.data.startswith("play:"))
async def cb_play_track(query: CallbackQuery) -> None:
    track_id = query.data.split(":", 1)[1]
    cached = get_cached_track(track_id)
    chat_id = query.message.chat.id

    await query.answer("⏳ Loading…")
    if cached and cached.get("url"):
        track = await get_stream_url(cached["url"])
    else:
        track = await get_stream_url(f"https://youtube.com/watch?v={track_id}")

    if not track:
        await query.message.edit_text(error_card("Failed to load track."), parse_mode="HTML")
        return

    track["requester"] = query.from_user.full_name if query.from_user else "Unknown"

    if stream_manager.is_playing(chat_id):
        pos = await queue_manager.add(chat_id, track)
        await query.message.edit_text(
            f"✅ Queued at <b>#{pos}</b>: {track['title']}",
            parse_mode="HTML",
            reply_markup=player_panel_kb(True),
        )
    else:
        await stream_manager.play(chat_id, track)
        loop = await queue_manager.get_loop(chat_id)
        vol = await queue_manager.get_volume(chat_id)
        await query.message.edit_text(
            now_playing_card(
                track["title"],
                track.get("artist", ""),
                track.get("duration"),
                track["requester"],
                loop_mode=loop.value,
                volume=vol,
            ),
            parse_mode="HTML",
            reply_markup=player_panel_kb(True),
        )


@router.callback_query(F.data.startswith("suggest:"))
async def cb_suggest_play(query: CallbackQuery) -> None:
    track_id = query.data.split(":", 1)[1]
    cached = get_cached_track(track_id)
    chat_id = query.message.chat.id

    if not cached:
        await query.answer("Track expired, search again", show_alert=True)
        return

    await query.answer("⏳ Loading…")
    track = await get_stream_url(cached.get("url") or f"https://youtube.com/watch?v={track_id}")
    if not track:
        await query.answer("Failed to load", show_alert=True)
        return

    track["requester"] = query.from_user.full_name if query.from_user else "Unknown"

    if stream_manager.is_playing(chat_id):
        await queue_manager.add(chat_id, track)
        await query.answer(f"✅ Queued: {track['title'][:30]}")
    else:
        await stream_manager.play(chat_id, track)
        await query.answer(f"▶️ Playing: {track['title'][:30]}")


@router.callback_query(F.data == "ctrl:replay")
async def cb_replay(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    current = await queue_manager.get_current(chat_id)
    if not current:
        await query.answer("Nothing to replay", show_alert=True)
        return
    await stream_manager.play(chat_id, current)
    await query.answer("⏮ Replaying")
    await cb_panel(query)


@router.callback_query(F.data.startswith("radio:"))
async def cb_radio(query: CallbackQuery) -> None:
    from bot.services.radio import get_station
    from bot.services.history import history_tracker
    from bot.services.stats import bot_stats

    key = query.data.split(":", 1)[1]
    station = get_station(key)
    if not station:
        await query.answer("Station not found", show_alert=True)
        return

    await query.answer(f"📻 Tuning to {station['name']}…")
    chat_id = query.message.chat.id
    track = await get_stream_url(station["url"])
    if not track:
        track = {
            "title": station["name"],
            "artist": station["genre"],
            "url": station["url"],
            "stream_url": station["url"],
            "is_live": True,
        }
    else:
        track["title"] = station["name"]
        track["is_live"] = True
    track["requester"] = query.from_user.full_name if query.from_user else "Unknown"
    await stream_manager.play(chat_id, track)
    await history_tracker.record(chat_id, track)
    bot_stats.streams_started += 1
    await cb_panel(query)


@router.callback_query(F.data.startswith("mood:"))
async def cb_mood(query: CallbackQuery) -> None:
    from bot.services.music import get_mood_tracks
    from bot.utils.helpers import cache_search_results

    mood = query.data.split(":", 1)[1]
    await query.answer(f"🎭 Loading {mood}…")
    tracks = await get_mood_tracks(mood, limit=8)
    if not tracks:
        await query.message.edit_text(error_card("No tracks for this mood."), parse_mode="HTML")
        return
    cache_search_results(tracks)
    from bot.utils.formatters import search_results_card
    from bot.keyboards.inline import search_results_kb

    await query.message.edit_text(
        search_results_card(f"{mood} mood", tracks),
        parse_mode="HTML",
        reply_markup=search_results_kb(tracks),
    )


@router.callback_query(F.data.startswith("favplay:"))
async def cb_favplay(query: CallbackQuery) -> None:
    from bot.services.favorites import favorites_store

    idx = int(query.data.split(":", 1)[1])
    user_id = query.from_user.id if query.from_user else 0
    favs = await favorites_store.list(user_id)
    if idx >= len(favs):
        await query.answer("Favorite not found", show_alert=True)
        return

    fav = favs[idx]
    await query.answer("⏳ Loading…")
    track = await get_stream_url(fav.get("url", ""))
    if not track:
        await query.answer("Failed to load", show_alert=True)
        return

    chat_id = query.message.chat.id
    track["requester"] = query.from_user.full_name if query.from_user else "Unknown"
    await stream_manager.play(chat_id, track)
    await cb_panel(query)
