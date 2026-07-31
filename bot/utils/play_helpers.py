"""Shared playback helper — wires history, stats, autoleave."""

from __future__ import annotations

from aiogram.types import Message

from bot.keyboards.inline import player_panel_kb
from bot.services.autoleave import auto_leave
from bot.services.history import history_tracker
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.formatters import now_playing_card, success_card
from bot.utils.helpers import ensure_assistant_in_chat, is_group_chat, reply_error


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
    track["requester"] = requester
    auto_leave.touch(chat_id)

    if is_group_chat(message):
        err = await ensure_assistant_in_chat(message.bot, chat_id)
        if err:
            await reply_error(message, err)
            return False

    send = edit_msg.edit_text if edit_msg else message.answer

    if force or (not queue_only and not stream_manager.is_playing(chat_id)):
        try:
            await stream_manager.play(chat_id, track)
            await history_tracker.record(chat_id, track)
            bot_stats.streams_started += 1
            loop = await queue_manager.get_loop(chat_id)
            vol = await queue_manager.get_volume(chat_id)
            card = now_playing_card(
                track["title"],
                track.get("artist", ""),
                track.get("duration"),
                requester,
                video=track.get("is_video", False),
                is_live=track.get("is_live", False),
                loop_mode=loop.value,
                volume=vol,
            )
            await send(
                card,
                parse_mode="HTML",
                reply_markup=player_panel_kb(True),
            )
            return True
        except Exception as exc:
            await reply_error(message, f"Playback failed: {exc}")
            return False

    try:
        if front:
            await queue_manager.add_front(chat_id, track)
            text = success_card(f"Added to front of queue: {track['title']}")
        else:
            pos = await queue_manager.add(chat_id, track)
            text = success_card(f"Queued at #{pos}: {track['title']}")
        await send(text, parse_mode="HTML", reply_markup=player_panel_kb(True))
        return True
    except ValueError as exc:
        await reply_error(message, str(exc))
        return False
