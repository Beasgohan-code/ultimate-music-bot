"""Voice chat streaming via PyTgCalls.

Handles per-chat quality, volume, speed, mute state and seeking, and drives
queue advancement when a track ends.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from pytgcalls import PyTgCalls
from pytgcalls import filters as pytg_filters
from pytgcalls.types import AudioQuality, MediaStream, Update, VideoQuality
from pytgcalls.types.stream import StreamEnded

from bot.config import config
from bot.services.queue import queue_manager

logger = logging.getLogger(__name__)

#: How many consecutive unplayable tracks to skip before giving up. Without a
#: cap, a queue of dead links would spin through every entry on each failure.
MAX_AUTOSKIP = 5

AUDIO_QUALITY_MAP = {
    "studio": AudioQuality.STUDIO,
    "high": AudioQuality.HIGH,
    "medium": AudioQuality.MEDIUM,
    "low": AudioQuality.LOW,
}

VIDEO_QUALITY_MAP = {
    "uhd_4k": VideoQuality.UHD_4K,
    "qhd_2k": VideoQuality.QHD_2K,
    "fhd_1080p": VideoQuality.FHD_1080p,
    "hd_720p": VideoQuality.HD_720p,
    "sd_480p": VideoQuality.SD_480p,
    "sd_360p": VideoQuality.SD_360p,
}


class StreamManager:
    def __init__(self) -> None:
        self._calls: PyTgCalls | None = None
        self._user_client: Any = None
        self._playing: dict[int, bool] = {}
        self._paused: dict[int, bool] = {}
        self._muted: dict[int, bool] = {}
        self._started_at: dict[int, float] = {}
        self._offset: dict[int, int] = {}
        self._on_track_end_callbacks: list[Callable] = []
        self._on_queue_empty_callbacks: list[Callable] = []
        self._on_autoskip_callbacks: list[Callable] = []

    # ── lifecycle ───────────────────────────────────────────────────────
    def setup(self, user_client) -> PyTgCalls:
        self._user_client = user_client
        self._calls = PyTgCalls(user_client)

        @self._calls.on_update(pytg_filters.stream_end)
        async def on_stream_end(_: PyTgCalls, update: Update) -> None:
            if isinstance(update, StreamEnded):
                await self._handle_end(update.chat_id)

        return self._calls

    @property
    def calls(self) -> PyTgCalls:
        if not self._calls:
            raise RuntimeError("StreamManager is not initialised — call setup() first")
        return self._calls

    @property
    def active_chats(self) -> list[int]:
        return [cid for cid, playing in self._playing.items() if playing]

    async def _handle_end(self, chat_id: int) -> None:
        self._playing[chat_id] = False
        self._paused[chat_id] = False

        # One unplayable track used to kill the whole queue: the first failure
        # stopped the stream outright, so a single dead link or geo-blocked
        # video ended the session. Skip past failures instead, with a cap so a
        # queue full of broken links can't spin.
        next_track = None
        skipped: list[str] = []
        for _ in range(MAX_AUTOSKIP):
            candidate = await queue_manager.next_track(chat_id)
            if not candidate:
                break
            try:
                await self.play(chat_id, candidate)
                next_track = candidate
                break
            except Exception as exc:
                title = candidate.get("title", "unknown")
                skipped.append(title)
                logger.warning(
                    "Auto-advance skipped %r in %s: %s", title, chat_id, exc
                )

        if skipped:
            for cb in self._on_autoskip_callbacks:
                try:
                    await cb(chat_id, skipped)
                except Exception as exc:
                    logger.error("Auto-skip callback error: %s", exc)

        if next_track:
            for cb in self._on_track_end_callbacks:
                try:
                    await cb(chat_id, next_track)
                except Exception as exc:
                    logger.error("Track-end callback error: %s", exc)
        else:
            await self.stop(chat_id)
            for cb in self._on_queue_empty_callbacks:
                try:
                    await cb(chat_id)
                except Exception as exc:
                    logger.error("Queue-empty callback error: %s", exc)

    # ── stream construction ─────────────────────────────────────────────
    async def _qualities(self, chat_id: int) -> tuple[AudioQuality, VideoQuality]:
        from bot.services.database import database

        audio_key = str(await database.get_chat_value(chat_id, "audio_quality", config.audio_quality))
        video_key = str(await database.get_chat_value(chat_id, "video_quality", config.video_quality))
        return (
            AUDIO_QUALITY_MAP.get(audio_key.lower(), AudioQuality.HIGH),
            VIDEO_QUALITY_MAP.get(video_key.lower(), VideoQuality.HD_720p),
        )

    def _ffmpeg_params(self, volume: int, speed: float, seek: int) -> str | None:
        """Build ffmpeg args for volume, atempo and seeking."""
        parts: list[str] = []
        if seek > 0:
            parts.append(f"-ss {seek}")

        audio_filters: list[str] = []
        if speed and abs(speed - 1.0) > 0.01:
            # atempo only accepts 0.5–2.0, chain for wider ranges
            remaining = speed
            while remaining > 2.0:
                audio_filters.append("atempo=2.0")
                remaining /= 2.0
            while remaining < 0.5:
                audio_filters.append("atempo=0.5")
                remaining /= 0.5
            audio_filters.append(f"atempo={remaining:.3f}")
        if volume != 100:
            audio_filters.append(f"volume={max(1, min(200, volume)) / 100:.2f}")

        if audio_filters:
            parts.append(f"-af {','.join(audio_filters)}")
        return " ".join(parts) if parts else None

    @staticmethod
    def _is_playable(url: str) -> bool:
        """Is this something ffmpeg can actually read as media?

        A page URL is not. ``https://youtube.com/watch?v=...`` is HTML, and
        ffmpeg fails on it with a decode error rather than anything that
        explains itself.
        """
        if not url:
            return False
        low = url.lower()
        if low.startswith(("/", "file://")):
            return True
        return not any(
            host in low
            for host in (
                "youtube.com/watch",
                "youtu.be/",
                "youtube.com/shorts",
                "open.spotify.com",
                "music.apple.com",
                "deezer.com",
                "soundcloud.com/",
            )
        )

    @staticmethod
    def _expired(url: str) -> bool:
        """True when a signed CDN URL has passed its ``expire`` stamp.

        YouTube stamps an expiry into the query string, normally a few hours
        out. A queue can easily outlive it, and the failure is a 403 several
        seconds into playback rather than at play() time.
        """
        match = re.search(r"[?&]expire=(\d{9,})", url or "")
        if not match:
            return False
        try:
            # 60s of slack: a URL about to die mid-track is already useless.
            return int(match.group(1)) <= time.time() + 60
        except ValueError:
            return False

    async def _playable_url(self, track: dict[str, Any]) -> str:
        """Return a URL ffmpeg can stream, re-resolving it when stale.

        Queues outlive their stream URLs. A snapshot restored after a restart
        deliberately drops the signed URL (it would be dead anyway), and a
        long queue can sit for hours past the CDN expiry stamp. Both used to
        reach ffmpeg as-is: the first as an HTML watch page, the second as a
        403 a few seconds in. Resolve late instead — at the moment of play,
        when the URL only has to survive one track.
        """
        url = track.get("stream_url") or ""
        if url and self._is_playable(url) and not self._expired(url):
            return url

        query = track.get("url") or track.get("id") or track.get("title") or ""
        if not query:
            return url or track.get("url", "")

        try:
            from bot.services.music import get_stream_url

            fresh = await get_stream_url(query)
        except Exception as exc:
            logger.warning("Could not refresh the stream for %r: %s", query, exc)
            fresh = None

        if fresh and fresh.get("stream_url"):
            logger.info("Refreshed a stale stream URL for %r", track.get("title", query))
            # Mutate in place: the caller holds this dict as the current
            # track, and seek/replay must not resolve it a second time.
            track["stream_url"] = fresh["stream_url"]
            for key in ("thumbnail", "duration", "is_live"):
                if not track.get(key) and fresh.get(key):
                    track[key] = fresh[key]
            return fresh["stream_url"]

        # Nothing better available. Hand back what we had so the caller's
        # existing failure path reports it, rather than inventing a new one.
        return url or track.get("url", "")

    async def _build_stream(
        self, chat_id: int, track: dict[str, Any], *, seek: int = 0
    ) -> MediaStream:
        url = await self._playable_url(track)
        is_video = bool(track.get("is_video"))
        audio_q, video_q = await self._qualities(chat_id)
        volume = await queue_manager.get_volume(chat_id)
        speed = float(track.get("_speed") or 1.0)

        return MediaStream(
            url,
            audio_parameters=audio_q,
            video_parameters=video_q,
            video_flags=MediaStream.Flags.AUTO_DETECT if is_video else MediaStream.Flags.IGNORE,
            ffmpeg_parameters=self._ffmpeg_params(volume, speed, seek),
        )

    # ── playback controls ───────────────────────────────────────────────
    async def play(self, chat_id: int, track: dict[str, Any], *, seek: int = 0) -> None:
        from bot.services.database import database

        speed = await database.get_chat_value(chat_id, "speed", 1.0)
        if speed and float(speed) != 1.0:
            track = {**track, "_speed": float(speed)}

        stream = await self._build_stream(chat_id, track, seek=seek)
        await self.calls.play(chat_id, stream)
        await queue_manager.set_current(chat_id, track)
        self._playing[chat_id] = True
        self._paused[chat_id] = False
        self._muted[chat_id] = False
        self._started_at[chat_id] = time.time()
        self._offset[chat_id] = seek

    async def pause(self, chat_id: int) -> None:
        await self.calls.pause(chat_id)
        self._paused[chat_id] = True

    async def resume(self, chat_id: int) -> None:
        await self.calls.resume(chat_id)
        self._paused[chat_id] = False
        self._started_at[chat_id] = time.time() - self.elapsed(chat_id)

    async def mute(self, chat_id: int) -> None:
        await self.calls.mute(chat_id)
        self._muted[chat_id] = True

    async def unmute(self, chat_id: int) -> None:
        await self.calls.unmute(chat_id)
        self._muted[chat_id] = False

    async def stop(self, chat_id: int) -> None:
        try:
            await self.calls.leave_call(chat_id)
        except Exception as exc:
            logger.debug("leave_call(%s) failed: %s", chat_id, exc)
        self._playing[chat_id] = False
        self._paused[chat_id] = False
        self._muted[chat_id] = False
        self._offset.pop(chat_id, None)
        self._started_at.pop(chat_id, None)
        await queue_manager.set_current(chat_id, None)

    async def skip(self, chat_id: int, to: int = 0) -> dict[str, Any] | None:
        """Advance to the next track, or to queue position ``to`` (1-based)."""
        if to > 1:
            await queue_manager.drop_before(chat_id, to - 1)
        next_track = await queue_manager.skip(chat_id)
        if next_track:
            await self.play(chat_id, next_track)
        else:
            await self.stop(chat_id)
        return next_track

    async def seek(self, chat_id: int, seconds: int) -> int | None:
        """Seek to an absolute position; returns the new position."""
        current = await queue_manager.get_current(chat_id)
        if not current or current.get("is_live"):
            return None
        duration = current.get("duration") or 0
        position = max(0, seconds)
        if duration:
            position = min(position, max(0, int(duration) - 5))
        await self.play(chat_id, current, seek=position)
        return position

    async def seek_relative(self, chat_id: int, delta: int) -> int | None:
        return await self.seek(chat_id, self.elapsed(chat_id) + delta)

    async def change_volume(self, chat_id: int, volume: int) -> int:
        vol = await queue_manager.set_volume(chat_id, volume)
        if self.is_playing(chat_id):
            try:
                # Native volume change avoids restarting the stream when possible.
                await self.calls.change_volume_call(chat_id, vol)
            except Exception:
                current = await queue_manager.get_current(chat_id)
                if current:
                    await self.play(chat_id, current, seek=self.elapsed(chat_id))
        return vol

    # ── state ───────────────────────────────────────────────────────────
    def is_playing(self, chat_id: int) -> bool:
        return self._playing.get(chat_id, False)

    def is_paused(self, chat_id: int) -> bool:
        return self._paused.get(chat_id, False)

    def is_muted(self, chat_id: int) -> bool:
        return self._muted.get(chat_id, False)

    def elapsed(self, chat_id: int) -> int:
        """Seconds into the current track (best effort)."""
        started = self._started_at.get(chat_id)
        if not started or not self._playing.get(chat_id):
            return self._offset.get(chat_id, 0)
        if self._paused.get(chat_id):
            return self._offset.get(chat_id, 0)
        return int(self._offset.get(chat_id, 0) + (time.time() - started))

    def on_track_end(self, callback: Callable) -> None:
        self._on_track_end_callbacks.append(callback)

    def on_queue_empty(self, callback: Callable) -> None:
        self._on_queue_empty_callbacks.append(callback)

    def on_autoskip(self, callback: Callable) -> None:
        """Notified with (chat_id, [titles]) when tracks were skipped as unplayable."""
        self._on_autoskip_callbacks.append(callback)


stream_manager = StreamManager()
