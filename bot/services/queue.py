"""Per-chat music queue manager."""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from enum import Enum
from typing import Any

from bot.config import config


class LoopMode(str, Enum):
    OFF = "off"
    SINGLE = "single"
    ALL = "all"


class QueueManager:
    def __init__(self, max_size: int | None = None) -> None:
        self._queues: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self._current: dict[int, dict[str, Any] | None] = {}
        self._loop: dict[int, LoopMode] = defaultdict(lambda: LoopMode.OFF)
        #: Remaining repeats when a numeric loop count is set (Yukki-style).
        self._loop_count: dict[int, int] = defaultdict(int)
        self._volume: dict[int, int] = defaultdict(lambda: config.default_volume)
        self._lock = asyncio.Lock()
        #: Chats whose current track may rejoin the rotation under
        #: LoopMode.ALL. /clear removes the chat so a cleared track
        #: cannot come back around.
        self._requeue_current: set[int] = set()
        self.max_size = max_size or config.max_queue_size

    # ── adding ──────────────────────────────────────────────────────────
    @property
    def full_message(self) -> str:
        return f"Queue is full (max {self.max_size} tracks)"

    def _space_left(self, chat_id: int) -> int:
        return max(0, self.max_size - len(self._queues[chat_id]))

    async def add(self, chat_id: int, track: dict[str, Any]) -> int:
        """Append a track. Raises ValueError when the queue is full.

        Prefer :meth:`try_add` at call sites that cannot handle an exception —
        an escaped ValueError here surfaces to the user as the generic
        "Something went wrong" card, which tells them nothing actionable.
        """
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                raise ValueError(self.full_message)
            q.append(track)
            return len(q)

    async def try_add(self, chat_id: int, track: dict[str, Any]) -> int | None:
        """Append a track, or return None when the queue is full.

        The non-raising twin of :meth:`add`, for the many call sites where a
        full queue is an ordinary outcome rather than an error.
        """
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                return None
            q.append(track)
            return len(q)

    async def add_many(self, chat_id: int, tracks: list[dict[str, Any]]) -> int:
        """Append as many tracks as fit, returning how many landed.

        Takes the lock once rather than per track: the old version called
        add() in a loop, so a concurrent add between iterations could fill the
        queue underneath it, and it stopped at the first rejection.
        """
        async with self._lock:
            q = self._queues[chat_id]
            room = max(0, self.max_size - len(q))
            fitting = tracks[:room]
            q.extend(fitting)
            return len(fitting)

    async def add_front(self, chat_id: int, track: dict[str, Any]) -> int:
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                raise ValueError(self.full_message)
            q.insert(0, track)
            return 1

    async def try_add_front(self, chat_id: int, track: dict[str, Any]) -> int | None:
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                return None
            q.insert(0, track)
            return 1

    # ── reading ─────────────────────────────────────────────────────────
    async def get_queue(self, chat_id: int) -> list[dict[str, Any]]:
        return list(self._queues.get(chat_id, []))

    async def get_current(self, chat_id: int) -> dict[str, Any] | None:
        return self._current.get(chat_id)

    async def set_current(self, chat_id: int, track: dict[str, Any] | None) -> None:
        self._current[chat_id] = track

    async def size(self, chat_id: int) -> int:
        return len(self._queues.get(chat_id, []))

    async def is_empty(self, chat_id: int) -> bool:
        return not self._queues.get(chat_id) and not self._current.get(chat_id)

    # ── advancing ───────────────────────────────────────────────────────
    async def next_track(self, chat_id: int) -> dict[str, Any] | None:
        async with self._lock:
            mode = self._loop[chat_id]
            current = self._current.get(chat_id)

            # Numeric loop: repeat the same track N more times.
            if self._loop_count[chat_id] > 0 and current:
                self._loop_count[chat_id] -= 1
                return current

            if mode == LoopMode.SINGLE and current:
                return current

            q = self._queues[chat_id]
            if q:
                track = q.pop(0)
                if mode == LoopMode.ALL and current and chat_id in self._requeue_current:
                    q.append(current)
                self._current[chat_id] = track
                self._requeue_current.add(chat_id)
                return track

            if mode == LoopMode.ALL and current and chat_id in self._requeue_current:
                return current

            self._current[chat_id] = None
            return None

    async def skip(self, chat_id: int) -> dict[str, Any] | None:
        """Skip ignores single-track looping, as users expect."""
        async with self._lock:
            self._loop_count[chat_id] = 0
            if self._loop[chat_id] == LoopMode.SINGLE:
                self._loop[chat_id] = LoopMode.OFF
        return await self.next_track(chat_id)

    async def drop_before(self, chat_id: int, count: int) -> int:
        """Discard ``count`` queued tracks (used by ``/skip 3``)."""
        async with self._lock:
            q = self._queues[chat_id]
            removed = min(count, len(q))
            del q[:removed]
            return removed

    # ── mutating ────────────────────────────────────────────────────────
    async def clear(self, chat_id: int) -> int:
        """Empty the pending queue.

        Under LoopMode.ALL, next_track() re-appends the *current* track so the
        rotation closes. That made a cleared track reappear: /clear emptied
        the list but left _current set, so the song the user just removed came
        back around and kept cycling. Dropping the loop's hold on it — while
        leaving _current itself alone, since /clear must not stop playback —
        keeps "clear" meaning what it says.
        """
        async with self._lock:
            count = len(self._queues[chat_id])
            self._queues[chat_id].clear()
            self._requeue_current.discard(chat_id)
            return count

    async def shuffle(self, chat_id: int) -> int:
        async with self._lock:
            random.shuffle(self._queues[chat_id])
            return len(self._queues[chat_id])

    async def remove_at(self, chat_id: int, index: int) -> dict[str, Any] | None:
        async with self._lock:
            q = self._queues[chat_id]
            if 0 <= index < len(q):
                return q.pop(index)
            return None

    async def move(self, chat_id: int, src: int, dst: int) -> bool:
        async with self._lock:
            q = self._queues[chat_id]
            if not (0 <= src < len(q)) or not (0 <= dst < len(q)):
                return False
            q.insert(dst, q.pop(src))
            return True

    # ── loop & volume ───────────────────────────────────────────────────
    async def toggle_loop(self, chat_id: int) -> LoopMode:
        modes = list(LoopMode)
        idx = (modes.index(self._loop[chat_id]) + 1) % len(modes)
        self._loop[chat_id] = modes[idx]
        self._loop_count[chat_id] = 0
        return modes[idx]

    async def set_loop(self, chat_id: int, mode: LoopMode) -> LoopMode:
        self._loop[chat_id] = mode
        self._loop_count[chat_id] = 0
        return mode

    async def set_loop_count(self, chat_id: int, count: int) -> int:
        """Repeat the current track ``count`` more times, then continue."""
        count = max(0, min(10, count))
        self._loop_count[chat_id] = count
        self._loop[chat_id] = LoopMode.OFF
        return count

    async def get_loop(self, chat_id: int) -> LoopMode:
        return self._loop[chat_id]

    async def get_loop_count(self, chat_id: int) -> int:
        return self._loop_count[chat_id]

    async def set_volume(self, chat_id: int, vol: int) -> int:
        vol = max(1, min(200, vol))
        self._volume[chat_id] = vol
        return vol

    async def get_volume(self, chat_id: int) -> int:
        return self._volume[chat_id]

    async def reset(self, chat_id: int) -> None:
        """Forget everything about a chat.

        Every per-chat structure must be dropped here. The defaultdicts are
        popped rather than reassigned: leaving a LoopMode.OFF entry behind for
        a chat the bot has left is a slow leak across a long-running process,
        and it is exactly how the stale-current bug survived /clear.
        """
        async with self._lock:
            self._queues.pop(chat_id, None)
            self._current.pop(chat_id, None)
            self._loop.pop(chat_id, None)
            self._loop_count.pop(chat_id, None)
            self._volume.pop(chat_id, None)
            self._requeue_current.discard(chat_id)


queue_manager = QueueManager()
