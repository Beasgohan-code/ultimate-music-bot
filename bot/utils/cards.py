"""Rich card builders for the music side of the bot."""

from __future__ import annotations

from typing import Any

from bot.config import config
from bot.utils.rich import RichCard, a, b, c, i, plain


def _icon(icon: str):
    return plain(f"{icon} ")


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_duration(seconds: int | None) -> str:
    if not seconds or seconds < 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: int, duration: int | None, width: int = 14) -> str:
    if not duration:
        return "🔴 LIVE"
    ratio = max(0.0, min(1.0, elapsed / duration))
    filled = int(ratio * width)
    return "▬" * filled + "🔘" + "▬" * max(0, width - filled - 1)


def now_playing_card(
    track: dict[str, Any],
    *,
    elapsed: int = 0,
    queue_len: int = 0,
    volume: int = 100,
    loop_mode: str = "off",
    position: int | None = None,
) -> RichCard:
    """The main 'Now Playing' card."""
    title = track.get("title", "Unknown")
    artist = track.get("artist", "")
    duration = track.get("duration")
    is_live = bool(track.get("is_live"))
    is_video = bool(track.get("is_video"))

    mode = "📡 Live" if is_live else ("🎬 Video" if is_video else "🎵 Audio")

    card = RichCard().heading([_icon("▶️"), b("Now Playing")], size=1)

    if track.get("url"):
        card.para([b(a(title, track["url"]))])
    else:
        card.para([b(title)])
    if artist:
        card.para([i(artist)])

    if not is_live:
        card.para([plain(progress_bar(elapsed, duration))])
        card.para([c(fmt_duration(elapsed)), plain(" / "), c(fmt_duration(duration))])
    else:
        card.para([plain("🔴 "), b("LIVE")])

    rows: list[list[Any]] = [
        ["Mode", mode],
        ["Volume", c(f"{volume}%")],
        ["Loop", c(loop_mode)],
    ]
    if queue_len:
        rows.append(["Up next", c(f"{queue_len} track(s)")])
    if track.get("requester"):
        rows.append(["Requested by", track["requester"]])
    if position:
        rows.append(["Queue position", c(f"#{position}")])
    card.table(["Detail", "Value"], rows)

    source = track.get("source") or "youtube"
    card.footer(f"Source: {source}  •  {config.bot_name}")
    return card


def queued_card(track: dict[str, Any], position: int, queue_len: int) -> RichCard:
    card = (
        RichCard()
        .heading([_icon("➕"), b("Added to Queue")], size=1)
        .para([b(track.get("title", "Unknown"))])
    )
    if track.get("artist"):
        card.para([i(track["artist"])])
    card.table(
        ["Detail", "Value"],
        [
            ["Position", c(f"#{position}")],
            ["Duration", c(fmt_duration(track.get("duration")))],
            ["Queue length", c(str(queue_len))],
            ["Requested by", track.get("requester", "—")],
        ],
    )
    card.footer("Use /queue to see everything lined up.")
    return card


