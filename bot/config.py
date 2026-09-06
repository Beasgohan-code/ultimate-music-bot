"""Configuration loader for Ultimate Music Bot.

Every value can be supplied through the environment or a `.env` file next to
the repository root.  Nothing here ever raises at import time — call
:meth:`Config.validate` to collect human readable problems instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or (ROOT_DIR / "data"))
DOWNLOAD_DIR = DATA_DIR / "downloads"
CACHE_DIR = DATA_DIR / "cache"

load_dotenv(ROOT_DIR / ".env")


def _parse_int_list(value: str) -> list[int]:
    """Parse ``"1, -100234 5"`` into ``[1, -100234, 5]`` (comma or space separated)."""
    if not value or not value.strip():
        return []
    cleaned = value.replace(",", " ")
    out: list[int] = []
    for chunk in cleaned.split():
        chunk = chunk.strip()
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def _parse_str_list(value: str, default: list[str] | None = None) -> list[str]:
    if not value or not value.strip():
        return list(default or [])
    cleaned = value.replace(",", " ")
    return [c for c in (chunk.strip() for chunk in cleaned.split()) if c]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enable", "enabled"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _clean(value: str | None) -> str:
    """Strip whitespace and stray quotes that people leave in .env files."""
    return (value or "").strip().strip("'\"").strip()


AUDIO_QUALITIES = ("studio", "high", "medium", "low")
VIDEO_QUALITIES = ("uhd_4k", "qhd_2k", "fhd_1080p", "hd_720p", "sd_480p", "sd_360p")
STREAM_MODES = ("audio", "video")


@dataclass(frozen=True)
class Config:
    # ── Core credentials ────────────────────────────────────────────────
    bot_token: str = field(default_factory=lambda: _clean(os.getenv("BOT_TOKEN")))
    api_id: int = field(default_factory=lambda: _env_int("API_ID", 0))
    api_hash: str = field(default_factory=lambda: _clean(os.getenv("API_HASH")))
    session_string: str = field(
        default_factory=lambda: _clean(os.getenv("SESSION_STRING") or os.getenv("STRING_SESSION"))
    )

    # ── Ownership & access control ──────────────────────────────────────
    owner_id: int = field(default_factory=lambda: _env_int("OWNER_ID", 0))
    sudo_users: list[int] = field(
        default_factory=lambda: _parse_int_list(os.getenv("SUDO_USERS") or os.getenv("SUDOERS") or "")
    )
    log_group_id: int = field(default_factory=lambda: _env_int("LOG_GROUP_ID", 0))
    storage_chat_id: int = field(default_factory=lambda: _env_int("STORAGE_CHAT_ID", 0))
    support_chat: str = field(default_factory=lambda: _clean(os.getenv("SUPPORT_CHAT")))
    support_channel: str = field(default_factory=lambda: _clean(os.getenv("SUPPORT_CHANNEL")))
    owner_username: str = field(
        default_factory=lambda: _clean(os.getenv("OWNER_USERNAME")).lstrip("@")
    )

    # ── Assistant userbot ───────────────────────────────────────────────
    assistant_username: str = field(
        default_factory=lambda: _clean(os.getenv("ASSISTANT_USERNAME")).lstrip("@")
    )
    auto_invite_assistant: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("AUTO_INVITE_ASSISTANT"), True)
    )

    # ── Persistence ─────────────────────────────────────────────────────
    mongo_uri: str = field(
        default_factory=lambda: _clean(os.getenv("MONGO_URI") or os.getenv("MONGO_DB_URI"))
    )
    mongo_db_name: str = field(
        default_factory=lambda: _clean(os.getenv("MONGO_DB_NAME")) or "ultimatemusic"
    )

    # ── Branding ────────────────────────────────────────────────────────
    bot_name: str = field(
        default_factory=lambda: _clean(os.getenv("BOT_NAME")) or "Ultimate Music Bot"
    )
    bot_username: str = field(default_factory=lambda: _clean(os.getenv("BOT_USERNAME")).lstrip("@"))
    start_image_url: str = field(default_factory=lambda: _clean(os.getenv("START_IMAGE_URL")))

    # ── Playback defaults ───────────────────────────────────────────────
    command_prefixes: list[str] = field(
        default_factory=lambda: _parse_str_list(os.getenv("COMMAND_PREFIXES") or "", ["/"])
    )
    default_language: str = field(
        default_factory=lambda: (_clean(os.getenv("LANGUAGE")) or "en").lower()
    )
    default_stream_mode: str = field(
        default_factory=lambda: (_clean(os.getenv("STREAM_MODE")) or "audio").lower()
    )
    audio_quality: str = field(
        default_factory=lambda: (_clean(os.getenv("QUALITY")) or _clean(os.getenv("AUDIO_QUALITY")) or "high").lower()
    )
    video_quality: str = field(
        default_factory=lambda: (_clean(os.getenv("VIDEO_QUALITY")) or "hd_720p").lower()
    )
    default_volume: int = field(default_factory=lambda: _env_int("DEFAULT_VOLUME", 100))
    max_queue_size: int = field(default_factory=lambda: _env_int("MAX_QUEUE_SIZE", 50))
    max_playlist_size: int = field(default_factory=lambda: _env_int("MAX_PLAYLIST_SIZE", 100))
    duration_limit_min: int = field(default_factory=lambda: _env_int("DURATION_LIMIT", 180))
    video_limit_min: int = field(default_factory=lambda: _env_int("VIDEO_LIMIT", 60))
    default_tz_offset_min: int = field(
        default_factory=lambda: _env_int("DEFAULT_TZ_OFFSET_MIN", 0)
    )
    voteskip_ratio: float = field(
        default_factory=lambda: _env_float("VOTESKIP_RATIO", 0.5)
    )
    admins_only: bool = field(default_factory=lambda: _parse_bool(os.getenv("ADMINS_ONLY"), False))

    # ── Housekeeping ────────────────────────────────────────────────────
    auto_leave_idle: int = field(default_factory=lambda: _env_int("AUTO_LEAVE_IDLE", 300))
    auto_end_empty_vc: int = field(default_factory=lambda: _env_int("AUTO_END_EMPTY_VC", 120))
    clean_mode_seconds: int = field(default_factory=lambda: _env_int("CLEAN_MODE_SECONDS", 300))
    throttle_seconds: float = field(
        default_factory=lambda: _env_float(
            "THROTTLE_SECONDS", float(_env_int("THROTTLE_MS", 700)) / 1000.0
        )
    )

    # ── Downloads ───────────────────────────────────────────────────────
    max_download_mb: int = field(default_factory=lambda: _env_int("MAX_DOWNLOAD_MB", 48))
    audio_bitrate: str = field(default_factory=lambda: _clean(os.getenv("AUDIO_BITRATE")) or "320")
    enable_downloads: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("ENABLE_DOWNLOADS"), True)
    )
    cookies_file: str = field(default_factory=lambda: _clean(os.getenv("COOKIES_FILE")))
    # Cookie jar contents inline, for hosts with no writable repo to commit a
    # file into. Accepts raw Netscape text or base64 of it; written to disk at
    # startup and used as COOKIES_FILE.
    cookies_data: str = field(default_factory=lambda: (os.getenv("COOKIES_DATA") or "").strip())
    # Directory of extra cookies*.txt jars. Rotating across several accounts
    # stops any one of them being rate-limited on its own.
    cookies_dir: str = field(default_factory=lambda: _clean(os.getenv("COOKIES_DIR")))
    # Browser to impersonate at the TLS layer ("firefox", "chrome", "off").
    # Blank autodetects. Requires curl_cffi.
    impersonate: str = field(default_factory=lambda: _clean(os.getenv("IMPERSONATE")))
    # Hard ceiling on a single yt-dlp extraction. Beyond this the user is
    # told plainly rather than left watching "Loading media…".
    extract_timeout: int = field(default_factory=lambda: _env_int("EXTRACT_TIMEOUT", 45))
    ytdlp_proxy: str = field(default_factory=lambda: _clean(os.getenv("YTDLP_PROXY")))
    # JS runtime for yt-dlp's YouTube challenge solver. Blank = autodetect
    # (deno, node, bun, quickjs); "none" disables. Without one, yt-dlp tries a
    # single player client and gives up, which reads as "no results".
    js_runtime: str = field(default_factory=lambda: _clean(os.getenv("YTDLP_JS_RUNTIME")))
    # Comma-separated YouTube player clients. Blank uses yt-dlp's defaults plus
    # extra fallbacks; each client is an independent chance past an IP block.
    youtube_clients: str = field(default_factory=lambda: _clean(os.getenv("YOUTUBE_CLIENTS")))
    # Ordered search backends, e.g. "youtube,soundcloud". Blank tries YouTube
    # first then SoundCloud, so a YouTube IP block does not kill every search.
    search_backends: str = field(default_factory=lambda: _clean(os.getenv("SEARCH_BACKENDS")))

    # ── Third-party APIs ────────────────────────────────────────────────
    spotify_client_id: str = field(default_factory=lambda: _clean(os.getenv("SPOTIFY_CLIENT_ID")))
    spotify_client_secret: str = field(
        default_factory=lambda: _clean(os.getenv("SPOTIFY_CLIENT_SECRET"))
    )
    # Storefront for artist top-tracks; also affects which regional catalogue
    # a track resolves to.
    spotify_market: str = field(
        default_factory=lambda: _clean(os.getenv("SPOTIFY_MARKET")) or "US"
    )
    genius_api_token: str = field(default_factory=lambda: _clean(os.getenv("GENIUS_API_TOKEN")))

    # ── Web dashboard / health server ───────────────────────────────────
    web_enabled: bool = field(default_factory=lambda: _parse_bool(os.getenv("WEB_ENABLED"), True))
    web_host: str = field(default_factory=lambda: _clean(os.getenv("WEB_HOST")) or "0.0.0.0")
    web_port: int = field(default_factory=lambda: _env_int("PORT", _env_int("WEB_PORT", 8080)))

    # ── Derived helpers ─────────────────────────────────────────────────
    @property
    def spotify_enabled(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @property
    def mongo_enabled(self) -> bool:
        return bool(self.mongo_uri)

    @property
    def owners(self) -> list[int]:
        """Sudo users plus the explicit owner id, de-duplicated, owner first."""
        out: list[int] = []
        if self.owner_id:
            out.append(self.owner_id)
        for uid in self.sudo_users:
            if uid not in out:
                out.append(uid)
        return out

    def validate(self) -> list[str]:
        """Return a list of fatal configuration problems (empty == good to go)."""
        errors: list[str] = []
        if not self.bot_token:
            errors.append("BOT_TOKEN is required — get one from @BotFather")
        elif ":" not in self.bot_token:
            errors.append("BOT_TOKEN looks malformed (expected the 123456:ABC-DEF form)")
        if not self.api_id:
            errors.append("API_ID is required — create an app at https://my.telegram.org")
        if not self.api_hash:
            errors.append("API_HASH is required — create an app at https://my.telegram.org")
        if not self.session_string:
            errors.append("SESSION_STRING is required — run `python session_generator.py`")
        return errors

    def warnings(self) -> list[str]:
        """Non-fatal configuration advice surfaced in the startup banner."""
        warns: list[str] = []
        if not self.owners:
            warns.append("No SUDO_USERS/OWNER_ID set — admin commands will be unavailable.")
        if not self.assistant_username:
            warns.append("ASSISTANT_USERNAME not set — the bot cannot tell users who to invite.")
        if not self.mongo_enabled:
            warns.append("MONGO_URI not set — falling back to local JSON storage in ./data.")
        if not self.spotify_enabled:
            warns.append("Spotify credentials missing — Spotify links use public metadata only.")
        if self.audio_quality not in AUDIO_QUALITIES:
            warns.append(f"Unknown QUALITY={self.audio_quality!r}, falling back to 'high'.")
        if self.video_quality not in VIDEO_QUALITIES:
            warns.append(f"Unknown VIDEO_QUALITY={self.video_quality!r}, falling back to 'hd_720p'.")
        if self.default_stream_mode not in STREAM_MODES:
            warns.append(f"Unknown STREAM_MODE={self.default_stream_mode!r}, falling back to 'audio'.")
        if not self.log_group_id:
            warns.append("LOG_GROUP_ID not set — bot activity will not be mirrored to a log chat.")
        return warns

    def ensure_dirs(self) -> None:
        for path in (DATA_DIR, DOWNLOAD_DIR, CACHE_DIR):
            path.mkdir(parents=True, exist_ok=True)


config = Config()
