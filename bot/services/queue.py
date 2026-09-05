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
        self.max_size = max_size or config.max_queue_size

    # ── adding ──────────────────────────────────────────────────────────
    async def add(self, chat_id: int, track: dict[str, Any]) -> int:
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                raise ValueError(f"Queue is full (max {self.max_size} tracks)")
            q.append(track)
            return len(q)

    async def add_many(self, chat_id: int, tracks: list[dict[str, Any]]) -> int:
        added = 0
        for track in tracks:
            try:
                await self.add(chat_id, track)
                added += 1
            except ValueError:
                break
        return added

    async def add_front(self, chat_id: int, track: dict[str, Any]) -> int:
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                raise ValueError(f"Queue is full (max {self.max_size} tracks)")
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
                if mode == LoopMode.ALL and current:
                    q.append(current)
                self._current[chat_id] = track
                return track

            if mode == LoopMode.ALL and current:
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
        async with self._lock:
            count = len(self._queues[chat_id])
            self._queues[chat_id].clear()
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
        async with self._lock:
            self._queues.pop(chat_id, None)
            self._current.pop(chat_id, None)
            self._loop_count[chat_id] = 0
            self._loop[chat_id] = LoopMode.OFF


queue_manager = QueueManager()
