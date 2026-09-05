"""YouTube / media extraction via yt-dlp."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import shutil
from typing import Any
from urllib.parse import urlparse

import yt_dlp

from bot.config import DATA_DIR, config

logger = logging.getLogger(__name__)

#: Message YouTube extraction failures are reported with, so handlers can tell
#: "this IP is blocked" apart from "there is genuinely no such song".
BLOCKED_HINT = (
    "YouTube is refusing requests from this server's IP. "
    "Set COOKIES_FILE (exported from a logged-in browser) or YTDLP_PROXY."
)

_BLOCK_MARKERS = (
    "failed to extract any player response",
    "sign in to confirm",
    "not a bot",
    "your ip is likely being blocked",
    "all player responses are invalid",
    "http error 429",
    "this content isn't available",
)


def _js_runtimes() -> dict[str, dict]:
    """Pick a JavaScript runtime for yt-dlp's YouTube challenge solver.

    This matters far more than it looks. Modern yt-dlp needs a JS runtime to
    solve YouTube's player challenges; without one it falls back to
    ``_DEFAULT_JSLESS_CLIENTS``, which is a *single* client (``visionos``).
    One client means one chance, and on a datacenter IP that chance usually
    fails with "Failed to extract any player response" and no retry.

    With a runtime registered it uses the full default client list, so a
    refusal from one client falls through to the next.

    yt-dlp only auto-registers Deno, so Node — which most hosts already have —
    has to be opted into explicitly.
    """
    configured = (config.js_runtime or "").strip().lower()
    if configured in {"none", "off", "disabled"}:
        return {}

    order = [configured] if configured else ["deno", "node", "bun", "quickjs"]
    for name in order:
        if name and shutil.which(name):
            return {name: {}}
    return {}


#: Extra YouTube player clients to try beyond yt-dlp's defaults.
#:
#: Each client is a separate shot at getting a player response, and they are
#: refused independently — a datacenter IP that `web` rejects may still be
#: served by `android_vr` or `tv`. The default list is only two clients, so on
#: a flagged IP there is very little to fall back on. Ordered cheapest-first:
#: the ones needing no JS challenge come before the web-ish ones.
_EXTRA_PLAYER_CLIENTS = ("android_vr", "tv", "mweb", "ios")


def _player_clients() -> list[str]:
    """Client list for the YouTube extractor, overridable per deployment."""
    configured = (config.youtube_clients or "").strip()
    if configured:
        return [c.strip() for c in configured.split(",") if c.strip()]
    return ["default", *_EXTRA_PLAYER_CLIENTS]


def materialize_cookies() -> str:
    """Return a path to a cookie jar, writing COOKIES_DATA out if needed.

    Cookies are the one reliable way past a datacenter-IP block, but PaaS
    hosts give you no persistent place to put a file and committing one to the
    repo leaks a live login. So accept the jar as an env var too — raw
    Netscape text or base64 of it — and materialize it at runtime.
    """
    if config.cookies_file and os.path.isfile(config.cookies_file):
        return config.cookies_file

    raw = config.cookies_data
    if not raw:
        return ""

    text = raw
    if "# Netscape" not in raw and "\t" not in raw:
        # Base64 keeps multi-line jars intact through dashboards that mangle
        # newlines, so accept that encoding transparently.
        try:
            text = base64.b64decode(raw, validate=True).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            text = raw

    text = text.replace("\\n", "\n").replace("\\t", "\t")
    if not text.endswith("\n"):
        text += "\n"

    try:
        target = DATA_DIR / "cookies.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Only rewrite when the contents changed, to avoid pointless churn.
        if not target.exists() or target.read_text(errors="replace") != text:
            target.write_text(text)
            target.chmod(0o600)
        return str(target)
    except OSError as exc:
        logger.error("Could not write COOKIES_DATA to disk: %s", exc)
        return ""


def _ydl_common() -> dict[str, Any]:
    """Options every extraction shares: auth, proxy, JS runtime, clients."""
    opts: dict[str, Any] = {}

    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes

    clients = _player_clients()
    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}

    # Cookies from a logged-in account are the single most effective way past
    # a datacenter-IP block. These were already read from the environment but
    # only ever applied to /song downloads, never to streaming or search.
    cookies = materialize_cookies()
    if cookies:
        opts["cookiefile"] = cookies
    if config.ytdlp_proxy:
        opts["proxy"] = config.ytdlp_proxy
    return opts


def looks_blocked(error_text: str) -> bool:
    """True when an extraction error looks like an IP block, not a bad query."""
    low = (error_text or "").lower()
    return any(marker in low for marker in _BLOCK_MARKERS)


YDL_OPTS_BASE: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 30,
    "retries": 3,
    "geo_bypass": True,
    # Keep transient network hiccups from surfacing as "no results".
    "extractor_retries": 3,
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


#: Last extraction failure, so handlers can explain *why* nothing came back
#: instead of always claiming "no results found".
_last_error: str = ""


def last_error() -> str:
    return _last_error


async def _run_ytdl(opts: dict[str, Any], query: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    global _last_error
    loop = asyncio.get_event_loop()
    merged = {**opts, **_ydl_common()}

    def _extract() -> dict[str, Any] | list[dict[str, Any]] | None:
        with yt_dlp.YoutubeDL(merged) as ydl:
            return ydl.extract_info(query, download=False)

    try:
        result = await loop.run_in_executor(None, _extract)
    except Exception as exc:
        _last_error = str(exc)
        if looks_blocked(_last_error):
            # Distinguish the systemic failure from a typo'd song title, and
            # do not bury it at DEBUG — this one needs operator action.
            logger.error(
                "yt-dlp blocked while extracting %r: %s | %s",
                query,
                exc,
                BLOCKED_HINT,
            )
        else:
            logger.error("yt-dlp extraction failed for %r: %s", query, exc)
        return None

    _last_error = ""
    return result


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


async def resolve_query(query: str) -> str:
    """Resolve Spotify and other URLs to searchable queries."""
    if "open.spotify.com/track/" in query or "spotify:track:" in query:
        info = await _run_ytdl({**YDL_OPTS_BASE, "skip_download": True}, query)
        if info and isinstance(info, dict):
            title = info.get("title", "")
            artist = info.get("artist") or info.get("uploader") or ""
            if title:
                return f"{artist} {title}".strip() if artist else title
    if "open.spotify.com/playlist/" in query:
        return query
    return query


async def get_track(query: str, requester: str = "", video: bool = False) -> dict[str, Any] | None:
    resolved = await resolve_query(query)
    opts = YDL_VIDEO if video else YDL_AUDIO
    search_query = resolved if is_url(resolved) else f"ytsearch1:{resolved}"
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
    resolved = await resolve_query(query)
    if live:
        opts = {**YDL_LIVE, "format": "best"}
    elif video:
        opts = YDL_VIDEO
    else:
        opts = YDL_AUDIO

    search_query = resolved if is_url(resolved) else f"ytsearch1:{resolved}"
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
