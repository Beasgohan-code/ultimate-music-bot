"""Lyrics fetching service."""

from __future__ import annotations

import logging
import re

import aiohttp

logger = logging.getLogger(__name__)

GENIUS_SEARCH = "https://api.genius.com/search"
LYRICS_OVH = "https://api.lyrics.ovh/v1"


async def fetch_lyrics_ovh(artist: str, title: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{LYRICS_OVH}/{artist}/{title}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    lyrics = data.get("lyrics", "").strip()
                    return lyrics if lyrics else None
    except Exception as exc:
        logger.debug("lyrics.ovh failed: %s", exc)
    return None


async def fetch_lyrics_lrclib(artist: str, title: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            params = {"artist_name": artist, "track_name": title}
            async with session.get(
                "https://lrclib.net/api/search",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    if results:
                        return (results[0].get("plainLyrics") or results[0].get("syncedLyrics") or "").strip() or None
    except Exception as exc:
        logger.debug("lrclib failed: %s", exc)
    return None


def _parse_query(query: str) -> tuple[str, str]:
    """Split 'Artist - Title' or use query as title."""
    for sep in (" - ", " – ", " — ", " by "):
        if sep in query:
            parts = query.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return "", query.strip()


def _clean_lyrics(text: str) -> str:
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def get_lyrics(query: str, artist: str = "", title: str = "") -> tuple[str, str, str] | None:
    """
    Returns (artist, title, lyrics) or None.
    """
    if not artist or not title:
        parsed_artist, parsed_title = _parse_query(query)
        artist = artist or parsed_artist or "Unknown"
        title = title or parsed_title

    lyrics = await fetch_lyrics_lrclib(artist, title)
    if not lyrics:
        lyrics = await fetch_lyrics_ovh(artist, title)

    if lyrics:
        return artist, title, _clean_lyrics(lyrics)
    return None
