"""Premium OS dashboard and stats."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import os_dashboard_kb
from bot.services.history import history_tracker
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.formatters import os_dashboard_card, stats_card

router = Router(name="dashboard")


@router.message(Command("os"))
async def cmd_os(message: Message) -> None:
    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    queue_len = len(await queue_manager.get_queue(chat_id))
    loop = await queue_manager.get_loop(chat_id)
    vol = await queue_manager.get_volume(chat_id)
    stats = await bot_stats.summary()
    history = await history_tracker.get_chat_history(chat_id, limit=3)

    card = os_dashboard_card(
        current=current,
        queue_len=queue_len,
        is_playing=stream_manager.is_playing(chat_id),
        is_paused=stream_manager.is_paused(chat_id),
        loop_mode=loop.value,
        volume=vol,
        stats=stats,
        recent=history,
    )
    await message.answer(
        card,
        parse_mode="HTML",
        reply_markup=os_dashboard_kb(
            stream_manager.is_playing(chat_id),
            stream_manager.is_paused(chat_id),
        ),
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = await bot_stats.summary()
    global_history = await history_tracker.get_global_history(5)
    await message.answer(
        stats_card(stats, global_history),
        parse_mode="HTML",
    )
