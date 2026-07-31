"""Voice chat streaming via PyTgCalls."""

from __future__ import annotations

import logging
import os
from typing import Any

from pytgcalls import PyTgCalls, filters
from pytgcalls.types import MediaStream, Update
from pytgcalls.types.stream import StreamEnded

from bot.services.queue import queue_manager

logger = logging.getLogger(__name__)


class StreamManager:
    def __init__(self) -> None:
        self._calls: PyTgCalls | None = None
        self._user_client = None
        self._playing: dict[int, bool] = {}
        self._paused: dict[int, bool] = {}
        self._on_track_end_callbacks: list = []

    def setup(self, user_client) -> PyTgCalls:
        self._user_client = user_client
        self._calls = PyTgCalls(user_client)

        @self._calls.on_update(filters.stream_end)
        async def on_stream_end(_: PyTgCalls, update: Update) -> None:
            if isinstance(update, StreamEnded):
                chat_id = update.chat_id
                await self._handle_end(chat_id)

        return self._calls

    async def _handle_end(self, chat_id: int) -> None:
        self._playing[chat_id] = False
        self._paused[chat_id] = False
        next_track = await queue_manager.next_track(chat_id)
        if next_track:
            await self.play(chat_id, next_track)
        else:
            for cb in self._on_track_end_callbacks:
                try:
                    await cb(chat_id)
                except Exception as exc:
                    logger.error("Track end callback error: %s", exc)

    @property
    def calls(self) -> PyTgCalls:
        if not self._calls:
            raise RuntimeError("StreamManager not initialized")
        return self._calls

    def _build_stream(self, track: dict[str, Any], volume: int = 100) -> MediaStream:
        url = track.get("stream_url") or track.get("url", "")
        is_video = track.get("is_video", False)

        filters: list[str] = []
        speed = track.get("_speed") or 1.0
        if speed != 1.0:
            filters.append(f"atempo={speed}")
        if volume != 100:
            filters.append(f"volume={volume / 100:.2f}")
        ffmpeg_params = f"-af {','.join(filters)}" if filters else None

        return MediaStream(
            url,
            ffmpeg_parameters=ffmpeg_params,
            video_flags=None if not is_video else MediaStream.Flags.AUTO_DETECT,
        )

    async def play(self, chat_id: int, track: dict[str, Any]) -> None:
        from bot.services.chat_settings import chat_settings

        speed = await chat_settings.get(chat_id, "speed")
        if speed and speed != 1.0:
            track = {**track, "_speed": speed}
        vol = await queue_manager.get_volume(chat_id)
        stream = self._build_stream(track, vol)
        await self._calls.play(chat_id, stream)
        await queue_manager.set_current(chat_id, track)
        self._playing[chat_id] = True
        self._paused[chat_id] = False

    async def pause(self, chat_id: int) -> None:
        await self._calls.pause(chat_id)
        self._paused[chat_id] = True

    async def resume(self, chat_id: int) -> None:
        await self._calls.resume(chat_id)
        self._paused[chat_id] = False

    async def stop(self, chat_id: int) -> None:
        try:
            await self._calls.leave_call(chat_id)
        except Exception:
            pass
        self._playing[chat_id] = False
        self._paused[chat_id] = False
        await queue_manager.set_current(chat_id, None)

    async def skip(self, chat_id: int) -> dict[str, Any] | None:
        try:
            await self._calls.leave_call(chat_id)
        except Exception:
            pass
        self._playing[chat_id] = False
        next_track = await queue_manager.skip(chat_id)
        if next_track:
            await self.play(chat_id, next_track)
        return next_track

    async def change_volume(self, chat_id: int, volume: int) -> int:
        vol = await queue_manager.set_volume(chat_id, volume)
        current = await queue_manager.get_current(chat_id)
        if current and self._playing.get(chat_id):
            await self.play(chat_id, current)
        return vol

    def is_playing(self, chat_id: int) -> bool:
        return self._playing.get(chat_id, False)

    def is_paused(self, chat_id: int) -> bool:
        return self._paused.get(chat_id, False)

    def on_track_end(self, callback) -> None:
        self._on_track_end_callbacks.append(callback)


stream_manager = StreamManager()
