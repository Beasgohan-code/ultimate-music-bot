"""Bot runtime statistics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from bot.services.history import history_tracker
from bot.services.queue import queue_manager


@dataclass
class BotStats:
    started_at: float = field(default_factory=time.time)
    commands_handled: int = 0
    streams_started: int = 0
    errors_count: int = 0

    def uptime_str(self) -> str:
        secs = int(time.time() - self.started_at)
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        parts.append(f"{m}m {s}s")
        return " ".join(parts)

    async def summary(self) -> dict[str, int | str]:
        return {
            "uptime": self.uptime_str(),
            "commands": self.commands_handled,
            "streams": self.streams_started,
            "total_plays": history_tracker.total_plays,
            "active_chats": history_tracker.active_chats,
            "errors": self.errors_count,
        }


bot_stats = BotStats()
