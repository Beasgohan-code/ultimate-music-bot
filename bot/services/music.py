"""YouTube / media extraction via yt-dlp."""

from __future__ import annotations

import asyncio
import base64
import http.cookiejar
import binascii
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import time
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
    "YouTube is blocking this server's IP, and SoundCloud had no match "
    "either. The fix is cookies: export them from a browser signed in to "
    "YouTube and set COOKIES_DATA. A proxy (YTDLP_PROXY) also works."
)

_BLOCK_MARKERS = (
    "failed to extract any player response",
    "sign in to confirm",
    "not a bot",
    "your ip is likely being blocked",
    "all player responses are invalid",
    "http error 429",
    "this content isn't available",
    # A timeout on a host that normally answers in a second is throttling in
    # all but name — and must trigger the fallback to another backend.
    "extraction timed out",
)

_UNSUPPORTED_MARKERS = ("unsupported url", "no video formats found", "is not a valid url")


def looks_unsupported(error_text: str) -> bool:
    """True when the link simply is not media — a docs page, an article, etc."""
    low = (error_text or "").lower()
    return any(marker in low for marker in _UNSUPPORTED_MARKERS)


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


#: Cookies that actually carry a YouTube login. A jar without at least one of
#: these authenticates as nobody, however many lines it has.
_AUTH_COOKIES = {"SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}


def inspect_cookies(path: str) -> dict[str, Any]:
    """Report whether a cookie jar is actually usable.

    An expired jar is worse than none: yt-dlp drops the dead entries, sends
    the request unauthenticated, and the failure looks identical to having no
    cookies at all. Checking up front turns a mystery into a message.
    """
    info: dict[str, Any] = {
        "path": path,
        "exists": False,
        "total": 0,
        "live": 0,
        "expired": 0,
        "authenticated": False,
        "next_expiry": None,
        "problem": "",
    }
    if not path or not os.path.isfile(path):
        info["problem"] = "no cookie file"
        return info
    info["exists"] = True

    try:
        jar = http.cookiejar.MozillaCookieJar(path)
        # ignore_expires so we can *count* the dead ones rather than silently
        # dropping them the way a plain load() would.
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        info["problem"] = f"unreadable ({type(exc).__name__})"
        return info

    now = time.time()
    live_names: set[str] = set()
    expiries: list[float] = []
    for cookie in jar:
        info["total"] += 1
        if cookie.expires and cookie.expires <= now:
            info["expired"] += 1
            continue
        info["live"] += 1
        live_names.add(cookie.name)
        if cookie.expires:
            expiries.append(cookie.expires)

    info["authenticated"] = bool(live_names & _AUTH_COOKIES)
    if expiries:
        info["next_expiry"] = min(expiries)

    if not info["total"]:
        info["problem"] = "file has no cookies"
    elif not info["live"]:
        info["problem"] = "every cookie has expired"
    elif not info["authenticated"]:
        info["problem"] = "no login cookies (SID / LOGIN_INFO) — not signed in"
    return info


def cookie_pool() -> list[str]:
    """Every usable cookie jar, newest-checked first.

    Rotating across several accounts spreads the load so one jar does not get
    rate-limited on its own — the idea comes from DAXXMUSIC, which picks a
    random file from a cookies/ directory. Unusable jars are filtered out
    here rather than picked blindly: an expired file is not a fallback, it is
    a silent failure.
    """
    paths: list[str] = []

    single = materialize_cookies()
    if single:
        paths.append(single)

    directory = (config.cookies_dir or "").strip()
    if directory and os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".txt"):
                full = os.path.join(directory, name)
                if full not in paths:
                    paths.append(full)

    # Keep anything with at least one live cookie. A jar without a login is
    # weaker but still useful (consent/region cookies), so it is warned about
    # at startup rather than discarded here. Fully expired jars are useless.
    return [p for p in paths if inspect_cookies(p)["live"]]


def pick_cookie_file() -> str:
    """Choose a cookie jar for one request, rotating when several work."""
    usable = cookie_pool()
    if not usable:
        return ""
    if len(usable) == 1:
        return usable[0]
    return random.choice(usable)


