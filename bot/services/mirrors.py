"""Play YouTube without cookies, by asking someone else's server.

A datacenter IP gets refused by YouTube. Cookies fix that, but they expire,
they leak, and asking a user to export a browser profile to hear a song is a
bad trade. Invidious and Piped are public front-ends that run the extraction
on *their* infrastructure and hand back plain metadata and stream URLs over
ordinary JSON. Their IPs are not ours, so a block on us does not apply.

The catch, and the reason this module is mostly defensive plumbing: public
instances are volunteer-run. They go down, rate-limit, return half-filled
JSON, or quietly serve stale data. So:

*   **Never trust one instance.** Every call races across several and takes
    the first usable answer.
*   **Remember what is broken.** An instance that fails is benched for a
    while rather than retried on every request, because a dead host costs a
    full timeout each time it is asked.
*   **Validate before returning.** A response missing a playable URL is a
    failure even when the HTTP status was 200 — treating it as success is how
    you get a card that says "Now Playing" over silence.

This is a fallback, not a replacement: yt-dlp direct is still tried first
because it is faster and higher quality when the IP is not blocked.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Iterable

import aiohttp

logger = logging.getLogger(__name__)

#: Public Invidious instances. Kept short deliberately — a long list mostly
#: adds dead hosts, and the health tracker below prunes what fails anyway.
INVIDIOUS = (
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://invidious.jing.rocks",
)

#: Piped speaks a different JSON shape, so it is a genuinely independent
#: second opinion rather than another Invidious clone.
PIPED = (
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.private.coffee",
)

#: One instance gets this long to answer before we give up on it. Short: we
#: race several, so a slow host should lose rather than hold up the request.
TIMEOUT = 6.0

#: How many instances to try at once. Two is enough to hide one dead host
#: without hammering volunteer infrastructure for every single play.
FANOUT = 2

#: Bench a failing instance for this long.
COOLDOWN = 300.0

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"

#: instance -> epoch when it may be tried again.
_benched: dict[str, float] = {}


def _healthy(instances: Iterable[str]) -> list[str]:
    """Instances not currently benched, in random order.

    Randomised so we do not send every deployment's traffic to whichever
    host happens to be first in the tuple.
    """
    now = time.monotonic()
    alive = [i for i in instances if _benched.get(i, 0.0) <= now]
    if not alive:  # everything is benched — clear and try again rather than fail
        _benched.clear()
        alive = list(instances)
    random.shuffle(alive)
    return alive


def _bench(instance: str, reason: str) -> None:
    _benched[instance] = time.monotonic() + COOLDOWN
    logger.debug("Benching %s for %.0fs: %s", instance, COOLDOWN, reason)


def reset() -> None:
    """Forget all bench state. For tests."""
    _benched.clear()


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=TIMEOUT), headers={"User-Agent": _UA}
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.json(content_type=None)


async def _race(paths: list[tuple[str, str]]) -> tuple[str, Any] | None:
    """Fire several instance requests, return the first usable answer.

    Losers are cancelled as soon as a winner lands, so a slow instance costs
    nothing beyond the socket it already opened.
    """
    if not paths:
        return None

    async with aiohttp.ClientSession() as session:

        async def one(instance: str, url: str) -> tuple[str, Any]:
            try:
                return instance, await _get_json(session, url)
            except Exception as exc:
                _bench(instance, type(exc).__name__)
                raise

        tasks = [asyncio.create_task(one(i, u)) for i, u in paths]
        try:
            for coro in asyncio.as_completed(tasks):
                try:
                    instance, data = await coro
                except Exception:
                    continue
                if data:
                    return instance, data
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return None


# ── shape normalisation ────────────────────────────────────────────────────
def _pick_audio(streams: list[dict[str, Any]]) -> str:
    """Best audio-only URL from an Invidious adaptiveFormats list."""
    best, best_rate = "", -1
    for stream in streams or []:
        if not isinstance(stream, dict):
            continue
        mime = str(stream.get("type") or stream.get("mimeType") or "")
        if not mime.startswith("audio"):
            continue
        url = stream.get("url")
        if not url:
            continue
        try:
            rate = int(stream.get("bitrate") or 0)
        except (TypeError, ValueError):
            rate = 0
        if rate > best_rate:
            best, best_rate = url, rate
    return best


def _from_invidious(data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    url = _pick_audio(data.get("adaptiveFormats") or [])
    if not url:
        # formatStreams carry muxed audio+video; usable, just larger.
        for stream in data.get("formatStreams") or []:
            if isinstance(stream, dict) and stream.get("url"):
                url = stream["url"]
                break
    if not url:
        return None
    video_id = data.get("videoId") or ""
    return {
        "title": data.get("title") or "Unknown",
        "artist": data.get("author") or "",
        "duration": int(data.get("lengthSeconds") or 0) or None,
        "url": f"https://youtube.com/watch?v={video_id}" if video_id else "",
        "id": video_id,
        "stream_url": url,
        "thumbnail": _invidious_thumb(data),
        "source": "youtube",
        "via": "invidious",
    }


def _invidious_thumb(data: dict[str, Any]) -> str:
    thumbs = data.get("videoThumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        first = thumbs[0]
        if isinstance(first, dict):
            return first.get("url") or ""
    return ""


def _from_piped(data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    best, best_rate = "", -1
    for stream in data.get("audioStreams") or []:
        if not isinstance(stream, dict) or not stream.get("url"):
            continue
        try:
            rate = int(stream.get("bitrate") or 0)
        except (TypeError, ValueError):
            rate = 0
        if rate > best_rate:
            best, best_rate = stream["url"], rate
    if not best:
        return None
    return {
        "title": data.get("title") or "Unknown",
        "artist": data.get("uploader") or "",
        "duration": int(data.get("duration") or 0) or None,
        "url": "",
        "id": "",
        "stream_url": best,
        "thumbnail": data.get("thumbnailUrl") or "",
        "source": "youtube",
        "via": "piped",
    }


# ── public API ─────────────────────────────────────────────────────────────
async def fetch_stream(video_id: str) -> dict[str, Any] | None:
    """Resolve a YouTube id to a playable track dict, or None.

    Tries Invidious first (richer metadata), then Piped. Both are asked
    without any credential, which is the entire point.
    """
    if not video_id:
        return None

    invidious = [
        (base, f"{base}/api/v1/videos/{video_id}")
        for base in _healthy(INVIDIOUS)[:FANOUT]
    ]
    won = await _race(invidious)
    if won:
        track = _from_invidious(won[1])
        if track:
            logger.info("Resolved %s via %s", video_id, won[0])
            return track
        _bench(won[0], "no playable stream in response")

    piped = [
        (base, f"{base}/streams/{video_id}") for base in _healthy(PIPED)[:FANOUT]
    ]
    won = await _race(piped)
    if won:
        track = _from_piped(won[1])
        if track:
            track["id"] = video_id
            track["url"] = f"https://youtube.com/watch?v={video_id}"
            logger.info("Resolved %s via %s", video_id, won[0])
            return track
        _bench(won[0], "no audio streams in response")

    return None


async def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search YouTube through a mirror. Returns metadata, not stream URLs.

    Deliberately separate from fetch_stream: search results from Invidious do
    not carry stream URLs, and pretending otherwise would produce entries
    that fail only once someone presses play.
    """
    if not query:
        return []

    from urllib.parse import quote

    encoded = quote(query)
    paths = [
        (base, f"{base}/api/v1/search?q={encoded}&type=video")
        for base in _healthy(INVIDIOUS)[:FANOUT]
    ]
    won = await _race(paths)
    if not won:
        return []

    out: list[dict[str, Any]] = []
    for item in won[1] if isinstance(won[1], list) else []:
        if not isinstance(item, dict) or item.get("type") != "video":
            continue
        video_id = item.get("videoId")
        if not video_id:
            continue
        out.append(
            {
                "title": item.get("title") or "Unknown",
                "artist": item.get("author") or "",
                "duration": int(item.get("lengthSeconds") or 0) or None,
                "url": f"https://youtube.com/watch?v={video_id}",
                "id": video_id,
                "thumbnail": _invidious_thumb(item),
                "source": "youtube",
                "via": "invidious",
            }
        )
        if len(out) >= limit:
            break
    return out


def status() -> str:
    """One-line health summary for /sysinfo and the startup report."""
    total = len(INVIDIOUS) + len(PIPED)
    now = time.monotonic()
    down = sum(1 for until in _benched.values() if until > now)
    if not down:
        return f"{total} mirrors ready"
    return f"{total - down}/{total} mirrors ready ({down} benched)"
