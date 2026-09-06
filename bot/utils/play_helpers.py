"""Shared playback helper — wires history, stats, limits, autoleave and cards."""

from __future__ import annotations

import logging

from aiogram.types import Message

from bot.config import config
from bot.keyboards.inline import player_panel_kb
from bot.services.autoleave import auto_leave
from bot.services.database import database
from bot.services.history import history_tracker
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.cards import error_card, now_playing_card, queued_card
from bot.utils.guards import is_admin_or_auth
from bot.services.callerrors import diagnose
from bot.utils.helpers import ensure_assistant_in_chat, is_group_chat
from bot.utils.rich import send_card

logger = logging.getLogger(__name__)


async def can_play(message: Message) -> bool:
    """Honour the per-chat 'only admins may play' setting."""
    if not is_group_chat(message) or not message.from_user:
        return True
    admins_only = bool(
        await database.get_chat_value(message.chat.id, "play_admins_only", config.admins_only)
    )
    if not admins_only:
        return True
    if await is_admin_or_auth(message.bot, message.chat.id, message.from_user.id):
        return True
    await send_card(
        message,
        error_card(
            "Only admins can start playback in this chat.",
            "An admin can change this with /settings → Music.",
        ),
    )
    return False


def _within_duration_limit(track: dict) -> tuple[bool, str]:
    """Reject overly long tracks (except live streams)."""
    if track.get("is_live"):
        return True, ""
    duration = track.get("duration")
    if not duration:
        return True, ""
    limit_min = config.video_limit_min if track.get("is_video") else config.duration_limit_min
    if limit_min <= 0:
        return True, ""
    if int(duration) > limit_min * 60:
        mins = int(duration) // 60
        return False, f"That track is {mins} minutes long — the limit here is {limit_min} minutes."
    return True, ""


async def play_track(
    message: Message,
    track: dict,
    *,
    force: bool = False,
    front: bool = False,
    queue_only: bool = False,
    edit_msg=None,
) -> bool:
    """Play or queue a track. Returns True on success."""
    chat_id = message.chat.id
    requester = message.from_user.full_name if message.from_user else "Unknown"
    user_id = message.from_user.id if message.from_user else 0
    track["requester"] = requester
    auto_leave.touch(chat_id)

    ok, reason = _within_duration_limit(track)
    if not ok:
        await send_card(message, error_card(reason, "Ask an admin to raise DURATION_LIMIT."), edit=edit_msg)
        return False

    if is_group_chat(message):
        err = await ensure_assistant_in_chat(message.bot, chat_id)
        if err:
            await send_card(message, error_card(err), edit=edit_msg)
            return False

    should_start = force or (not queue_only and not stream_manager.is_playing(chat_id))

    if should_start:
        try:
            await stream_manager.play(chat_id, track)
        except Exception as exc:
            logger.error("Playback failed in %s: %s", chat_id, exc, exc_info=True)
            bot_stats.errors_count += 1
            # "NoActiveGroupCall()" means nothing to a user, and each cause
            # has a completely different fix.
            found = diagnose(exc, config.assistant_username)
            await send_card(
                message,
                error_card(found.title, found.hint),
                edit=edit_msg,
            )
            return False

        await history_tracker.record(chat_id, track)
        await database.record_play(chat_id, user_id, track)
        bot_stats.streams_started += 1

        card = now_playing_card(
            track,
            elapsed=0,
            queue_len=await queue_manager.size(chat_id),
            volume=await queue_manager.get_volume(chat_id),
            loop_mode=(await queue_manager.get_loop(chat_id)).value,
        )
        # Duration drives the progress row; live streams have none and skip it.
        panel = player_panel_kb(True, elapsed=0, duration=track.get("duration") or 0)
        if not await _send_with_thumbnail(message, track, card, edit_msg, panel):
            await send_card(message, card, reply_markup=panel, edit=edit_msg)
        return True

    try:
        if front:
            await queue_manager.add_front(chat_id, track)
            position = 1
        else:
            position = await queue_manager.add(chat_id, track)
    except ValueError as exc:
        await send_card(message, error_card(str(exc), "Use /clear to make room."), edit=edit_msg)
        return False

    card = queued_card(track, position, await queue_manager.size(chat_id))
    await send_card(message, card, reply_markup=player_panel_kb(True), edit=edit_msg)
    return True


async def _send_with_thumbnail(message: Message, track: dict, card, edit_msg, panel=None) -> bool:
    """Try to send the now-playing card as an image. False -> caller falls back.

    Image cards are opt-out per chat because they cost a render and some
    groups would rather have a compact text line.
    """
    if not await database.get_chat_value(message.chat.id, "thumbnails", True):
        return False
    try:
        from bot.services.thumbnails import now_playing_image

        image = await now_playing_image(track, elapsed=0, bot_name=config.bot_name)
        if not image:
            return False

        from aiogram.types import FSInputFile

        caption = card.to_html()
        if len(caption) > 1024:  # photo captions are capped well below messages
            caption = caption[:1015].rsplit("\n", 1)[0] + "\n…"
        await message.answer_photo(
            FSInputFile(image),
            caption=caption,
            parse_mode="HTML",
            reply_markup=panel or player_panel_kb(True),
        )
        if edit_msg:
            try:
                await edit_msg.delete()
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.debug("Thumbnail card failed, using text: %s", exc)
        return False
