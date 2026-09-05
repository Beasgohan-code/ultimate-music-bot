"""Audio downloads with a Telegram file_id cache.

The expensive part of `/song` is not sending the file — it is downloading and
transcoding it. So the first time a track is requested we download it, upload
it once, and remember the resulting `file_id`. Every later request for the same
track resends that `file_id`, which costs one API call and no disk at all.

Optionally the upload is mirrored to a private storage channel
(`STORAGE_CHAT_ID`) so the file_id stays valid even if the original chat is
deleted, and so the cache survives across deployments.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from bot.config import DOWNLOAD_DIR, config
from bot.services.database import database

logger = logging.getLogger(__name__)

# One download per track at a time — two users asking for the same song
# simultaneously should not both pull it.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

_SAFE = re.compile(r"[^\w\s.\-()\[\]]+", re.UNICODE)


class DownloadError(RuntimeError):
    """Raised with a user-presentable message."""


def cache_key(track: dict[str, Any], *, video: bool = False) -> str:
    """Stable identity for a track, independent of how it was searched for."""
    ident = track.get("id") or track.get("url") or track.get("title", "")
    source = (track.get("source") or "yt").lower()
    return f"{'v' if video else 'a'}:{source}:{ident}"[:180]


def safe_filename(name: str, ext: str) -> str:
    cleaned = _SAFE.sub("", name).strip() or "track"
    return f"{cleaned[:60]}.{ext}"


async def _lock_for(key: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(key, asyncio.Lock())


async def cached_file_id(track: dict[str, Any], *, video: bool = False) -> str | None:
    """Return a previously uploaded file_id for this track, if we have one."""
    doc = await database.cached_track(cache_key(track, video=video))
    if not doc:
        return None
    file_id = doc.get("file_id") or ""
    return file_id or None


async def remember_file_id(
    track: dict[str, Any], file_id: str, *, video: bool = False
) -> None:
    payload = dict(track)
    payload["file_id"] = file_id
    await database.cache_track(cache_key(track, video=video), payload)


def _ytdl_download(track: dict[str, Any], target_dir: Path, video: bool) -> Path:
    """Blocking yt-dlp download. Runs in a worker thread."""
    import yt_dlp

    stem = str(target_dir / "media")
    if video:
        opts: dict[str, Any] = {
            "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "merge_output_format": "mp4",
            "outtmpl": f"{stem}.%(ext)s",
        }
    else:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": f"{stem}.%(ext)s",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": config.audio_bitrate,
                },
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
            "writethumbnail": True,
        }

    opts.update(
        {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": True,
        }
    )
    if config.cookies_file and os.path.isfile(config.cookies_file):
        opts["cookiefile"] = config.cookies_file
    if config.ytdlp_proxy:
        opts["proxy"] = config.ytdlp_proxy

    source = track.get("url") or track.get("title", "")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source])

    wanted = (".mp4", ".mkv", ".webm") if video else (".mp3", ".m4a", ".opus")
    candidates = [p for p in target_dir.iterdir() if p.suffix.lower() in wanted]
    if not candidates:
        raise DownloadError("The download produced no playable file.")
    return max(candidates, key=lambda p: p.stat().st_size)


async def download_track(track: dict[str, Any], *, video: bool = False) -> Path:
    """Download a track to disk and return the path. Caller must clean up."""
    if not shutil.which("ffmpeg"):
        raise DownloadError(
            "FFmpeg is not installed on the server, so I cannot convert audio."
        )

    target = DOWNLOAD_DIR / f"{int(time.time() * 1000)}-{os.getpid()}"
    target.mkdir(parents=True, exist_ok=True)
    try:
        path = await asyncio.to_thread(_ytdl_download, track, target, video)
    except DownloadError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except Exception as exc:  # yt-dlp raises a wide variety of errors
        shutil.rmtree(target, ignore_errors=True)
        logger.warning("Download failed for %s: %s", track.get("title"), exc)
        raise DownloadError("I could not download that track.") from exc

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > config.max_download_mb:
        shutil.rmtree(target, ignore_errors=True)
        raise DownloadError(
            f"That file is {size_mb:.0f} MB — over the {config.max_download_mb} MB limit."
        )
    return path


async def mirror_to_storage(bot, path: Path, track: dict[str, Any], video: bool) -> str | None:
    """Upload to the private storage channel and return its file_id.

    Uploading here first means the file_id is owned by a chat we control, so it
    keeps working no matter what happens to the chat that asked for the track.
    """
    if not config.storage_chat_id:
        return None
    from aiogram.types import FSInputFile

    caption = f"{track.get('title', 'Unknown')} — {track.get('artist', '')}".strip(" —")
    try:
        file = FSInputFile(path, filename=path.name)
        if video:
            sent = await bot.send_video(config.storage_chat_id, file, caption=caption)
            return sent.video.file_id if sent.video else None
        sent = await bot.send_audio(
            config.storage_chat_id,
            file,
            caption=caption,
            title=track.get("title"),
            performer=track.get("artist") or None,
            duration=track.get("duration") or None,
        )
        return sent.audio.file_id if sent.audio else None
    except Exception as exc:
        logger.warning("Storage mirror failed: %s", exc)
        return None


async def cleanup(path: Path) -> None:
    """Remove the temp directory a download lived in."""
    try:
        await asyncio.to_thread(shutil.rmtree, path.parent, True)
    except Exception:
        pass


async def prune_downloads(max_age_hours: int = 6) -> int:
    """Delete stale temp download dirs left behind by crashes."""
    if not DOWNLOAD_DIR.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in DOWNLOAD_DIR.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Pruned %d stale download dir(s)", removed)
    return removed


async def get_or_send_audio(
    message,
    track: dict[str, Any],
    *,
    video: bool = False,
    caption: str = "",
    reply_markup=None,
) -> bool:
    """Send a track as a file, using the cache when possible.

    Returns True if something was delivered to the user.
    """
    bot = message.bot
    chat_id = message.chat.id
    key = cache_key(track, video=video)

    # ── Fast path: we have already uploaded this exact track before.
    file_id = await cached_file_id(track, video=video)
    if file_id:
        try:
            if video:
                await bot.send_video(
                    chat_id, file_id, caption=caption, reply_markup=reply_markup
                )
            else:
                await bot.send_audio(
                    chat_id, file_id, caption=caption, reply_markup=reply_markup
                )
            logger.info("Cache hit for %s", track.get("title"))
            return True
        except Exception as exc:
            # file_id can rot (deleted from storage, migrated DC). Fall through
            # and re-download rather than failing the user's request.
            logger.info("Stale file_id for %s (%s) — re-downloading", key, exc)
            await database.cache_track(key, {**track, "file_id": ""})

    lock = await _lock_for(key)
    async with lock:
        # Another coroutine may have finished the download while we waited.
        file_id = await cached_file_id(track, video=video)
        if file_id:
            try:
                if video:
                    await bot.send_video(chat_id, file_id, caption=caption, reply_markup=reply_markup)
                else:
                    await bot.send_audio(chat_id, file_id, caption=caption, reply_markup=reply_markup)
                return True
            except Exception:
                pass

        path = await download_track(track, video=video)
        try:
            from aiogram.types import FSInputFile

            nice_name = safe_filename(
                f"{track.get('title', 'track')}", path.suffix.lstrip(".")
            )

            # Prefer the storage channel so the cached id outlives this chat.
            stored = await mirror_to_storage(bot, path, track, video)
            if stored:
                if video:
                    await bot.send_video(chat_id, stored, caption=caption, reply_markup=reply_markup)
                else:
                    await bot.send_audio(chat_id, stored, caption=caption, reply_markup=reply_markup)
                await remember_file_id(track, stored, video=video)
                return True

            file = FSInputFile(path, filename=nice_name)
            if video:
                sent = await bot.send_video(chat_id, file, caption=caption, reply_markup=reply_markup)
                new_id = sent.video.file_id if sent.video else ""
            else:
                sent = await bot.send_audio(
                    chat_id,
                    file,
                    caption=caption,
                    title=track.get("title"),
                    performer=track.get("artist") or None,
                    duration=track.get("duration") or None,
                    reply_markup=reply_markup,
                )
                new_id = sent.audio.file_id if sent.audio else ""
            if new_id:
                await remember_file_id(track, new_id, video=video)
            return True
        finally:
            await cleanup(path)
