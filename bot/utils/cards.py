"""Rich card builders for the music side of the bot."""

from __future__ import annotations

import re
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


#: Where a track came from, as a badge. Keyed by the lowercased extractor name
#: so both "Spotify" and "spotify:track" land on the same entry.
_SOURCES: dict[str, tuple[str, str]] = {
    "spotify": ("🟢", "Spotify"),
    "deezer": ("🟣", "Deezer"),
    "apple": ("🍎", "Apple Music"),
    "applemusic": ("🍎", "Apple Music"),
    "soundcloud": ("🟠", "SoundCloud"),
    "youtube": ("🔴", "YouTube"),
    "youtubemusic": ("🔴", "YouTube Music"),
    "bandcamp": ("🔵", "Bandcamp"),
    "mixcloud": ("🎛", "Mixcloud"),
    "audiomack": ("🟡", "Audiomack"),
    "twitch": ("🟪", "Twitch"),
    "vimeo": ("🎬", "Vimeo"),
    "telegram": ("📎", "File"),
    "radio": ("📻", "Radio"),
}


def source_badge(source: str) -> str:
    """Human label with a colour dot, e.g. "🟢 Spotify"."""
    key = re.sub(r"[^a-z]", "", str(source or "").lower())
    for name, (icon, label) in _SOURCES.items():
        if key.startswith(name):
            return f"{icon} {label}"
    return f"🎵 {source or 'Unknown'}"


def progress_bar(elapsed: int, duration: int | None, width: int = 14) -> str:
    """A seek bar that reads cleanly at mobile widths.

    Uses a continuous rule with a knob rather than repeated glyphs, so the bar
    keeps its shape when Telegram renders it in a proportional font.
    """
    if not duration:
        return "🔴 LIVE"
    ratio = max(0.0, min(1.0, elapsed / duration))
    # The knob occupies one cell, so the track either side totals width - 1.
    filled = round(ratio * (width - 1))
    return "━" * filled + "◉" + "─" * (width - 1 - filled)


def meter(value: int, total: int = 100, width: int = 10) -> str:
    """Small block meter for volume and similar 0-100 values."""
    ratio = max(0.0, min(1.0, (value or 0) / (total or 100)))
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


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

    # Title and artist lead, because that is what people actually look for.
    if track.get("url"):
        card.para([b(a(_clip(title, 72), track["url"]))])
    else:
        card.para([b(_clip(title, 72))])
    if artist:
        card.para([i(_clip(artist, 60))])

    card.blank()

    # The seek bar and timecode belong together, on one line, monospaced so
    # the digits do not jitter as the position updates.
    if not is_live:
        card.para(
            [
                c(fmt_duration(elapsed)),
                plain("  "),
                plain(progress_bar(elapsed, duration)),
                plain("  "),
                c(fmt_duration(duration)),
            ]
        )
    else:
        card.para([plain("🔴  "), b("LIVE"), plain("  ── streaming now")])

    # A compact status strip reads better than a six-row table for values
    # that are mostly one word each.
    strip: list[Any] = [plain(mode)]
    if not is_live:
        strip += [plain("   🔊 "), c(f"{volume}%")]
    if loop_mode and loop_mode != "off":
        strip += [plain("   🔁 "), c(loop_mode)]
    if queue_len:
        strip += [plain("   📋 "), c(str(queue_len))]
    card.para(strip)

    if track.get("requester") or position:
        line: list[Any] = []
        if track.get("requester"):
            line += [plain("🙋 "), plain(_clip(track["requester"], 32))]
        if position:
            if line:
                line.append(plain("   "))
            line += [plain("📍 #"), c(str(position))]
        card.para(line)

    card.footer(f"{source_badge(track.get('source') or 'youtube')}  •  {config.bot_name}")
    return card


