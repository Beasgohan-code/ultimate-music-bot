"""Survive a restart with the queue intact.

Queues live in memory, so every restart silently destroyed them. That matters
more here than it sounds: the deployment runs on Render's free plan, which
spins the service down after roughly fifteen minutes of inactivity and brings
it back on the next request. A group that queued twenty tracks, went quiet for
a coffee, and came back found an empty bot and no explanation.

Scheduler jobs already persisted; queues did not. This closes that gap.

What is saved is deliberately small: the pending queue, the current track, the
loop mode and the volume, per chat. What is *not* saved is the stream itself —
a PyTgCalls session cannot be resurrected, the assistant has to rejoin, and
pretending otherwise would produce a bot that claims to be playing while
silent. On restore the tracks come back and the chat is told to send /play to
pick up where it left off.

Two failure modes shaped the design:

*   **A snapshot must never block a shutdown.** Saving happens under a
    timeout, and a failure is logged rather than raised — losing a queue is
    bad, hanging the process on the way down is worse.
*   **A stale snapshot is worse than none.** Restoring a two-day-old queue
    into a group that has moved on is noise, so snapshots carry a timestamp
    and expire.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bot.services.database import database

logger = logging.getLogger(__name__)

COLLECTION = "queue_state"

#: Ignore anything older than this on restore. A restart is usually seconds;
#: an hour means the process was down long enough that the room has moved on.
MAX_AGE = 3600.0

#: Cap what is written per chat. A snapshot is a convenience, not an archive,
#: and a 50-track queue of full metadata dicts is a lot of JSON to rewrite.
MAX_TRACKS = 50

#: Fields worth keeping. Stream URLs are deliberately excluded: they are
#: signed, short-lived, and a restored one would fail in a way that looks
#: like a bug rather than an expiry.
_KEEP = (
    "title",
    "artist",
    "duration",
    "url",
    "id",
    "source",
    "thumbnail",
    "requester",
    "requester_id",
    "is_video",
    "is_live",
)


def _slim(track: dict[str, Any]) -> dict[str, Any]:
    """Keep the identifying fields, drop the volatile ones."""
    return {key: track[key] for key in _KEEP if track.get(key) is not None}


def _restorable(track: dict[str, Any]) -> bool:
    """A track needs something to re-resolve from."""
    return bool(track.get("url") or track.get("id") or track.get("title"))


async def snapshot_chat(chat_id: int) -> bool:
    """Write one chat's queue state. True if anything was stored."""
    from bot.services.queue import queue_manager

    try:
        pending = await queue_manager.get_queue(chat_id)
        current = await queue_manager.get_current(chat_id)
        loop_mode = await queue_manager.get_loop(chat_id)
        volume = await queue_manager.get_volume(chat_id)
    except Exception as exc:
        logger.debug("Could not read queue state for %s: %s", chat_id, exc)
        return False

    tracks = [_slim(t) for t in pending[:MAX_TRACKS] if _restorable(t)]
    saved_current = _slim(current) if current and _restorable(current) else None

    if not tracks and not saved_current:
        await forget(chat_id)
        return False

    payload = {
        "tracks": tracks,
        "current": saved_current,
        "loop": getattr(loop_mode, "value", str(loop_mode)),
        "volume": int(volume),
        "saved_at": time.time(),
    }
    try:
        await database._set(COLLECTION, str(chat_id), payload)
        return True
    except Exception as exc:
        logger.warning("Could not save the queue for %s: %s", chat_id, exc)
        return False


async def snapshot_all(*, timeout: float = 10.0) -> int:
    """Save every active chat. Returns how many were written.

    Bounded by a timeout because this runs on the shutdown path, where a
    hung database write would stop the process from exiting cleanly and the
    platform would eventually kill it mid-write.
    """
    from bot.services.queue import queue_manager

    chat_ids = set(queue_manager._queues) | set(queue_manager._current)
    if not chat_ids:
        return 0

    async def run() -> int:
        results = await asyncio.gather(
            *(snapshot_chat(chat_id) for chat_id in chat_ids),
            return_exceptions=True,
        )
        return sum(1 for r in results if r is True)

    try:
        saved = await asyncio.wait_for(run(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Queue snapshot timed out after %.0fs", timeout)
        return 0
    except Exception as exc:
        logger.warning("Queue snapshot failed: %s", exc)
        return 0

    if saved:
        logger.info("Saved the queue for %d chat(s)", saved)
    return saved


async def restore_chat(chat_id: int) -> dict[str, Any] | None:
    """Load one chat's saved queue back into the queue manager.

    Returns a summary dict, or None when there was nothing usable. The
    snapshot is consumed either way — a restore that half-worked should not
    be retried on the next boot.
    """
    from bot.services.queue import LoopMode, queue_manager

    try:
        doc = await database._get(COLLECTION, str(chat_id))
    except Exception as exc:
        logger.debug("Could not read the snapshot for %s: %s", chat_id, exc)
        return None

    if not doc or not isinstance(doc, dict):
        return None

    age = time.time() - float(doc.get("saved_at") or 0)
    if age > MAX_AGE:
        logger.info("Discarding a %.0f-minute-old queue for %s", age / 60, chat_id)
        await forget(chat_id)
        return None

    tracks = [t for t in doc.get("tracks") or [] if isinstance(t, dict) and _restorable(t)]
    current = doc.get("current") if isinstance(doc.get("current"), dict) else None

    restored = 0
    if tracks:
        restored = await queue_manager.add_many(chat_id, tracks)

    try:
        loop_value = str(doc.get("loop") or "off")
        for mode in LoopMode:
            if mode.value == loop_value:
                await queue_manager.set_loop(chat_id, mode)
                break
        volume = int(doc.get("volume") or 0)
        if volume:
            await queue_manager.set_volume(chat_id, volume)
    except Exception as exc:
        logger.debug("Could not restore settings for %s: %s", chat_id, exc)

    await forget(chat_id)

    if not restored and not current:
        return None
    return {
        "chat_id": chat_id,
        "restored": restored,
        "current": current,
        "age": age,
    }


async def restore_all() -> list[dict[str, Any]]:
    """Restore every saved chat. Returns one summary per chat that came back."""
    try:
        docs = await database._all(COLLECTION)
    except Exception as exc:
        logger.warning("Could not list saved queues: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for key in list(docs):
        try:
            chat_id = int(key)
        except (TypeError, ValueError):
            continue
        summary = await restore_chat(chat_id)
        if summary:
            out.append(summary)

    if out:
        total = sum(s["restored"] for s in out)
        logger.info("Restored %d track(s) across %d chat(s)", total, len(out))
    return out


async def forget(chat_id: int) -> None:
    try:
        await database._delete(COLLECTION, str(chat_id))
    except Exception as exc:
        logger.debug("Could not clear the snapshot for %s: %s", chat_id, exc)
