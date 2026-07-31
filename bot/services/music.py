"""YouTube / media extraction via yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import yt_dlp

logger = logging.getLogger(__name__)

YDL_OPTS_BASE: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 30,
    "retries": 3,
    "geo_bypass": True,
}

YDL_AUDIO: dict[str, Any] = {
    **YDL_OPTS_BASE,
    "format": "bestaudio/best",
    "extract_flat": False,
}

YDL_VIDEO: dict[str, Any] = {
    **YDL_OPTS_BASE,
    "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
    "merge_output_format": "mp4",
}

YDL_SEARCH: dict[str, Any] = {
    **YDL_OPTS_BASE,
    "extract_flat": True,
    "skip_download": True,
}

YDL_LIVE: dict[str, Any] = {
    **YDL_OPTS_BASE,
    "format": "best",
    "live_from_start": False,
}

URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|m\.youtube\.com|"
    r"soundcloud\.com|open\.spotify\.com|music\.youtube\.com|"
    r"twitch\.tv|vimeo\.com|dailymotion\.com|facebook\.com|"
    r"instagram\.com|tiktok\.com|twitter\.com|x\.com)[^\s]*",
    re.IGNORECASE,
)

M3U8_RE = re.compile(r"https?://[^\s]+\.m3u8[^\s]*", re.IGNORECASE)


def is_url(text: str) -> bool:
    return bool(URL_RE.search(text) or M3U8_RE.search(text) or text.startswith("http"))


def is_live_url(text: str) -> bool:
    return bool(M3U8_RE.search(text) or "live" in text.lower())


async def _run_ytdl(opts: dict[str, Any], query: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    loop = asyncio.get_event_loop()

    def _extract() -> dict[str, Any] | list[dict[str, Any]] | None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(query, download=False)

    try:
        return await loop.run_in_executor(None, _extract)
    except Exception as exc:
        logger.error("yt-dlp extraction failed for %r: %s", query, exc)
        return None


def _normalize_entry(entry: dict[str, Any], requester: str = "") -> dict[str, Any]:
    return {
        "id": entry.get("id", ""),
        "title": entry.get("title", "Unknown"),
        "artist": entry.get("uploader") or entry.get("channel") or entry.get("artist") or "",
        "duration": entry.get("duration"),
        "url": entry.get("webpage_url") or entry.get("url", ""),
        "stream_url": entry.get("url", ""),
        "thumbnail": entry.get("thumbnail", ""),
        "is_live": bool(entry.get("is_live")),
        "is_video": entry.get("vcodec", "none") != "none",
        "requester": requester,
        "source": entry.get("extractor_key", "unknown"),
    }


async def search_youtube(query: str, limit: int = 10) -> list[dict[str, Any]]:
    info = await _run_ytdl(YDL_SEARCH, f"ytsearch{limit}:{query}")
    if not info:
        return []
    entries = info.get("entries", []) if isinstance(info, dict) else []
    return [_normalize_entry(e) for e in entries if e]


async def get_track(query: str, requester: str = "", video: bool = False) -> dict[str, Any] | None:
    opts = YDL_VIDEO if video else YDL_AUDIO
    search_query = query if is_url(query) else f"ytsearch1:{query}"
    info = await _run_ytdl(opts, search_query)
    if not info:
        return None

    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            return None
        info = entries[0]

    track = _normalize_entry(info, requester)
    track["is_video"] = video or track["is_video"]
    return track


async def get_stream_url(query: str, video: bool = False, live: bool = False) -> dict[str, Any] | None:
    if live:
        opts = {**YDL_LIVE, "format": "best"}
    elif video:
        opts = YDL_VIDEO
    else:
        opts = YDL_AUDIO

    search_query = query if is_url(query) else f"ytsearch1:{query}"
    info = await _run_ytdl(opts, search_query)
    if not info:
        return None

    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            return None
        info = entries[0]

    fmt = info.get("url", "")
    if not fmt and info.get("formats"):
        formats = sorted(
            [f for f in info["formats"] if f.get("url")],
            key=lambda f: f.get("abr") or f.get("tbr") or 0,
            reverse=True,
        )
        if formats:
            fmt = formats[0]["url"]

    track = _normalize_entry(info)
    track["stream_url"] = fmt
    track["is_video"] = video or track["is_video"]
    track["is_live"] = live or track["is_live"]
    return track


async def get_suggestions(seed_query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fetch related/suggested tracks based on a seed query."""
    seed = await get_track(seed_query)
    if not seed:
        return await search_youtube(seed_query, limit)

    related = await search_youtube(f"{seed['title']} {seed.get('artist', '')} similar", limit + 2)
    seen = {seed["id"]}
    results = []
    for r in related:
        if r["id"] not in seen:
            seen.add(r["id"])
            results.append(r)
        if len(results) >= limit:
            break
    return results


async def get_playlist(url: str, requester: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Extract tracks from a YouTube/Spotify playlist URL."""
    opts = {
        **YDL_OPTS_BASE,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": False,
        "playlistend": limit,
    }
    info = await _run_ytdl(opts, url)
    if not info:
        return []

    entries = info.get("entries", []) if isinstance(info, dict) else []
    tracks = []
    for entry in entries:
        if not entry:
            continue
        track = _normalize_entry(entry, requester)
        if not track.get("url") and track.get("id"):
            track["url"] = f"https://youtube.com/watch?v={track['id']}"
        tracks.append(track)
    return tracks


MOOD_QUERIES: dict[str, str] = {
    "chill": "chill lofi beats to relax",
    "party": "party dance hits 2024",
    "workout": "workout gym motivation music",
    "sad": "sad emotional songs playlist",
    "happy": "happy upbeat feel good songs",
    "focus": "focus concentration study music",
    "sleep": "sleep relaxing ambient music",
    "romantic": "romantic love songs playlist",
    "gaming": "gaming epic music mix",
    "retro": "80s 90s retro hits playlist",
}


async def get_mood_tracks(mood: str, limit: int = 10) -> list[dict[str, Any]]:
    query = MOOD_QUERIES.get(mood.lower(), f"{mood} music playlist")
    return await search_youtube(query, limit)


async def resolve_telegram_file(file_path: str, title: str = "Uploaded File") -> dict[str, Any]:
    """Build a track dict from a local Telegram-downloaded file."""
    return {
        "id": file_path,
        "title": title,
        "artist": "Local File",
        "duration": None,
        "url": file_path,
        "stream_url": file_path,
        "thumbnail": "",
        "is_live": False,
        "is_video": file_path.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")),
        "requester": "",
        "source": "telegram",
    }
