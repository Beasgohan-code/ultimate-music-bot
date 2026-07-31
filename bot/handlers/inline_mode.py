"""Inline query mode — search songs from any chat."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from bot.services.music import search_youtube
from bot.utils.formatters import format_duration

router = Router(name="inline")


@router.inline_query()
async def inline_search(query: InlineQuery) -> None:
    q = (query.query or "").strip()
    if len(q) < 2:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id="help",
                    title="🎵 Ultimate Music Bot",
                    description="Type a song name to search…",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            "🎵 <b>Ultimate Music Bot</b>\n\n"
                            "Search inline by typing a song name, then tap a result.\n"
                            "Use /play in a group to stream in voice chat!"
                        ),
                        parse_mode="HTML",
                    ),
                )
            ],
            cache_time=10,
            is_personal=True,
        )
        return

    results_raw = await search_youtube(q, limit=10)
    results = []
    for i, r in enumerate(results_raw):
        dur = format_duration(r.get("duration"))
        results.append(
            InlineQueryResultArticle(
                id=f"{r.get('id', i)}",
                title=r.get("title", "Unknown")[:64],
                description=f"{r.get('artist', 'YouTube')} • {dur}",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        f"🎵 <b>{r.get('title', 'Unknown')}</b>\n"
                        f"👤 <i>{r.get('artist', '')}</i>\n"
                        f"⏱ <code>{dur}</code>\n\n"
                        f"Use: <code>/play {r.get('title', q)}</code>"
                    ),
                    parse_mode="HTML",
                ),
                thumbnail_url=r.get("thumbnail") or None,
            )
        )

    await query.answer(results, cache_time=30, is_personal=True)
