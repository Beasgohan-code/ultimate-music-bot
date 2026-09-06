"""Inline button callback handlers."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import player_more_kb, player_panel_kb, queue_pagination_kb
from bot.services.favorites import favorites_store
from bot.services.music import get_stream_url
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.cards import error_card, now_playing_card, queue_card
from bot.utils.rich import send_card
from bot.utils.helpers import get_cached_track

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

PER_PAGE = 8


async def _edit_card(query: CallbackQuery, card, reply_markup=None) -> None:
    """Edit a callback's message with a RichCard, ignoring 'not modified'."""
    try:
        await query.message.edit_text(
            card.to_html(), parse_mode="HTML", reply_markup=reply_markup
        )
    except Exception:
        pass


async def send_player_panel(message: Message) -> None:
    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    loop = await queue_manager.get_loop(chat_id)
    vol = await queue_manager.get_volume(chat_id)

    if current:
        card = now_playing_card(
            current,
            elapsed=stream_manager.elapsed(chat_id),
            queue_len=await queue_manager.size(chat_id),
            volume=vol,
            loop_mode=loop.value,
        )
    else:
        card = error_card(
            "No track playing.", "Use /play <song> or /song to get started."
        )

    await send_card(
        message,
        card,
        reply_markup=player_panel_kb(
            stream_manager.is_playing(chat_id),
            stream_manager.is_paused(chat_id),
        ),
    )