def cookie_status() -> str:
    """One-line summary for the startup banner.

    Says plainly when a jar exists but cannot work. An expired file behaves
    exactly like no file at all, and that ambiguity costs hours to debug.
    """
    candidates: list[str] = []
    single = materialize_cookies()
    if single:
        candidates.append(single)
    directory = (config.cookies_dir or "").strip()
    if directory and os.path.isdir(directory):
        candidates += [
            os.path.join(directory, n)
            for n in sorted(os.listdir(directory))
            if n.endswith(".txt")
        ]

    if not candidates:
        return "none"

    reports = [(p, inspect_cookies(p)) for p in candidates]
    usable = [(p, i) for p, i in reports if not i["problem"]]
    if not usable:
        # Report the first real reason rather than a generic failure.
        reason = next((i["problem"] for _, i in reports if i["problem"]), "unusable")
        return f"PRESENT BUT UNUSABLE — {reason}"

    soonest = min(
        (i["next_expiry"] for _, i in usable if i["next_expiry"]), default=None
    )
    when = ""
    if soonest:
        when = f", first expires in {int((soonest - time.time()) // 86400)}d"
    rejected = len(reports) - len(usable)
    extra = f", {rejected} rejected" if rejected else ""
    if len(usable) > 1:
        return f"loaded ({len(usable)} jars rotating{when}{extra})"
    return f"loaded ({usable[0][1]['live']} cookies, signed in{when}{extra})"


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
    target = _impersonate_target()
    if target is not None:
        opts["impersonate"] = target

    cookies = pick_cookie_file()
    if cookies:
        opts["cookiefile"] = cookies
    if config.ytdlp_proxy:
        opts["proxy"] = config.ytdlp_proxy
    return opts


def looks_blocked(error_text: str) -> bool:
    """True when an extraction error looks like an IP block, not a bad query."""
    low = (error_text or "").lower()
    return any(marker in low for marker in _BLOCK_MARKERS)


#: Browser to impersonate at the TLS layer, when curl_cffi is installed.
#:
#: yt-dlp's default TLS/HTTP2 fingerprint is unmistakably Python, and that is
#: one of the signals used to decide a datacenter IP is a bot. Presenting
#: Firefox's fingerprint costs nothing and occasionally gets through where the
#: default does not. It is not a substitute for cookies.
_IMPERSONATE_PREFERENCES = ("firefox", "chrome", "safari")


@lru_cache(maxsize=1)
def _impersonate_target() -> Any:
    """Pick an available browser target, or None when curl_cffi is missing."""
    configured = (config.impersonate or "").strip().lower()
    if configured in {"off", "none", "disabled"}:
        return None

    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.networking.impersonate import ImpersonateTarget
    except Exception:
        return None

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            available = [t for t, _ in ydl._get_available_impersonate_targets()]
    except Exception as exc:
        logger.debug("Could not enumerate impersonate targets: %s", exc)
        return None

    if not available:
        logger.info(
            "Browser impersonation unavailable — install curl_cffi to let "
            "requests look like a real browser."
        )
        return None

    if configured:
        for target in available:
            if configured in str(target).lower():
                return target
        logger.warning(
            "IMPERSONATE=%s is not available; falling back to autodetect", configured
        )

    for wanted in _IMPERSONATE_PREFERENCES:
        for target in available:
            if str(target).lower().startswith(wanted):
                return target
    return available[0]


def impersonate_status() -> str:
    """One-line summary for the startup banner."""
    target = _impersonate_target()
    if target is None:
        return "off (install curl_cffi)"
    return str(target)