def queue_card(
    current: dict[str, Any] | None,
    tracks: list[dict[str, Any]],
    page: int = 0,
    per_page: int = 10,
) -> RichCard:
    card = RichCard().heading([_icon("📋"), b("Queue")], size=1)

    if current:
        card.quote(
            [
                [b("Now: "), plain(current.get("title", "Unknown"))],
                [plain(f"{current.get('artist', '')}  •  {fmt_duration(current.get('duration'))}")],
            ]
        )

    if not tracks:
        card.para([i("Nothing queued — add tracks with /play.")])
        return card

    total_pages = max(1, (len(tracks) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = tracks[page * per_page : (page + 1) * per_page]

    card.table(
        ["#", "Track", "Length"],
        [
            [
                c(str(page * per_page + idx + 1)),
                t.get("title", "Unknown")[:40],
                c(fmt_duration(t.get("duration"))),
            ]
            for idx, t in enumerate(chunk)
        ],
    )

    total_secs = sum(int(t.get("duration") or 0) for t in tracks)
    card.footer(
        f"Page {page + 1}/{total_pages}  •  {len(tracks)} track(s)  •  "
        f"{fmt_duration(total_secs)} total"
    )
    return card


def search_card(query: str, results: list[dict[str, Any]]) -> RichCard:
    card = (
        RichCard()
        .heading([_icon("🔍"), b("Search Results")], size=1)
        .para([plain("Query: "), c(query)])
    )
    card.table(
        ["#", "Title", "Length"],
        [
            [c(str(idx)), r.get("title", "Unknown")[:44], c(fmt_duration(r.get("duration")))]
            for idx, r in enumerate(results[:10], 1)
        ],
    )
    card.footer("Tap a button below to play or queue a result.")
    return card


def lyrics_card(title: str, artist: str, lyrics: str) -> RichCard:
    """Lyrics use an expandable blockquote so long songs stay tidy."""
    card = RichCard().heading([_icon("📝"), b("Lyrics")], size=1)
    card.para([b(title), plain(" — "), i(artist or "Unknown")])
    body = lyrics.strip()
    if len(body) > 3000:
        body = body[:3000].rsplit("\n", 1)[0] + "\n…"
    card.expandable(body)
    card.footer("Tap the quote to expand the full lyrics.")
    return card


def track_info_card(track: dict[str, Any], elapsed: int = 0) -> RichCard:
    card = RichCard().heading([_icon("ℹ️"), b("Track Info")], size=1)
    card.para([b(track.get("title", "Unknown"))])
    rows = [
        ["Artist", track.get("artist") or "—"],
        ["Duration", c(fmt_duration(track.get("duration")))],
        ["Elapsed", c(fmt_duration(elapsed))],
        ["Source", track.get("source", "youtube")],
        ["Requested by", track.get("requester", "—")],
        ["Type", "Live" if track.get("is_live") else ("Video" if track.get("is_video") else "Audio")],
    ]
    card.table(["Field", "Value"], rows)
    if track.get("url"):
        card.para([a("Open original", track["url"])])
    return card


def stats_card(
    stats: dict[str, Any],
    counters: dict[str, int],
    top: list[dict[str, Any]],
    backend: str,
) -> RichCard:
    card = (
        RichCard()
        .heading([_icon("📊"), b(f"{config.bot_name} Statistics")], size=1)
        .table(
            ["Metric", "Value"],
            [
                ["Uptime", c(str(stats.get("uptime", "—")))],
                ["Total plays", c(str(counters.get("total_plays", 0)))],
                ["Served chats", c(str(counters.get("served_chats", 0)))],
                ["Served users", c(str(counters.get("served_users", 0)))],
                ["Streams started", c(str(stats.get("streams", 0)))],
                ["Commands handled", c(str(stats.get("commands", 0)))],
                ["Storage backend", c(backend)],
            ],
        )
    )
    if top:
        card.para([b("Most played")])
        card.bullets(
            [f"{t.get('title', 'Unknown')[:44]} — {t.get('count', 0)} plays" for t in top[:5]],
            ordered=True,
        )
    card.footer("/top for the full leaderboard")
    return card


def error_card(message: str, hint: str = "") -> RichCard:
    card = RichCard().heading([_icon("❌"), b("Something went wrong")], size=1).para([plain(message)])
    if hint:
        card.footer(hint)
    return card


def success_card(message: str, hint: str = "") -> RichCard:
    card = RichCard().heading([_icon("✅"), b("Done")], size=1).para([plain(message)])
    if hint:
        card.footer(hint)
    return card


def welcome_card(
    bot_name: str,
    username: str = "",
    first_name: str = "there",
    user_username: str = "",
) -> RichCard:
    """The /start card — greeting, ASCII feature tree, then a collapsed extras list."""
    handle = f" (@{user_username})" if user_username else ""
    inline_hint = f"@{username} song name" if username else "inline mode"

    return (
        RichCard()
        .para([plain("👋 Hey, "), b(f"{first_name}{handle}"), plain(" ~ 🎶")])
        .blank()
        .para([plain("ɪ'ᴍ "), b(f"{bot_name}♡"), plain(", your ultimate music companion!")])
        .para(
            [
                plain("Send me "),
                c("/song [song name]"),
                plain(" to download tracks instantly. 🎧"),
            ]
        )
        .blank()
        .pre(
            "│     ✦ ┊ 🎵 ꜰᴇᴀᴛᴜʀᴇs:\n"
            "│╭────────────╯\n"
            "││• 🎶 Instant Song Downloading\n"
            "││• 🔊 Crystal Clear 320kbps HQ Audio\n"
            "││• 🎤 Synced Lyrics On Demand\n"
            "││• 📻 Live Radio & 24/7 Streams\n"
            "││• 🎬 Video Streaming In Voice Chats\n"
            "││• 📋 Smart Queue, Loop & Seek\n"
            "││• 💾 Save & Replay Your Playlists\n"
            "││• 🛡 Full Group Management Suite\n"
            "││• 🚫 No Ads, No Interruptions\n"
            "│╰─────────── · · ✦"
        )
        .blank()
        .details(
            [plain("✦ ┊ "), b("ᴍᴏʀᴇ ᴛʜɪɴɢs ɪ ᴄᴀɴ ᴅᴏ")],
            [
                "🎧  Stream from YouTube, Spotify, SoundCloud & Apple Music",
                "📢  Channel play — stream into a linked channel's voice chat",
                f"⚡  Inline mode — type {inline_hint} in any chat",
                "🎭  Mood playlists, smart suggestions & listening history",
                "🛡  Warns, mutes, locks, filters, notes & antiflood",
                "🌐  Multi-language: English, Español, हिन्दी, Русский",
            ],
        )
        .blank()
        .quote(
            [
                "Add me to a group, promote me to admin, start a voice chat,",
                [plain("then send "), c("/play <song>"), plain(" — that's the whole setup.")],
            ]
        )
        .footer("Tap ✦ Help for every command  •  ✦ Support if you get stuck")
    )


def voteskip_card(votes: int, needed: int, title: str) -> RichCard:
    """Progress card shown while a skip vote is open."""
    filled = "🟩" * votes
    empty = "⬜" * max(0, needed - votes)
    return (
        RichCard()
        .heading([_icon("🗳"), b("Vote to Skip")], size=1)
        .para([plain("Skipping "), b(_clip(title, 60)), plain("?")])
        .para([plain(filled + empty), plain(f"  {votes}/{needed}")])
        .para([i(f"{needed - votes} more vote(s) needed.")])
        .footer("Send /skip to add your vote  •  admins can skip instantly")
    )


def feature_card(section: str = "overview") -> RichCard:
    """Feature browser behind the ✨ Features button on /start."""
    if section == "music":
        return (
            RichCard()
            .heading([_icon("🎵"), b("Music Features")], size=1)
            .para([i("Everything the player can do.")])
            .table(
                ["Command", "What it does"],
                [
                    ["/play <song>", "Search & stream into the voice chat"],
                    ["/song <name>", "Download the track as an MP3"],
                    ["/vplay <song>", "Stream with video"],
                    ["/radio <url>", "24/7 live radio or stream"],
                    ["/seek 1:30", "Jump to a timestamp"],
                    ["/loop all", "Loop one track, the queue, or 1-10 times"],
                    ["/lyrics", "Synced lyrics for what's playing"],
                    ["/saveplaylist", "Save the queue and replay it later"],
                ],
            )
            .bullets(
                [
                    "Sources: YouTube, Spotify, SoundCloud, Apple Music, direct files",
                    "320kbps audio, up to 4K video",
                    "Queue up to 50 tracks with shuffle, move and skip-to",
                    "Per-chat volume, speed and quality settings",
                ]
            )
            .footer("Tip: reply to any audio file with /play to stream it.")
        )

    if section == "group":
        return (
            RichCard()
            .heading([_icon("🛡"), b("Group Management")], size=1)
            .para([i("A full moderation suite — no second bot needed.")])
            .table(
                ["Area", "Commands"],
                [
                    ["Warnings", "/warn /warns /warnlimit /warnmode"],
                    ["Restrict", "/ban /tban 2h /mute /tmute 30m /kick"],
                    ["Locks", "/lock url /locks /locktypes"],
                    ["Antiflood", "/setflood 10 /flood"],
                    ["Notes", "/save /get #note /notes"],
                    ["Filters", "/filter hello /filters"],
                    ["Greetings", "/setwelcome /setgoodbye"],
                    ["Cleanup", "/purge /del /pin"],
                ],
            )
            .bullets(
                [
                    "20 lock types including links, forwards, stickers and media",
                    "Blacklist words with wildcard support",
                    "Welcome messages with {first} {mention} {chatname} placeholders",
                    "Disable any command per chat with /disable",
                ]
            )
            .footer("Configure everything visually with /settings.")
        )

    if section == "power":
        return (
            RichCard()
            .heading([_icon("⚡"), b("Power User Features")], size=1)
            .bullets(
                [
                    "Inline mode — search and share tracks in any chat",
                    "Channel play — stream into a linked channel's voice chat",
                    "Saved playlists and favourites that persist across chats",
                    "Listening history and /top leaderboards per chat or global",
                    "Mood-based playlists: /mood chill, /mood workout",
                    "Auth users — let non-admins control the player with /auth",
                    "Four languages, switchable per chat with /lang",
                    "Live status page with uptime and health endpoints",
                ]
            )
            .quote(["Sudo tools: /broadcast, /gban, /maintenance, /logs, /sysinfo"])
            .footer("Owner-only commands are hidden unless you're in SUDO_USERS.")
        )

    return (
        RichCard()
        .heading([_icon("✨"), b("Features")], size=1)
        .para([i("Pick a category to see the details.")])
        .bullets(
            [
                [b("🎵 Music"), plain(" — streaming, downloads, queue, lyrics")],
                [b("🛡 Group"), plain(" — warns, locks, filters, greetings")],
                [b("⚡ Power user"), plain(" — inline mode, playlists, channel play")],
            ]
        )
        .divider()
        .checklist(
            [
                (True, "Free forever, no ads"),
                (True, "320kbps high quality audio"),
                (True, "Works in groups and channels"),
                (True, "Self-hostable and open source"),
            ]
        )
        .footer("Tap a category below, or ◂ Back to return to the start screen.")
    )
