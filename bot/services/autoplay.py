"""Keep the music going when the queue runs dry.

Without this the bot simply leaves: the last track ends, the voice chat
empties, and someone has to notice and queue something. Autoplay picks a
related track from what was just playing and keeps the room alive.

Two properties matter more than the picking itself:

*   **It must not repeat.** A naive "search for something similar" loops
    between the same three songs within minutes, because the seed keeps
    matching the same results. Every chat keeps a rolling memory of what it
    has already auto-played and filters against it.
*   **It must not run away.** A failing extractor, an empty result, or a
    dead chat should stop autoplay rather than retrying forever in the
    background. Consecutive failures disable it for the chat until someone
    plays something manually.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Callable, Awaitable

from bot.services.database import database

logger = logging.getLogger(__name__)

#: How many recent auto-picks to remember per chat. Large enough that a
#: rotation is not obvious, small enough that a niche seed does not starve.
MEMORY = 40

#: Consecutive failures before autoplay switches itself off for a chat.
MAX_FAILURES = 3

#: Chat setting key. Off by default: a bot that keeps playing on its own in
#: a group that did not ask for it is a nuisance, not a feature.
SETTING_KEY = "autoplay"


class AutoplayManager:
    """Per-chat autoplay state: memory, failure count, enable flag."""

    def __init__(self) -> None:
        self._recent: dict[int, deque[str]] = {}
        self._failures: dict[int, int] = {}
        #: Set while a pick is in flight, so a second queue-empty event for
        #: the same chat cannot start a parallel search.
        self._busy: set[int] = set()

    # ── enable / disable ────────────────────────────────────────────────
    async def is_enabled(self, chat_id: int) -> bool:
        value = await database.get_chat_value(chat_id, SETTING_KEY)
        return str(value).lower() in ("1", "true", "yes", "on")

    async def set_enabled(self, chat_id: int, enabled: bool) -> None:
        await database.set_chat_value(chat_id, SETTING_KEY, "1" if enabled else "0")
        if enabled:
            # A fresh start: past failures should not disable it immediately.
            self._failures.pop(chat_id, None)

    # ── memory ──────────────────────────────────────────────────────────
    def remember(self, chat_id: int, track: dict[str, Any]) -> None:
        key = self._key(track)
        if not key:
            return
        memory = self._recent.setdefault(chat_id, deque(maxlen=MEMORY))
        if key not in memory:
            memory.append(key)

    def seen(self, chat_id: int, track: dict[str, Any]) -> bool:
        key = self._key(track)
        return bool(key) and key in self._recent.get(chat_id, ())

    @staticmethod
    def _key(track: dict[str, Any]) -> str:
        """Identity for dedup: the id when present, else the title.

        Falling back to the title matters because SoundCloud and Niconico
        results carry different id shapes than YouTube, and the same song
        found on two backends should still count as a repeat.
        """
        ident = str(track.get("id") or "").strip()
        if ident:
            return ident
        return (track.get("title") or "").strip().lower()

    def forget(self, chat_id: int) -> None:
        self._recent.pop(chat_id, None)
        self._failures.pop(chat_id, None)
        self._busy.discard(chat_id)

    # ── failure tracking ────────────────────────────────────────────────
    def note_failure(self, chat_id: int) -> int:
        count = self._failures.get(chat_id, 0) + 1
        self._failures[chat_id] = count
        return count

    def note_success(self, chat_id: int) -> None:
        self._failures.pop(chat_id, None)

    def exhausted(self, chat_id: int) -> bool:
        return self._failures.get(chat_id, 0) >= MAX_FAILURES

    # ── the pick ────────────────────────────────────────────────────────
    async def pick(
        self,
        chat_id: int,
        seed: dict[str, Any] | None,
        *,
        fetch: Callable[[str, int], Awaitable[list[dict[str, Any]]]],
        limit: int = 8,
    ) -> dict[str, Any] | None:
        """Choose the next track to play after `seed`, or None.

        `fetch` is injected rather than imported so this stays testable
        without a network and without importing the extractor at module
        scope, which would make a circular import with music.py.
        """
        if not seed:
            return None
        if chat_id in self._busy:
            logger.debug("Autoplay already running for %s", chat_id)
            return None

        self._busy.add(chat_id)
        try:
            query = self._seed_query(seed)
            if not query:
                return None
            try:
                candidates = await fetch(query, limit)
            except Exception as exc:  # a dead extractor must not kill the loop
                logger.warning("Autoplay lookup failed for %s: %s", chat_id, exc)
                self.note_failure(chat_id)
                return None

            seed_key = self._key(seed)
            for candidate in candidates or []:
                if not candidate:
                    continue
                if self._key(candidate) == seed_key:
                    continue
                if self.seen(chat_id, candidate):
                    continue
                self.remember(chat_id, candidate)
                self.note_success(chat_id)
                candidate = dict(candidate)
                candidate["requester"] = "Autoplay"
                candidate["requester_id"] = 0
                candidate["_autoplay"] = True
                return candidate

            # Everything came back already-seen: the seed is exhausted.
            logger.info("Autoplay found nothing new for %s", chat_id)
            self.note_failure(chat_id)
            return None
        finally:
            self._busy.discard(chat_id)

    @staticmethod
    def _seed_query(seed: dict[str, Any]) -> str:
        title = (seed.get("title") or "").strip()
        artist = (seed.get("artist") or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        return title or artist


autoplay = AutoplayManager()
