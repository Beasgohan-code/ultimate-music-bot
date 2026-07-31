"""Auto-leave idle voice chats."""

from __future__ import annotations

import asyncio
import logging
import time

from bot.config import config
from bot.services.queue import queue_manager
from bot.services.stream import stream_manager

logger = logging.getLogger(__name__)


class AutoLeaveManager:
    def __init__(self) -> None:
        self._last_activity: dict[int, float] = {}
        self._task: asyncio.Task | None = None

    def touch(self, chat_id: int) -> None:
        self._last_activity[chat_id] = time.time()

    async def start(self) -> None:
        if config.auto_leave_idle <= 0:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.time()
            idle_limit = config.auto_leave_idle
            for chat_id, last in list(self._last_activity.items()):
                if now - last < idle_limit:
                    continue
                if stream_manager.is_playing(chat_id):
                    continue
                if not await queue_manager.is_empty(chat_id):
                    continue
                try:
                    await stream_manager.stop(chat_id)
                    logger.info("Auto-left idle VC in chat %s", chat_id)
                except Exception as exc:
                    logger.debug("Auto-leave failed for %s: %s", chat_id, exc)
                self._last_activity.pop(chat_id, None)


auto_leave = AutoLeaveManager()
