"""Playback command handlers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import player_panel_kb, search_results_kb
from bot.config import config
from bot.services import assistant, platforms
from bot.services.autoleave import auto_leave
from bot.services.music import (
    BLOCKED_HINT,
    get_stream_url,
    get_suggestions,
    is_live_url,
    is_url,
    last_error as music_last_error,
    looks_unsupported,
    looks_blocked,
    search_youtube,
)
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager
from bot.utils.helpers import extract_query, is_group_chat, reply_error
from bot.utils.cards import (
    action_card,
    error_card,
    import_card,
    meter,
    now_playing_card,
    queued_card,
    search_card,
    success_card,
    voteskip_card,
)
from bot.services.autoplay import autoplay
from bot.services.cleanup import clean_command, schedule_cleanup
from bot.utils.play_helpers import can_play, play_track
from bot.utils.rich import RichCard, b, c, i, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="play")


def _brand(track: dict, resolved, index: int) -> None:
    """Stamp the originating platform and its artwork onto a matched track.

    The audio comes from YouTube or SoundCloud, but the user pasted a Spotify
    link — so the card should say Spotify and show the album cover, not a
    video still. Title and artist also come from the platform, which has
    cleaner metadata than a YouTube video title full of "(Official Video)".
    """
    rows = getattr(resolved, "tracks", None) or []
    row = rows[index] if index < len(rows) else {}
    if row.get("artwork"):
        track["thumbnail"] = row["artwork"]
    elif getattr(resolved, "artwork", ""):
        track["thumbnail"] = resolved.artwork
    if row.get("title"):
        track["title"] = row["title"]
    if row.get("artist"):
        track["artist"] = row["artist"]
    track["source"] = resolved.platform
    track["origin_url"] = getattr(resolved, "url", "") or ""


async def _import_platform_link(
    message: Message, query: str, *, video: bool, status, queue_only: bool = False
) -> bool:
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
    if not queries:
        await send_card(
            message,
            error_card(
                "That link had no playable tracks.",
                "An empty playlist, or every track was unavailable.",
            ),
            edit=status,
        )
        return True

    if resolved.is_single and len(queries) == 1:
        # Play it here rather than falling through to a plain search. The
        # search would find the right audio but label it YouTube and attach a
        # random video still — losing the album art and the Spotify badge the
        # user pasted a Spotify link to get.
        track = await get_stream_url(queries[0], video=video)
        if not track:
            err = music_last_error()
            await send_card(
                message,
                error_card(
                    f"Found “{resolved.title}” but couldn't get the audio.",
                    BLOCKED_HINT
                    if looks_blocked(err)
                    else "No streamable source had this track.",
                ),
                edit=status,
            )
            return True
        _brand(track, resolved, 0)
        await play_track(message, track, edit_msg=status, queue_only=queue_only)
        return True

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

    _brand(first, resolved, 0)

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
        _brand(track, resolved, idx)
        track["requester"] = message.from_user.full_name if message.from_user else ""
        track["requester_id"] = message.from_user.id if message.from_user else 0
        try:
            await queue_manager.add(message.chat.id, track)
            queued += 1
        except ValueError:
            pass  # queue full — the card reports the shortfall

    await asyncio.gather(*(_add(i, q) for i, q in enumerate(queries[1:], start=1)))

    await send_card(message, import_card(resolved, added=1 + queued, queued=queued))
    return True


async def _report_slow_search(status) -> None:
    """Keep the status message honest while a slow extraction runs.

    Cancelled as soon as the result arrives, so a fast search never sees it.
    """
    # Timings track what the extractor is actually doing, so the message is
    # never a guess: a retry begins a few seconds in, and the SoundCloud /
    # Niconico fallbacks only start after YouTube has exhausted its attempts.
    steps = (
        (6, "🔎 <b>Searching…</b>"),
        (14, "🔁 <b>Retrying</b> — the first attempt didn't get through."),
        (26, "🎧 <b>Trying SoundCloud…</b>"),
        (40, "📻 <b>Trying other sources…</b>"),
    )
    try:
        previous = 0
        for delay, text in steps:
            await asyncio.sleep(delay - previous)
            previous = delay
            with contextlib.suppress(Exception):
                await status.edit_text(text, parse_mode="HTML")
    except asyncio.CancelledError:
        pass


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
    await clean_command(message)
    status = await message.answer("⏳ <b>Loading media…</b>", parse_mode="HTML")
    # A frozen "Loading media…" reads as a hang. Say what is happening once
    # the wait stops looking instant, so a slow search is visibly progress.
    progress = asyncio.create_task(_report_slow_search(status))
    try:
        await _play_body(
            message, query, status, video=video, live=live, queue_only=queue_only
        )
    finally:
        progress.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress


async def _play_body(
    message: Message,
    query: str,
    status,
    *,
    video: bool = False,
    live: bool = False,
    queue_only: bool = False,
) -> None:
    if not live and await _import_platform_link(
        message, query, video=video, status=status, queue_only=queue_only
    ):
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
        # Nothing played, so this status message is now pure clutter.
        schedule_cleanup(status)
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
        await send_card(
            message,
            RichCard()
            .heading([plain("🎧 "), b("What should I play?")], size=1)
            .table(
                ["Example", "What it does"],
                [
                    [c("/play never gonna give you up"), "Search and stream the top match"],
                    [c("/play <youtube / soundcloud url>"), "Stream that exact track"],
                    [c("/play <playlist url>"), "Queue the whole playlist"],
                    [c("/vplay <query>"), "Same, but with video"],
                ],
            )
            .para([i("You can also reply to an audio file with /play.")]),
        )
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
        await send_card(message, error_card("Downloads are disabled on this instance.", "The operator can enable them with ENABLE_DOWNLOADS=1."))
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
        await send_card(message, error_card("Something went wrong sending that file.", "It may be larger than Telegram's 50 MB bot limit."), edit=status)
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


@router.message(Command("vplay", "video", "playvideo"))
async def cmd_vplay(message: Message) -> None:
    """Play with video.

    Video needs a running voice chat *with video enabled* and far more
    bandwidth than audio, so failures here are common and confusing. The usage
    card says what actually works instead of a bare "Usage:" line.
    """
    query = extract_query(message)
    if not query:
        await send_card(
            message,
            RichCard()
            .heading([plain("🎬 "), b("Video Play")], size=1)
            .para([plain("Stream a video into the group's voice chat.")])
            .table(
                ["Example", "What it does"],
                [
                    [c("/vplay lofi hip hop"), "Search and stream the top match"],
                    [c("/vplay <youtube url>"), "Stream that exact video"],
                    [c("/vstream <m3u8 url>"), "Stream a live feed"],
                ],
            )
            .para([i("The voice chat must already be started, and video uses "
                     "noticeably more bandwidth than audio.")]),
        )
        return
    await _resolve_and_play(message, query, video=True)


@router.message(Command("vstream", "livestream"))
async def cmd_vstream(message: Message) -> None:
    query = extract_query(message)
    if not query:
        await send_card(
            message,
            RichCard()
            .heading([plain("📡 "), b("Live Stream")], size=1)
            .para([plain("Stream a live source into the voice chat.")])
            .table(
                ["Example", "Source"],
                [
                    [c("/vstream <youtube live url>"), "YouTube live"],
                    [c("/vstream <m3u8 url>"), "HLS / IPTV feed"],
                ],
            )
            .para([i("A live stream plays until the source stops or someone runs /stop.")]),
        )
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
    await send_card(
        message,
        action_card("paused", _actor(message)),
        reply_markup=player_panel_kb(True, True),
    )


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not stream_manager.is_paused(message.chat.id):
        await reply_error(message, "Nothing to resume.")
        return
    await stream_manager.resume(message.chat.id)
    await send_card(
        message,
        action_card("resumed", _actor(message)),
        reply_markup=player_panel_kb(True),
    )


@router.message(Command("skip"))
async def cmd_skip(message: Message) -> None:
    chat_id = message.chat.id
    if not stream_manager.is_playing(chat_id):
        await reply_error(message, "Nothing is playing.")
        return

    if not await _may_skip_now(message):
        return
    await _do_skip(message)


def _actor(message: Message) -> str:
    """Display name of whoever ran the command, for attributed cards."""
    return message.from_user.full_name if message.from_user else ""


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
    # Compare ids, never display names: a name match is trivially forged.
    if current and current.get("requester_id") and current["requester_id"] == user.id:
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
        await send_card(message, error_card("You have already voted to skip.", "Waiting for other listeners to agree."))
        return False

    from bot.keyboards.inline import voteskip_kb

    await send_card(
        message,
        voteskip_card(votes, needed, current.get("title", "this track") if current else "this track"),
        reply_markup=voteskip_kb(votes, needed),
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
        await send_card(
            message,
            action_card(
                "skipped",
                _actor(message),
                note="Nothing left in the queue — leaving the voice chat.",
            ),
        )


@router.message(Command("stop", "end"))
async def cmd_stop(message: Message) -> None:
    # grouptools registers /stop <name> to delete a filter, but play.router is
    # included first, so a bare pass-through there could never be reached.
    # Defer explicitly when an argument is present.
    if len((message.text or "").split(maxsplit=1)) > 1 and not message.text.lower().startswith("/end"):
        from bot.handlers.grouptools import cmd_stop_filter

        await cmd_stop_filter(message, message.bot)
        return

    chat_id = message.chat.id
    dropped = await queue_manager.size(chat_id)
    await stream_manager.stop(chat_id)
    await queue_manager.clear(chat_id)

    from bot.services.voteskip import voteskip

    # A vote opened against the old track must not survive into the next one.
    voteskip.reset(chat_id)
    note = (
        f"Queue cleared — {dropped} track(s) discarded."
        if dropped
        else "The queue was already empty."
    )
    await send_card(message, action_card("ended", _actor(message), note=note))


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
    depth = len(await queue_manager.get_queue(message.chat.id))
    await send_card(
        message,
        action_card(
            "shuffled",
            _actor(message),
            note=f"{depth} track(s) reordered." if depth else "",
        ),
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    count = await queue_manager.clear(message.chat.id)
    await send_card(
        message,
        action_card("cleared", _actor(message), detail=f"{count} track(s)"),
    )


@router.message(Command("volume"))
async def cmd_volume(message: Message) -> None:
    query = extract_query(message)
    chat_id = message.chat.id
    if not query:
        vol = await queue_manager.get_volume(chat_id)
        card = (
            RichCard()
            .heading([plain("🔊 "), b("Volume")], size=1)
            .quote([[c(f"{vol}%"), plain("   "), plain(meter(vol, 200))]])
            .footer("/volume 1-200 to change it")
        )
        await send_card(message, card)
        return
    try:
        vol = int(query)
    except ValueError:
        await reply_error(message, "Volume must be a number between 1 and 200.")
        return
    vol = await stream_manager.change_volume(chat_id, vol)
    await send_card(
        message, action_card("volume", _actor(message), detail=f"{vol}%")
    )


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

    joined = await assistant.ensure_present(bot, message.chat.id)
    if not joined.ok:
        await send_card(message, error_card(joined.title, joined.hint))
        return

    status = await message.reply("⏳ <b>Processing file…</b>", parse_mode="HTML")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, fname)
    await bot.download(file, destination=path)

    from bot.services.music import resolve_telegram_file

    title = getattr(file, "title", None) or getattr(file, "file_name", "Uploaded Media")
    track = await resolve_telegram_file(path, title)
    track["requester"] = message.from_user.full_name if message.from_user else "Unknown"
    track["requester_id"] = message.from_user.id if message.from_user else 0
    track["is_video"] = mime.startswith("video/") or fname.lower().endswith((".mp4", ".mkv", ".webm"))

    chat_id = message.chat.id
    if stream_manager.is_playing(chat_id):
        pos = await queue_manager.try_add(chat_id, track)
        if pos is None:
            await send_card(
                message,
                error_card(queue_manager.full_message, "Use /clear to make room."),
                edit=status,
            )
            return
        await send_card(
            message,
            queued_card(track, pos, await queue_manager.size(chat_id)),
            edit=status,
        )
    else:
        await play_track(message, track, force=True, edit_msg=status)


@router.message(F.video_chat_ended)
async def on_video_chat_ended(message: Message) -> None:
    """Tear down when the voice chat is closed from Telegram's own UI.

    Without this the bot keeps a queue and a stream for a call that no longer
    exists, so the next /play or /skip fails in a confusing way. Silent by
    design — ending the call is already visible in the chat, and an extra
    "stopped" card is exactly the noise clean mode exists to prevent.
    """
    chat_id = message.chat.id
    try:
        await queue_manager.clear(chat_id)
    except Exception as exc:
        logger.debug("Queue clear after video chat ended failed: %s", exc)
    try:
        await stream_manager.stop(chat_id)
    except Exception as exc:
        logger.debug("Stream stop after video chat ended failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Stream lifecycle announcements
#
# PyTgCalls advances the queue on its own when a track ends, but nothing told
# the group about it — the music simply changed with no message. FallenMusic's
# watcher.py posted a "started streaming" card on every auto-advance; these
# callbacks do the same, plus report tracks that were skipped as unplayable.
# ─────────────────────────────────────────────────────────────────────────────


def register_stream_notifications(bot: Bot) -> None:
    """Attach queue-advance announcements. Called once at startup."""
    from bot.services.database import database


    async def announce_next(chat_id: int, track: dict) -> None:
        if not await database.get_chat_value(chat_id, "announce_tracks", True):
            return
        try:
            card = now_playing_card(
                track,
                elapsed=0,
                queue_len=await queue_manager.size(chat_id),
                volume=await queue_manager.get_volume(chat_id),
                loop_mode=(await queue_manager.get_loop(chat_id)).value,
            )
            await bot.send_message(
                chat_id,
                card.to_html(),
                parse_mode="HTML",
                reply_markup=player_panel_kb(True),
            )
        except Exception as exc:
            # A chat that removed the bot must not break the stream loop.
            logger.debug("Could not announce next track in %s: %s", chat_id, exc)

    async def announce_skipped(chat_id: int, titles: list[str]) -> None:
        try:
            listed = ", ".join(t[:40] for t in titles[:3])
            if len(titles) > 3:
                listed += f" and {len(titles) - 3} more"
            await bot.send_message(
                chat_id,
                error_card(
                    f"Skipped {len(titles)} unplayable track(s).",
                    f"{listed} — the source may be private, deleted or region-locked.",
                ).to_html(),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.debug("Could not report skipped tracks in %s: %s", chat_id, exc)

    async def announce_empty(chat_id: int) -> None:
        # Autoplay gets first refusal: if it can find a related track the
        # queue is not really empty, and leaving would be wrong.
        if await _try_autoplay(chat_id):
            return
        if not await database.get_chat_value(chat_id, "announce_tracks", True):
            return
        try:
            await bot.send_message(
                chat_id,
                action_card(
                    "ended", note="Queue finished — leaving the voice chat."
                ).to_html(),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.debug("Could not announce queue end in %s: %s", chat_id, exc)

    async def _try_autoplay(chat_id: int) -> bool:
        """Extend the session with a related track. True if playback continues."""
        if not await autoplay.is_enabled(chat_id):
            return False
        if autoplay.exhausted(chat_id):
            logger.info("Autoplay is exhausted for %s — letting the queue end", chat_id)
            return False

        seed = await queue_manager.get_current(chat_id)
        track = await autoplay.pick(chat_id, seed, fetch=get_suggestions)
        if not track:
            return False

        # Resolve a playable stream: suggestions carry metadata, not a URL
        # that PyTgCalls can feed to ffmpeg.
        playable = await get_stream_url(track.get("url") or track.get("title", ""))
        if not playable:
            autoplay.note_failure(chat_id)
            return False
        playable["requester"] = "Autoplay"
        playable["requester_id"] = 0
        playable["_autoplay"] = True

        try:
            await stream_manager.play(chat_id, playable)
        except Exception as exc:
            logger.warning("Autoplay could not start %r in %s: %s", track.get("title"), chat_id, exc)
            autoplay.note_failure(chat_id)
            return False

        if await database.get_chat_value(chat_id, "announce_tracks", True):
            try:
                card = now_playing_card(playable, queue_len=0)
                card.footer("▶️ Autoplay — /autoplay off to stop")
                await bot.send_message(
                    chat_id, card.to_html(), parse_mode="HTML",
                    reply_markup=player_panel_kb(True),
                )
            except Exception as exc:
                logger.debug("Could not announce autoplay in %s: %s", chat_id, exc)
        return True

    stream_manager.on_track_end(announce_next)
    stream_manager.on_autoskip(announce_skipped)
    stream_manager.on_queue_empty(announce_empty)
