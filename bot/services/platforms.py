"""Resolve music links from services yt-dlp cannot download directly.

Spotify, Apple Music and Deezer all stream DRM-protected audio, so nothing can
pull the actual file from them — yt-dlp ships no Spotify extractor at all. What
*is* available is their metadata, and that is enough: read the title and artist
off the link, then find the same recording on a source that can be streamed.

Every resolver here degrades instead of failing. Spotify prefers the Web API
when credentials exist and falls back to the public oEmbed endpoint when they
do not, so a Spotify link works out of the box and works *better* once
configured. Deezer and Apple Music need no credentials at all.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_UA = "Mozilla/5.0 (compatible; UltimateMusicBot/1.0)"

#: Playlists can be enormous; keep a lid on how much we expand at once.
MAX_PLAYLIST_TRACKS = 100

#: Metadata rarely changes, and a chat replaying the same album should not
#: re-hit the API each time.
_CACHE_TTL = 3600.0
_cache: dict[str, tuple[float, "Resolved"]] = {}


# ──────────────────────────────────────────────────────────────────────────
# Link patterns
# ──────────────────────────────────────────────────────────────────────────

_SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/|spotify:(track|album|playlist|artist):)"
    r"([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_DEEZER_RE = re.compile(
    r"(?:deezer\.com/(?:[a-z]{2}/)?(track|album|playlist)/|deezer\.page\.link/)(\d+)?",
    re.IGNORECASE,
)
_APPLE_RE = re.compile(
    r"music\.apple\.com/(?:([a-z]{2})/)?(album|playlist|song|artist)/[^/]*/(?:pl\.)?([A-Za-z0-9.\-]+)",
    re.IGNORECASE,
)

#: Services people paste that we deliberately do not pretend to support.
_UNSUPPORTED = {
    "tidal.com": "Tidal",
    "music.amazon.": "Amazon Music",
    "pandora.com": "Pandora",
    "napster.com": "Napster",
}


@dataclass(slots=True)
class Resolved:
    """Metadata for a link, plus the search queries that will find the audio."""

    platform: str
    kind: str  # track | album | playlist | artist
    title: str = ""
    subtitle: str = ""
    artwork: str = ""
    url: str = ""
    tracks: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    @property
    def is_single(self) -> bool:
        return self.kind == "track" or len(self.tracks) == 1

    def queries(self) -> list[str]:
        """Search strings, best-effort, one per track.

        A track with no title is unsearchable — matching on the artist alone
        would queue an arbitrary song by them, which is worse than skipping it.
        """
        out = []
        for t in self.tracks:
            title = (t.get("title") or "").strip()
            if not title:
                continue
            artist = (t.get("artist") or "").strip()
            out.append(f"{artist} - {title}" if artist else title)
        return out


def detect(url: str) -> str:
    """Return the platform name for a URL, or "" when it is not one of ours."""
    low = (url or "").lower()
    if _SPOTIFY_RE.search(low):
        return "spotify"
    if "deezer.com" in low or "deezer.page.link" in low:
        return "deezer"
    if "music.apple.com" in low:
        return "apple"
    return ""


def unsupported_service(url: str) -> str:
    """Name a DRM service we cannot help with, so the bot can say so plainly."""
    low = (url or "").lower()
    for needle, name in _UNSUPPORTED.items():
        if needle in low:
            return name
    return ""


# ──────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────────


async def _get_json(url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
    # Merge rather than override: callers pass an Authorization header and
    # would otherwise collide with the default User-Agent.
    merged = {"User-Agent": _UA, **(headers or {})}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url, headers=merged, **kwargs) as resp:
                if resp.status != 200:
                    logger.debug("%s -> HTTP %s", url, resp.status)
                    return None
                return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.debug("Request to %s failed: %s", url, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────
# Spotify
# ──────────────────────────────────────────────────────────────────────────

_token: tuple[str, float] | None = None
_token_lock = asyncio.Lock()


async def _spotify_token() -> str:
    """Client-credentials token, cached until shortly before it expires."""
    global _token
    if not config.spotify_enabled:
        return ""

    async with _token_lock:
        if _token and _token[1] > time.time():
            return _token[0]
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(
                    "https://accounts.spotify.com/api/token",
                    data={"grant_type": "client_credentials"},
                    auth=aiohttp.BasicAuth(
                        config.spotify_client_id, config.spotify_client_secret
                    ),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("Spotify auth failed: HTTP %s", resp.status)
                        return ""
                    payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logger.warning("Spotify auth error: %s", exc)
            return ""

        token = payload.get("access_token", "")
        if token:
            # Renew a minute early rather than racing the expiry.
            _token = (token, time.time() + int(payload.get("expires_in", 3600)) - 60)
        return token


def _spotify_track(item: dict[str, Any]) -> dict[str, Any]:
    artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
    images = (item.get("album") or {}).get("images") or []
    return {
        "title": item.get("name", ""),
        "artist": artists,
        "duration": int(item.get("duration_ms", 0) / 1000) or None,
        "artwork": images[0]["url"] if images else "",
        "isrc": (item.get("external_ids") or {}).get("isrc", ""),
        "platform": "spotify",
    }


async def _resolve_spotify(url: str) -> Resolved | None:
    match = _SPOTIFY_RE.search(url)
    if not match:
        return None
    kind = (match.group(1) or match.group(2) or "").lower()
    ident = match.group(3)

    token = await _spotify_token()
    if token:
        resolved = await _spotify_via_api(kind, ident, token)
        if resolved:
            return resolved

    # No credentials, or the API declined — oEmbed is public and needs none.
    return await _spotify_via_oembed(kind, ident, url)


async def _spotify_via_api(kind: str, ident: str, token: str) -> Resolved | None:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": _UA}
    base = "https://api.spotify.com/v1"

    if kind == "track":
        data = await _get_json(f"{base}/tracks/{ident}", headers=headers)
        if not data:
            return None
        track = _spotify_track(data)
        return Resolved(
            platform="spotify",
            kind="track",
            title=track["title"],
            subtitle=track["artist"],
            artwork=track["artwork"],
            url=f"https://open.spotify.com/track/{ident}",
            tracks=[track],
        )

    if kind == "album":
        album = await _get_json(f"{base}/albums/{ident}", headers=headers)
        if not album:
            return None
        images = album.get("images") or []
        art = images[0]["url"] if images else ""
        items = (album.get("tracks") or {}).get("items", [])
        tracks = []
        for item in items[:MAX_PLAYLIST_TRACKS]:
            entry = _spotify_track(item)
            entry["artwork"] = entry["artwork"] or art
            tracks.append(entry)
        return Resolved(
            platform="spotify",
            kind="album",
            title=album.get("name", ""),
            subtitle=", ".join(a.get("name", "") for a in album.get("artists", [])),
            artwork=art,
            url=f"https://open.spotify.com/album/{ident}",
            tracks=tracks,
            truncated=len(items) > MAX_PLAYLIST_TRACKS,
        )

    if kind == "playlist":
        meta = await _get_json(
            f"{base}/playlists/{ident}?fields=name,owner(display_name),images",
            headers=headers,
        )
        tracks: list[dict[str, Any]] = []
        next_url = f"{base}/playlists/{ident}/tracks?limit=100"
        total_seen = 0
        while next_url and len(tracks) < MAX_PLAYLIST_TRACKS:
            page = await _get_json(next_url, headers=headers)
            if not page:
                break
            for row in page.get("items", []):
                total_seen += 1
                item = row.get("track") or {}
                if item.get("name"):
                    tracks.append(_spotify_track(item))
                if len(tracks) >= MAX_PLAYLIST_TRACKS:
                    break
            next_url = page.get("next")
        if not tracks:
            return None
        images = (meta or {}).get("images") or []
        return Resolved(
            platform="spotify",
            kind="playlist",
            title=(meta or {}).get("name", "Spotify playlist"),
            subtitle=((meta or {}).get("owner") or {}).get("display_name", ""),
            artwork=images[0]["url"] if images else "",
            url=f"https://open.spotify.com/playlist/{ident}",
            tracks=tracks,
            truncated=bool(next_url),
        )

    if kind == "artist":
        data = await _get_json(
            f"{base}/artists/{ident}/top-tracks?market={config.spotify_market or 'US'}",
            headers=headers,
        )
        if not data:
            return None
        tracks = [_spotify_track(t) for t in data.get("tracks", [])]
        return Resolved(
            platform="spotify",
            kind="artist",
            title=tracks[0]["artist"] if tracks else "Artist",
            subtitle="Top tracks",
            artwork=tracks[0]["artwork"] if tracks else "",
            url=f"https://open.spotify.com/artist/{ident}",
            tracks=tracks,
        )
    return None


async def _spotify_via_oembed(kind: str, ident: str, url: str) -> Resolved | None:
    """Public metadata for a Spotify link, no credentials required.

    oEmbed gives a display title and artwork but no structured artist field and
    no track list, so albums and playlists collapse to a single searchable
    name. That is a real limitation, and the caller says so rather than
    silently returning one track for a 40-track album.
    """
    data = await _get_json(f"https://open.spotify.com/oembed?url={url}")
    if not data or not data.get("title"):
        return None

    title = str(data["title"]).strip()
    artist = ""
    # oEmbed titles are commonly "Song - Artist" or "Song by Artist".
    for sep in (" - ", " by ", " · "):
        if sep in title:
            head, _, tail = title.partition(sep)
            title, artist = head.strip(), tail.strip()
            break

    track = {
        "title": title,
        "artist": artist,
        "duration": None,
        "artwork": data.get("thumbnail_url", ""),
        "platform": "spotify",
    }
    return Resolved(
        platform="spotify",
        kind="track" if kind == "track" else kind,
        title=title,
        subtitle=artist,
        artwork=data.get("thumbnail_url", ""),
        url=url,
        tracks=[track],
        # Anything that is not a single track lost its track list here.
        truncated=kind != "track",
    )


# ──────────────────────────────────────────────────────────────────────────
# Deezer — fully public API, no credentials
# ──────────────────────────────────────────────────────────────────────────


def _deezer_track(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title_short") or item.get("title", ""),
        "artist": (item.get("artist") or {}).get("name", ""),
        "duration": item.get("duration") or None,
        "artwork": (item.get("album") or {}).get("cover_big", ""),
        "platform": "deezer",
    }


async def _resolve_deezer(url: str) -> Resolved | None:
    match = re.search(r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist)/(\d+)", url, re.I)
    if not match:
        return None
    kind, ident = match.group(1).lower(), match.group(2)

    if kind == "track":
        data = await _get_json(f"https://api.deezer.com/track/{ident}")
        if not data or data.get("error"):
            return None
        track = _deezer_track(data)
        return Resolved(
            platform="deezer",
            kind="track",
            title=track["title"],
            subtitle=track["artist"],
            artwork=track["artwork"],
            url=url,
            tracks=[track],
        )

    data = await _get_json(f"https://api.deezer.com/{kind}/{ident}")
    if not data or data.get("error"):
        return None
    items = (data.get("tracks") or {}).get("data", [])
    tracks = [_deezer_track(t) for t in items[:MAX_PLAYLIST_TRACKS]]
    if not tracks:
        return None
    return Resolved(
        platform="deezer",
        kind=kind,
        title=data.get("title", ""),
        subtitle=(data.get("artist") or {}).get("name", "")
        or (data.get("creator") or {}).get("name", ""),
        artwork=data.get("cover_big") or data.get("picture_big", ""),
        url=url,
        tracks=tracks,
        truncated=len(items) > MAX_PLAYLIST_TRACKS,
    )


# ──────────────────────────────────────────────────────────────────────────
# Apple Music — via the public iTunes lookup API
# ──────────────────────────────────────────────────────────────────────────


def _itunes_track(item: dict[str, Any]) -> dict[str, Any]:
    art = item.get("artworkUrl100", "")
    return {
        "title": item.get("trackName", ""),
        "artist": item.get("artistName", ""),
        "duration": int(item.get("trackTimeMillis", 0) / 1000) or None,
        # The 100px thumbnail URL resizes by substitution.
        "artwork": art.replace("100x100", "600x600") if art else "",
        "platform": "apple",
    }


async def _resolve_apple(url: str) -> Resolved | None:
    match = _APPLE_RE.search(url)
    if not match:
        return None
    country = (match.group(1) or "us").lower()
    kind = match.group(2).lower()

    # A song inside an album is identified by the ?i= query parameter.
    song_id = re.search(r"[?&]i=(\d+)", url)
    if song_id or kind == "song":
        ident = song_id.group(1) if song_id else match.group(3)
        data = await _get_json(
            f"https://itunes.apple.com/lookup?id={ident}&entity=song&country={country}"
        )
        results = (data or {}).get("results") or []
        if not results:
            return None
        track = _itunes_track(results[0])
        return Resolved(
            platform="apple",
            kind="track",
            title=track["title"],
            subtitle=track["artist"],
            artwork=track["artwork"],
            url=url,
            tracks=[track],
        )

    if kind == "album":
        ident = re.search(r"/(\d+)", url)
        if not ident:
            return None
        data = await _get_json(
            f"https://itunes.apple.com/lookup?id={ident.group(1)}"
            f"&entity=song&limit={MAX_PLAYLIST_TRACKS}&country={country}"
        )
        results = (data or {}).get("results") or []
        if len(results) < 2:
            return None
        header, songs = results[0], [r for r in results[1:] if r.get("trackName")]
        tracks = [_itunes_track(s) for s in songs]
        if not tracks:
            return None
        return Resolved(
            platform="apple",
            kind="album",
            title=header.get("collectionName", ""),
            subtitle=header.get("artistName", ""),
            artwork=tracks[0]["artwork"],
            url=url,
            tracks=tracks,
        )

    # Apple playlists are not exposed through the public lookup API.
    return None


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────

_RESOLVERS = {
    "spotify": _resolve_spotify,
    "deezer": _resolve_deezer,
    "apple": _resolve_apple,
}


async def resolve(url: str) -> Resolved | None:
    """Resolve a supported music link to playable metadata."""
    platform = detect(url)
    if not platform:
        return None

    cached = _cache.get(url)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        result = await _RESOLVERS[platform](url)
    except Exception as exc:  # a bad link must never take the handler down
        logger.warning("%s resolution failed for %s: %s", platform, url, exc)
        return None

    if result and result.tracks:
        _cache[url] = (time.time() + _CACHE_TTL, result)
        # Keep the cache from growing without bound on a busy bot.
        if len(_cache) > 512:
            for key in sorted(_cache, key=lambda k: _cache[k][0])[:128]:
                _cache.pop(key, None)
        return result
    return None


def clear_cache() -> None:
    _cache.clear()