YDL_OPTS_BASE: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    # A blocked IP fails fast and identically on every retry, so long
    # timeouts and deep retry stacks only multiply the wait. Five clients x
    # 3 retries x 30s was up to ten minutes of dead air before the user saw
    # an error — and then the SoundCloud fallback started from scratch.
    "socket_timeout": 12,
    "retries": 1,
    "geo_bypass": True,
    "extractor_retries": 1,
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

    # A hard ceiling on top of yt-dlp's own timeouts. Its retry stack is
    # per-client, so a blocked IP can still burn a minute-plus walking through
    # clients that all fail the same way.
    #
    # yt-dlp is synchronous, so the worker cannot be interrupted — abandoning
    # it would leak a thread from the shared pool and eventually starve it.
    # A dedicated single-use executor is used instead: cancelling detaches it
    # and the thread dies on its own once yt-dlp's own socket timeout fires.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ytdl")
    future = loop.run_in_executor(executor, _extract)
    try:
        result = await asyncio.wait_for(asyncio.shield(future), config.extract_timeout)
    except asyncio.TimeoutError:
        _last_error = (
            f"Extraction timed out after {config.extract_timeout}s. "
            "The media host is not responding — this usually means the "
            "server's IP is being throttled or blocked."
        )
        logger.error("yt-dlp timed out after %ss for %r", config.extract_timeout, query)
        return None
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
    finally:
        # Never block on the worker: on timeout it is still inside yt-dlp and
        # will exit by itself once its own socket timeout fires.
        executor.shutdown(wait=False)

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
        # Display names are not identity: two users can share one, and
        # anyone can rename themselves to match. Permission checks use this.
        "requester_id": 0,
        "source": entry.get("extractor_key", "unknown"),
    }


async def search_youtube(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search for tracks, falling back to another backend if YouTube blocks."""
    info = await _search_with_fallback(query, YDL_SEARCH, count=limit)
    if not info:
        return []
    entries = info.get("entries", []) if isinstance(info, dict) else []
    return [_normalize_entry(e) for e in entries if e]


async def resolve_query(query: str) -> str:
    """Turn a streaming-service link into something searchable.

    Spotify, Apple Music and Deezer serve DRM-protected audio — yt-dlp has no
    Spotify extractor at all, so feeding it a Spotify URL simply fails. Read
    the metadata off the link instead and hand back "Artist - Title", which
    the normal YouTube search path can find.
    """
    from bot.services import platforms

    if platforms.detect(query):
        resolved = await platforms.resolve(query)
        if resolved:
            queries = resolved.queries()
            if queries:
                return queries[0]
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


#: Search backends tried in order, as (prefix, human name).
#:
#: YouTube has by far the best catalogue, so it stays first. But when a host's
#: IP is blocked, *every* YouTube query fails identically — and a music bot
#: that can never play anything is worse than one with a smaller library. The
#: fallbacks live on different infrastructure, so a YouTube block does not
#: affect them.
SEARCH_BACKENDS: tuple[tuple[str, str], ...] = (
    ("ytsearch", "YouTube"),
    ("scsearch", "SoundCloud"),
)


def _backends() -> tuple[tuple[str, str], ...]:
    configured = (config.search_backends or "").strip()
    if not configured:
        return SEARCH_BACKENDS
    names = {n.strip().lower() for n in configured.split(",") if n.strip()}
    picked = tuple(b for b in SEARCH_BACKENDS if b[1].lower() in names or b[0] in names)
    return picked or SEARCH_BACKENDS


async def _search_with_fallback(
    term: str, opts: dict[str, Any], *, count: int = 1
) -> dict[str, Any] | None:
    """Run a search against each backend until one answers.

    Only advances on a *blocked* failure. A genuine "no such song" should not
    send the query to another service — that would return an unrelated track
    rather than an honest empty result.
    """
    backends = _backends()
    for index, (prefix, label) in enumerate(backends):
        info = await _run_ytdl(opts, f"{prefix}{count}:{term}")
        if info:
            entries = info.get("entries") if isinstance(info, dict) else None
            if entries is None or [e for e in entries if e]:
                if index:
                    logger.info("Found %r on %s after YouTube failed", term, label)
                return info

        if not looks_blocked(_last_error):
            return None
        if index + 1 < len(backends):
            logger.warning(
                "%s blocked for %r — falling back to %s",
                label,
                term,
                backends[index + 1][1],
            )
    return None


async def get_stream_url(query: str, video: bool = False, live: bool = False) -> dict[str, Any] | None:
    resolved = await resolve_query(query)
    if live:
        opts = {**YDL_LIVE, "format": "best"}
    elif video:
        opts = YDL_VIDEO
    else:
        opts = YDL_AUDIO

    if is_url(resolved):
        info = await _run_ytdl(opts, resolved)
    else:
        # Video requests are YouTube-only in practice; the audio fallbacks
        # have no video catalogue worth searching.
        info = (
            await _run_ytdl(opts, f"ytsearch1:{resolved}")
            if video
            else await _search_with_fallback(resolved, opts)
        )
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
        "requester_id": 0,
        "source": "telegram",
    }