def queued_card(track: dict[str, Any], position: int, queue_len: int) -> RichCard:
    card = (
        RichCard()
        .heading([_icon("➕"), b("Added to Queue")], size=1)
        .para([b(_clip(track.get("title", "Unknown"), 72))])
    )
    if track.get("artist"):
        card.para([i(_clip(track["artist"], 60))])

    card.blank()
    card.para(
        [
            plain("📍 Position "),
            c(f"#{position}"),
            plain("   ⏱ "),
            c(fmt_duration(track.get("duration"))),
            plain("   📋 "),
            c(f"{queue_len} in queue"),
        ]
    )
    if track.get("requester"):
        card.para([plain("🙋 "), plain(_clip(track["requester"], 32))])

    card.footer(f"{source_badge(track.get('source') or 'youtube')}  •  /queue to see it all")
    return card


def import_card(resolved: Any, added: int, queued: int = 0) -> RichCard:
    """Summary for a Spotify / Apple Music / Deezer link that expanded.

    These links carry a whole album or playlist, so the useful feedback is
    "here is what I found and how much of it I could add", not a single track.
    """
    icon = {"spotify": "🟢", "deezer": "🟣", "apple": "🍎"}.get(resolved.platform, "🎵")
    label = {"spotify": "Spotify", "deezer": "Deezer", "apple": "Apple Music"}.get(
        resolved.platform, resolved.platform.title()
    )

    card = RichCard().heading([_icon(icon), b(f"{label} {resolved.kind.title()}")], size=1)

    if resolved.url:
        card.para([b(a(_clip(resolved.title, 68), resolved.url))])
    else:
        card.para([b(_clip(resolved.title, 68))])
    if resolved.subtitle:
        card.para([i(_clip(resolved.subtitle, 60))])

    card.blank()

    total = len(resolved.tracks)
    if total > 1:
        preview = [
            f"{idx}. {_clip(t.get('artist', ''), 24)} — {_clip(t.get('title', ''), 38)}"
            if t.get("artist")
            else f"{idx}. {_clip(t.get('title', ''), 60)}"
            for idx, t in enumerate(resolved.tracks[:5], 1)
        ]
        card.bullets(preview)
        if total > 5:
            card.para([i(f"…and {total - 5} more")])
        card.blank()

    line: list[Any] = [plain("✅ Added "), c(str(added))]
    if queued:
        line += [plain("   📋 Queued "), c(str(queued))]
    if total > added:
        line += [plain("   ⚠️ Skipped "), c(str(total - added))]
    card.para(line)

    if resolved.truncated:
        card.para([i(f"Only the first {total} tracks were read from this link.")])

    card.footer(
        f"{icon} {label} audio is DRM-protected, so tracks are matched and streamed "
        "from YouTube."
    )
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

    # Two lines per entry rather than a table: Telegram renders a proportional
    # font, so space-padded columns never align on a phone. Title leads on its
    # own line, details sit underneath in a lighter style.
    for idx, t in enumerate(chunk):
        position = page * per_page + idx + 1
        meta = [fmt_duration(t.get("duration"))]
        artist = (t.get("artist") or "").strip()
        if artist:
            meta.insert(0, _clip(artist, 28))
        requester = (t.get("requester") or "").strip()
        if requester:
            meta.append(f"🙋 {_clip(requester, 16)}")
        card.para(
            [
                c(f"{position:>2}"),
                plain("  "),
                b(_clip(t.get("title", "Unknown"), 42)),
            ]
        )
        card.para([plain("     "), i("  •  ".join(meta))])

    total_secs = sum(int(t.get("duration") or 0) for t in tracks)
    parts = [f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}"]
    if total_secs:
        parts.append(f"{fmt_duration(total_secs)} total")
    if total_pages > 1:
        parts.append(f"page {page + 1}/{total_pages}")
    card.footer("  •  ".join(parts))
    return card


