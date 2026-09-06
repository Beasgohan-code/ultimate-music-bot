"""Lyrics and suggestions handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.keyboards.inline import suggestions_kb
from bot.services.lyrics import get_lyrics
from bot.services.music import get_suggestions, get_track
from bot.services.queue import queue_manager
from bot.utils.formatters import error_card, lyrics_card, suggestions_card
from bot.utils.helpers import cache_suggestions, extract_query, reply_error

router = Router(name="extras")


@router.message(Command("lyrics"))
async def cmd_lyrics(message: Message) -> None:
    query = extract_query(message)
    if not query:
        current = await queue_manager.get_current(message.chat.id)
        if current:
            query = f"{current.get('artist', '')} - {current['title']}"
        else:
            await reply_error(message, "Usage: /lyrics <artist - song> or play something first.")
            return

    status = await message.answer("📝 <b>Fetching lyrics…</b>", parse_mode="HTML")

    artist, title = "", query
    if " - " in query:
        artist, title = query.split(" - ", 1)

    result = await get_lyrics(query, artist=artist.strip(), title=title.strip())
    if not result:
        await status.edit_text(
            error_card(f"Could not find lyrics for: {query}"),
            parse_mode="HTML",
        )
        return

    art, tit, lyrics = result
    await status.edit_text(lyrics_card(tit, art, lyrics), parse_mode="HTML")


@router.message(Command("suggest"))
async def cmd_suggest(message: Message) -> None:
    query = extract_query(message)
    if not query:
        current = await queue_manager.get_current(message.chat.id)
        if current:
            query = current["title"]
        else:
            await reply_error(message, "Usage: /suggest <song or mood>")
            return

    status = await message.answer("💡 <b>Finding suggestions…</b>", parse_mode="HTML")
    suggestions = await get_suggestions(query)
    if not suggestions:
        await status.edit_text(error_card("No suggestions found.", "Play a few tracks first so I can learn what fits."), parse_mode="HTML")
        return

    cache_suggestions(suggestions)
    await status.edit_text(
        suggestions_card(query, suggestions),
        parse_mode="HTML",
        reply_markup=suggestions_kb(suggestions),
    )
