"""Play history tracker."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

MAX_HISTORY = 30


class HistoryTracker:
    def __init__(self) -> None:
        self._global: deque[dict[str, Any]] = deque(maxlen=100)
        self._per_chat: dict[int, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=MAX_HISTORY)
        )
        self._lock = asyncio.Lock()
        self._total_plays = 0

    async def record(self, chat_id: int, track: dict[str, Any]) -> None:
        entry = {
            "title": track.get("title", "Unknown"),
            "artist": track.get("artist", ""),
            "url": track.get("url", ""),
            "requester": track.get("requester", ""),
        }
        async with self._lock:
            self._per_chat[chat_id].append(entry)
            self._global.append({**entry, "chat_id": chat_id})
            self._total_plays += 1

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        history = list(self._per_chat.get(chat_id, []))
        return history[-limit:][::-1]

    async def get_global_history(self, limit: int = 10) -> list[dict[str, Any]]:
        return list(self._global)[-limit:][::-1]

    @property
    def total_plays(self) -> int:
        return self._total_plays

    @property
    def active_chats(self) -> int:
        return len(self._per_chat)


history_tracker = HistoryTracker()