def search_card(query: str, results: list[dict[str, Any]]) -> RichCard:
    card = (
        RichCard()
        .heading([_icon("🔍"), b("Search Results")], size=1)
        .para([plain("Query: "), c(query)])
    )
    for idx, r in enumerate(results[:10], 1):
        meta = []
        artist = (r.get("artist") or "").strip()
        if artist:
            meta.append(_clip(artist, 28))
        meta.append(fmt_duration(r.get("duration")))
        source = (r.get("source") or "").strip()
        if source:
            meta.append(source_badge(source))
        card.para([c(f"{idx:>2}"), plain("  "), b(_clip(r.get("title", "Unknown"), 42))])
        card.para([plain("     "), i("  •  ".join(meta))])

    card.footer("Tap a number below to play it.")
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
    """A problem, stated once.

    The old version stacked a generic "Something went wrong" heading above the
    actual message, so every failure read as two lines of which only the
    second carried information. The message *is* the headline.
    """
    card = RichCard().para([plain("⚠️ "), b(message)])
    if hint:
        card.para([i(hint)])
    return card


def success_card(message: str, hint: str = "") -> RichCard:
    """Confirmation, without a redundant "Done" banner above it."""
    card = RichCard().para([plain("✅ "), b(message)])
    if hint:
        card.para([i(hint)])
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
                "🎧  Paste a Spotify, Apple Music or Deezer link — albums and playlists import",
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
                    "Sources: YouTube, Spotify, Apple Music, Deezer, SoundCloud, files",
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


# ─────────────────────────────────────────────────────────────────────────────
# Stream action cards
#
# FallenMusic answered every transport action with an attributed card:
#
#     ➻ sᴛʀᴇᴀᴍ ᴘᴀᴜsᴇᴅ 🥺
#     │
#     └ʙʏ : @someone 🥀
#
# Naming who acted matters in a busy group — "Paused" alone leaves everyone
# wondering whether the bot broke or a person did it. These rebuild that shape
# as real rich blocks instead of hand-assembled box-drawing characters.
# ─────────────────────────────────────────────────────────────────────────────

#: icon + label for each transport action
_ACTIONS = {
    "paused": ("⏸", "Stream Paused"),
    "resumed": ("▶️", "Stream Resumed"),
    "skipped": ("⏭", "Stream Skipped"),
    "ended": ("⏹", "Stream Ended"),
    "muted": ("🔇", "Stream Muted"),
    "unmuted": ("🔊", "Stream Unmuted"),
    "shuffled": ("🔀", "Queue Shuffled"),
    "cleared": ("🧹", "Queue Cleared"),
    "looped": ("🔁", "Loop Updated"),
    "seeked": ("⏩", "Position Changed"),
    "volume": ("🔊", "Volume Changed"),
}


def action_card(
    action: str,
    by: str = "",
    *,
    detail: str = "",
    note: str = "",
) -> RichCard:
    """An attributed transport-action card.

    ``action`` is a key from :data:`_ACTIONS` (unknown keys are title-cased so
    a new action never renders as a traceback).  ``by`` is the actor's display
    name, ``detail`` an inline extra ("50%", "track 3"), and ``note`` a full
    trailing line such as "Queue is empty — leaving the voice chat."
    """
    icon, label = _ACTIONS.get(action, ("🎧", action.replace("_", " ").title()))
    if detail:
        label = f"{label} — {detail}"

    card = RichCard().heading([_icon(icon), b(label)], size=1)
    if by:
        card.para([plain("by "), b(_clip(by, 48))])
    if note:
        card.para([i(note)])
    return card


def stream_started_card(track: dict[str, Any], *, video: bool = False) -> RichCard:
    """FallenMusic's "➻ sᴛᴀʀᴛᴇᴅ sᴛʀᴇᴀᴍɪɴɢ" card, as rich blocks."""
    kind = "Video" if video else "Audio"
    rows = [["Duration", fmt_duration(track.get("duration"))]]
    if track.get("artist"):
        rows.insert(0, ["Artist", _clip(track["artist"], 40)])
    if track.get("requester"):
        rows.append(["Requested by", _clip(track["requester"], 32)])
    rows.append(["Mode", kind])

    card = (
        RichCard()
        .heading([_icon("🎬" if video else "🎧"), b("Started Streaming")], size=1)
        .para([b(_clip(track.get("title", "Unknown"), 64))])
        .table(["Detail", "Value"], rows)
    )
    if track.get("is_live"):
        card.para([i("Live stream — it plays until the source stops.")])
    return card