async def send_queue_view(message: Message, page: int = 0) -> None:
    chat_id = message.chat.id
    tracks = await queue_manager.get_queue(chat_id)
    current = await queue_manager.get_current(chat_id)
    total_pages = max(1, (len(tracks) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    await send_card(
        message,
        queue_card(current, tracks, page, PER_PAGE),
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
            current,
            elapsed=stream_manager.elapsed(chat_id),
            queue_len=await queue_manager.size(chat_id),
            volume=vol,
            loop_mode=loop.value,
        ).to_html()
    else:
        card = error_card("No track playing.", "Use /play <song> to start.").to_html()

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
    total_pages = max(1, (len(tracks) + PER_PAGE - 1) // PER_PAGE)
    await _edit_card(query, queue_card(current, tracks, 0, PER_PAGE),
                     queue_pagination_kb(0, total_pages))
    await query.answer()


@router.callback_query(F.data.startswith("queue:page:"))
async def cb_queue_page(query: CallbackQuery) -> None:
    page = int(query.data.split(":")[-1])
    chat_id = query.message.chat.id
    tracks = await queue_manager.get_queue(chat_id)
    current = await queue_manager.get_current(chat_id)
    total_pages = max(1, (len(tracks) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    await _edit_card(query, queue_card(current, tracks, page, PER_PAGE),
                     queue_pagination_kb(page, total_pages))
    await query.answer()


@router.callback_query(F.data == "ctrl:lyrics")
async def cb_lyrics(query: CallbackQuery) -> None:
    current = await queue_manager.get_current(query.message.chat.id)
    if not current:
        await query.answer("Nothing playing", show_alert=True)
        return
    from bot.services.lyrics import get_lyrics
    from bot.utils.cards import lyrics_card

    result = await get_lyrics(
        f"{current.get('artist', '')} - {current['title']}",
        artist=current.get("artist", ""),
        title=current["title"],
    )
    if result:
        art, tit, lyrics = result
        await send_card(query.message, lyrics_card(tit, art, lyrics))
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


@router.callback_query(F.data == "ctrl:noop")
async def cb_noop(query: CallbackQuery) -> None:
    """The progress bar is a label, not a control.

    Telegram shows a spinner until a callback is answered, so an unhandled
    tap looks like the bot froze. Answering with the timestamp turns a stray
    press into something mildly useful.
    """
    text = ""
    if query.message and query.message.reply_markup:
        rows = query.message.reply_markup.inline_keyboard
        if rows and len(rows[0]) == 1:
            text = rows[0][0].text
    await query.answer(text or "Playback progress")


@router.callback_query(F.data == "ctrl:more")
async def cb_more(query: CallbackQuery) -> None:
    """Swap the transport row for the secondary actions."""
    try:
        await query.message.edit_reply_markup(reply_markup=player_more_kb())
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data == "ctrl:back")
async def cb_back(query: CallbackQuery) -> None:
    """Return from the ⋯ More panel to the transport controls."""
    chat_id = query.message.chat.id
    try:
        await query.message.edit_reply_markup(
            reply_markup=player_panel_kb(
                stream_manager.is_playing(chat_id),
                stream_manager.is_paused(chat_id),
            )
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data == "ctrl:fav")
async def cb_favourite(query: CallbackQuery) -> None:
    """Save the current track to the tapping user's favourites."""
    current = await queue_manager.get_current(query.message.chat.id)
    if not current:
        await query.answer("Nothing playing", show_alert=True)
        return

    user_id = query.from_user.id if query.from_user else 0
    if not user_id:
        await query.answer("Could not identify you", show_alert=True)
        return

    added = await favorites_store.add(user_id, current)
    title = current.get("title", "this track")[:40]
    await query.answer(
        f"⭐ Saved {title}" if added else "Already in your favourites",
        show_alert=not added,
    )


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
        await _edit_card(query, error_card("Failed to load track.", "Try a different result."))
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
        await _edit_card(
            query,
            now_playing_card(
                track,
                elapsed=0,
                queue_len=await queue_manager.size(chat_id),
                volume=vol,
                loop_mode=loop.value,
            ),
            player_panel_kb(True),
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
        await _edit_card(query, error_card("No tracks for this mood.", "Try another vibe."))
        return
    cache_search_results(tracks)
    from bot.utils.cards import search_card
    from bot.keyboards.inline import search_results_kb

    await _edit_card(query, search_card(f"{mood} mood", tracks), search_results_kb(tracks))


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


@router.callback_query(F.data == "ctrl:video")
async def cb_video_mode(query: CallbackQuery) -> None:
    from bot.services.chat_settings import chat_settings
    from bot.utils.formatters import bq, bold

    chat_id = query.message.chat.id
    enabled = await chat_settings.toggle(chat_id, "default_video")
    await query.answer(f"🎬 Video mode: {'ON' if enabled else 'OFF'}")
    await query.message.answer(
        f"🎬 {bold('Video Mode')}\n\n"
        f"{bq('Default video mode is now ' + ('enabled' if enabled else 'disabled') + '. Use /vplay for video streams.')}",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ctrl:live")
async def cb_live_mode(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold, italic

    await query.message.answer(
        f"📡 {bold('Live Stream')}\n\n"
        f"{bq('Send a live URL to stream.')}\n\n"
        f"{italic('Usage: /vstream <m3u8 or YouTube Live URL>')}",
        parse_mode="HTML",
    )
    await query.answer("Use /vstream <url>")


@router.callback_query(F.data == "ctrl:more_suggest")
async def cb_more_suggest(query: CallbackQuery) -> None:
    from bot.services.music import get_suggestions
    from bot.keyboards.inline import suggestions_kb
    from bot.utils.formatters import suggestions_card
    from bot.utils.helpers import cache_suggestions

    current = await queue_manager.get_current(query.message.chat.id)
    seed = current["title"] if current else "popular music"
    suggestions = await get_suggestions(seed, limit=12)
    cache_suggestions(suggestions)
    await query.message.edit_text(
        suggestions_card(seed, suggestions),
        parse_mode="HTML",
        reply_markup=suggestions_kb(suggestions),
    )
    await query.answer("🔄 Refreshed")


@router.callback_query(F.data == "settings:volume")
async def cb_settings_volume(query: CallbackQuery) -> None:
    vol = await queue_manager.get_volume(query.message.chat.id)
    await query.message.answer(
        f"🔊 <b>Volume:</b> <code>{vol}%</code>\n\nUse /volume 1-200 or the panel buttons.",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data == "settings:loop")
async def cb_settings_loop(query: CallbackQuery) -> None:
    mode = await queue_manager.toggle_loop(query.message.chat.id)
    await query.answer(f"🔁 Loop: {mode.value}")
    await query.message.answer(f"🔁 Loop mode: <b>{mode.value.title()}</b>", parse_mode="HTML")


@router.callback_query(F.data == "settings:autoleave")
async def cb_settings_autoleave(query: CallbackQuery) -> None:
    from bot.config import config
    from bot.services.chat_settings import chat_settings

    enabled = await chat_settings.toggle(query.message.chat.id, "autoleave_enabled")
    await query.answer(f"Auto-leave: {'ON' if enabled else 'OFF'}")
    await query.message.answer(
        f"📡 Auto-leave: <b>{'enabled' if enabled else 'disabled'}</b> "
        f"(idle: {config.auto_leave_idle}s)",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings:video")
async def cb_settings_video(query: CallbackQuery) -> None:
    from bot.services.chat_settings import chat_settings

    enabled = await chat_settings.toggle(query.message.chat.id, "default_video")
    await query.answer(f"Default video: {'ON' if enabled else 'OFF'}")
    await query.message.answer(
        f"🎬 Default video: <b>{'enabled' if enabled else 'disabled'}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ui:close")
async def cb_close(query: CallbackQuery) -> None:
    """Dismiss a card. FallenMusic's ✖ close button.

    Deleting can fail for reasons the user cannot act on (message older than
    48h, or the bot lost delete rights), so fall back to stripping the keyboard
    — the card stops being interactive either way.
    """
    import contextlib

    try:
        await query.message.delete()
    except Exception:
        with contextlib.suppress(Exception):
            await query.message.edit_reply_markup(reply_markup=None)
    with contextlib.suppress(Exception):
        await query.answer()


@router.callback_query(F.data == "vote:skip")
async def cb_voteskip(query: CallbackQuery) -> None:
    """Tap to vote (or un-vote) on an open skip vote.

    The card is edited in place so the tally is always current, rather than
    posting a new message per vote and burying the queue.
    """
    import contextlib

    from bot.keyboards.inline import voteskip_kb
    from bot.services.queue import queue_manager
    from bot.services.stream import stream_manager
    from bot.services.voteskip import count_listeners, voteskip
    from bot.utils.cards import success_card, voteskip_card

    chat_id = query.message.chat.id
    user = query.from_user
    current = await queue_manager.get_current(chat_id)
    if not current:
        await query.answer("That track already finished.", show_alert=True)
        with contextlib.suppress(Exception):
            await query.message.delete()
        return

    # The requester never needs to vote — they can skip outright.
    if current.get("requester_id") and current["requester_id"] == user.id:
        await query.answer("It's your track — use /skip to skip it now.", show_alert=True)
        return

    listeners = await count_listeners(query.bot, chat_id)
    needed = voteskip.needed(listeners, await voteskip.ratio(chat_id))

    if voteskip.has_voted(chat_id, user.id, current):
        votes, _ = voteskip.remove_vote(chat_id, user.id, current)
        await query.answer("Vote withdrawn.")
    else:
        votes, _ = voteskip.add_vote(chat_id, user.id, current)
        await query.answer("Vote counted.")

    if votes >= needed:
        voteskip.reset(chat_id)
        title = current.get("title", "this track")
        with contextlib.suppress(Exception):
            await query.message.edit_text(
                success_card(
                    f"Vote passed — skipping {title}.", f"{votes} of {needed} listeners agreed."
                ).to_html(),
                parse_mode="HTML",
            )
        await stream_manager.skip(chat_id)
        return

    title = current.get("title", "this track")
    with contextlib.suppress(Exception):
        await query.message.edit_text(
            voteskip_card(votes, needed, title).to_html(),
            parse_mode="HTML",
            reply_markup=voteskip_kb(votes, needed),
        )
