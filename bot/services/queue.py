"""Per-chat music queue manager."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from enum import Enum
from typing import Any


class LoopMode(str, Enum):
    OFF = "off"
    SINGLE = "single"
    ALL = "all"


class QueueManager:
    def __init__(self, max_size: int = 50) -> None:
        self._queues: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self._current: dict[int, dict[str, Any] | None] = {}
        self._loop: dict[int, LoopMode] = defaultdict(lambda: LoopMode.OFF)
        self._volume: dict[int, int] = defaultdict(lambda: 100)
        self._lock = asyncio.Lock()
        self.max_size = max_size

    async def add(self, chat_id: int, track: dict[str, Any]) -> int:
        async with self._lock:
            q = self._queues[chat_id]
            if len(q) >= self.max_size:
                raise ValueError(f"Queue full (max {self.max_size})")
            q.append(track)
            return len(q)

    async def add_many(self, chat_id: int, tracks: list[dict[str, Any]]) -> int:
        added = 0
        for t in tracks:
            try:
                await self.add(chat_id, t)
                added += 1
            except ValueError:
                break
        return added

    async def get_queue(self, chat_id: int) -> list[dict[str, Any]]:
        return list(self._queues.get(chat_id, []))

    async def get_current(self, chat_id: int) -> dict[str, Any] | None:
        return self._current.get(chat_id)

    async def set_current(self, chat_id: int, track: dict[str, Any] | None) -> None:
        self._current[chat_id] = track

    async def next_track(self, chat_id: int) -> dict[str, Any] | None:
        async with self._lock:
            loop = self._loop[chat_id]
            current = self._current.get(chat_id)

            if loop == LoopMode.SINGLE and current:
                return current

            q = self._queues[chat_id]
            if q:
                track = q.pop(0)
                self._current[chat_id] = track
                if loop == LoopMode.ALL and current:
                    q.append(current)
                return track

            if loop == LoopMode.ALL and current:
                return current

            self._current[chat_id] = None
            return None

    async def skip(self, chat_id: int) -> dict[str, Any] | None:
        self._loop[chat_id] = LoopMode.OFF if self._loop[chat_id] == LoopMode.SINGLE else self._loop[chat_id]
        return await self.next_track(chat_id)

    async def clear(self, chat_id: int) -> int:
        async with self._lock:
            count = len(self._queues[chat_id])
            self._queues[chat_id].clear()
            return count

    async def shuffle(self, chat_id: int) -> None:
        import random

        async with self._lock:
            random.shuffle(self._queues[chat_id])

    async def remove_at(self, chat_id: int, index: int) -> dict[str, Any] | None:
        async with self._lock:
            q = self._queues[chat_id]
            if 0 <= index < len(q):
                return q.pop(index)
            return None

    async def toggle_loop(self, chat_id: int) -> LoopMode:
        modes = list(LoopMode)
        current = self._loop[chat_id]
        idx = (modes.index(current) + 1) % len(modes)
        self._loop[chat_id] = modes[idx]
        return modes[idx]

    async def get_loop(self, chat_id: int) -> LoopMode:
        return self._loop[chat_id]

    async def set_volume(self, chat_id: int, vol: int) -> int:
        vol = max(1, min(200, vol))
        self._volume[chat_id] = vol
        return vol

    async def get_volume(self, chat_id: int) -> int:
        return self._volume[chat_id]

    async def is_empty(self, chat_id: int) -> bool:
        return not self._queues[chat_id] and not self._current.get(chat_id)


queue_manager = QueueManager()
