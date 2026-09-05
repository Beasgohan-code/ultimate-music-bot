"""Premium HTML message formatters with blockquotes and styled text."""

from __future__ import annotations

import html
from typing import Any


def esc(text: str | None) -> str:
    return html.escape(str(text or ""), quote=False)


def bq(text: str) -> str:
    """Wrap text in a Telegram HTML blockquote."""
    return f"<blockquote>{esc(text)}</blockquote>"


def bold(text: str) -> str:
    return f"<b>{esc(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{esc(text)}</i>"


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def link(text: str, url: str) -> str:
    return f'<a href="{esc(url)}">{esc(text)}</a>'


def spoiler(text: str) -> str:
    return f"<tg-spoiler>{esc(text)}</tg-spoiler>"


def underline(text: str) -> str:
    return f"<u>{esc(text)}</u>"


def format_duration(seconds: int | None) -> str:
    if not seconds or seconds < 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_track(
    title: str,
    artist: str = "",
    duration: int | None = None,
    requester: str = "",
    position: int | None = None,
) -> str:
    lines = [
        f"🎵 {bold(title)}",
    ]
    if artist:
        lines.append(f"👤 {italic(artist)}")
    if duration:
        lines.append(f"⏱ {code(format_duration(duration))}")
    if requester:
        lines.append(f"🙋 {esc(requester)}")
    if position is not None:
        lines.append(f"📍 Queue #{position}")
    return "\n".join(lines)


def welcome_message(bot_name: str = "Ultimate Music Bot") -> str:
    return (
        f"✨ {bold(bot_name)}\n\n"
        f"{bq('Premium Telegram music & video streaming — voice chats, live streams, lyrics, radio, moods, and smart suggestions.')}\n\n"
        f"🎧 {bold('Audio')} — YouTube, Spotify, SoundCloud, files\n"
        f"🎬 {bold('Video')} — MKV, MP4, live streams\n"
        f"📻 {bold('Radio')} — 10+ built-in stations\n"
        f"🎭 {bold('Moods')} — Chill, party, workout & more\n"
        f"📝 {bold('Lyrics')} — instant song lyrics\n"
        f"💡 {bold('Suggestions')} — smart picks\n"
        f"⭐ {bold('Favorites')} — save tracks you love\n"
        f"🖥 {bold('OS Dashboard')} — /os for full control\n\n"
        f"{italic('Add me to a group, promote me, then use /play or tap the buttons below.')}"
    )


def now_playing_card(
    title: str,
    artist: str = "",
    duration: int | None = None,
    requester: str = "",
    is_video: bool = False,
    is_live: bool = False,
    loop_mode: str = "off",
    volume: int = 100,
) -> str:
    mode = "🎬 Video" if is_video else "🎵 Audio"
    if is_live:
        mode = "📡 Live Stream"

    body = format_track(title, artist, duration, requester)
    stats = (
        f"\n\n{mode}  •  🔊 {volume}%  •  🔁 {loop_mode.title()}"
    )
    return (
        f"▶️ {bold('Now Playing')}\n\n"
        f"{bq(body)}{stats}"
    )


def queue_card(tracks: list[dict[str, Any]], page: int = 0, per_page: int = 8) -> str:
    if not tracks:
        return f"📋 {bold('Queue')}\n\n{bq('Queue is empty. Use /song or /play to add tracks.')}"

    start = page * per_page
    chunk = tracks[start : start + per_page]
    lines = [f"📋 {bold('Queue')} — {len(tracks)} track(s)\n"]
    for i, t in enumerate(chunk, start=start + 1):
        dur = format_duration(t.get("duration"))
        lines.append(f"{i}. {esc(t.get('title', 'Unknown'))} {code(dur)}")
    if len(tracks) > start + per_page:
        lines.append(f"\n{italic(f'… and {len(tracks) - start - per_page} more')}")
    return "\n".join(lines)


def lyrics_card(title: str, artist: str, lyrics: str) -> str:
    preview = lyrics[:3500]
    if len(lyrics) > 3500:
        preview += "\n\n…"
    return (
        f"📝 {bold('Lyrics')}\n"
        f"{italic(f'{title} — {artist}')}\n\n"
        f"{bq(preview)}"
    )


def search_results_card(query: str, results: list[dict[str, Any]]) -> str:
    lines = [
        f"🔍 {bold('Search Results')}",
        f"{italic(f'Query: {esc(query)}')}\n",
    ]
    for i, r in enumerate(results[:10], 1):
        dur = format_duration(r.get("duration"))
        lines.append(f"{i}. {esc(r.get('title', '?'))} {code(dur)}")
    lines.append(f"\n{italic('Tap a button below to play or add to queue.')}")
    return "\n".join(lines)


def suggestions_card(seed: str, suggestions: list[dict[str, Any]]) -> str:
    lines = [
        f"💡 {bold('Suggested For You')}",
        f"{italic(f'Based on: {esc(seed)}')}\n",
    ]
    for i, s in enumerate(suggestions[:8], 1):
        dur = format_duration(s.get("duration"))
        lines.append(f"{i}. {esc(s.get('title', '?'))} {code(dur)}")
    return "\n".join(lines)


def error_card(message: str) -> str:
    return f"❌ {bold('Error')}\n\n{bq(esc(message))}"


def success_card(message: str) -> str:
    return f"✅ {bold('Success')}\n\n{bq(esc(message))}"


def help_card() -> str:
    return (
        f"📖 {bold('Command Reference')}\n\n"
        f"{bq('Playback')}\n"
        f"/play — Play audio in voice chat\n"
        f"/song — Search & play a song\n"
        f"/cplay — Channel/group play\n"
        f"/vplay — Stream video (MKV/MP4)\n"
        f"/vstream — Live stream (m3u8/YouTube Live)\n"
        f"/playlist — Load YouTube playlist\n"
        f"/playnow — Force play immediately\n"
        f"/playnext — Add to front of queue\n"
        f"/search — Interactive search picker\n"
        f"/radio — Internet radio stations\n"
        f"/mood — Mood-based playlists\n\n"
        f"{bq('Controls')}\n"
        f"/pause /resume /skip /stop\n"
        f"/queue /shuffle /loop /clear\n"
        f"/remove — Remove from queue\n"
        f"/volume — Set volume (1–200)\n\n"
        f"{bq('Extras')}\n"
        f"/lyrics — Get song lyrics\n"
        f"/suggest — Song suggestions\n"
        f"/fav /favs /unfav — Favorites\n"
        f"/download — Download as MP3\n"
        f"/history — Recently played\n"
        f"/now — Current track info\n"
        f"/panel — Control panel\n"
        f"/os — Premium OS dashboard\n"
        f"/stats — Bot statistics\n"
        f"/ping — Latency check\n"
        f"/join — Add assistant guide\n"
        f"/info — Current track details\n"
        f"/source — Supported platforms\n"
        f"/speed — Playback speed (0.5–2x)\n"
        f"/active — Playback status\n"
        f"/id — Get chat & user IDs\n"
        f"/help — This menu"
    )


def radio_card() -> str:
    from bot.services.radio import list_stations

    lines = [f"📻 {bold('Internet Radio')}", ""]
    for key, s in list_stations():
        lines.append(f"{s['emoji']} {bold(s['name'])} — {code('/radio ' + key)}")
    lines.append(f"\n{italic('Tap a station below or use /radio <name>')}")
    return "\n".join(lines)


def mood_card() -> str:
    from bot.services.music import MOOD_QUERIES

    lines = [f"🎭 {bold('Mood Playlists')}", ""]
    for mood in MOOD_QUERIES:
        lines.append(f"• {bold(mood.title())} — {code('/mood ' + mood)}")
    lines.append(f"\n{italic('Pick a mood below or use /mood <name>')}")
    return "\n".join(lines)


def favorites_card(favs: list[dict[str, Any]]) -> str:
    if not favs:
        return f"⭐ {bold('Favorites')}\n\n{bq('No favorites yet. Use /fav while a song is playing.')}"
    lines = [f"⭐ {bold('Your Favorites')} — {len(favs)} track(s)\n"]
    for i, f in enumerate(favs[:15], 1):
        dur = format_duration(f.get("duration"))
        lines.append(f"{i}. {esc(f.get('title', '?'))} {code(dur)}")
    return "\n".join(lines)


def history_card(history: list[dict[str, Any]]) -> str:
    if not history:
        return f"🕐 {bold('History')}\n\n{bq('No tracks played yet.')}"
    lines = [f"🕐 {bold('Recently Played')}\n"]
    for i, h in enumerate(history, 1):
        lines.append(f"{i}. {esc(h.get('title', '?'))} — {italic(h.get('requester', ''))}")
    return "\n".join(lines)


def stats_card(stats: dict[str, Any], recent: list[dict[str, Any]]) -> str:
    lines = [
        f"📊 {bold('Bot Statistics')}\n",
        f"⏱ Uptime: {code(str(stats.get('uptime', '—')))}",
        f"🎵 Total plays: {code(str(stats.get('total_plays', 0)))}",
        f"📡 Streams started: {code(str(stats.get('streams', 0)))}",
        f"💬 Active chats: {code(str(stats.get('active_chats', 0)))}",
        f"⚡ Commands handled: {code(str(stats.get('commands', 0)))}",
        f"❌ Errors: {code(str(stats.get('errors', 0)))}",
    ]
    if recent:
        lines.append(f"\n{bq('Recent Global Plays')}")
        for h in recent[:5]:
            lines.append(f"• {esc(h.get('title', '?'))}")
    return "\n".join(lines)


def os_dashboard_card(
    current: dict[str, Any] | None,
    queue_len: int,
    is_playing: bool,
    is_paused: bool,
    loop_mode: str,
    volume: int,
    stats: dict[str, Any],
    recent: list[dict[str, Any]],
) -> str:
    status = "⏸ Paused" if is_paused else ("▶️ Playing" if is_playing else "⏹ Idle")

    now_block = "Nothing playing"
    if current:
        now_block = (
            f"{current.get('title', 'Unknown')}\n"
            f"{current.get('artist', '')} • {format_duration(current.get('duration'))}"
        )

    recent_lines = "\n".join(f"• {h.get('title', '?')}" for h in recent[:3]) or "—"

    summary = (
        f"Status: {status}\n"
        f"Queue: {queue_len} tracks\n"
        f"Loop: {loop_mode} • Vol: {volume}%"
    )
    return (
        f"🖥 {bold('Music OS Dashboard')}\n\n"
        f"{bq(summary)}\n\n"
        f"▶️ {bold('Now Playing')}\n"
        f"{bq(now_block)}\n\n"
        f"🕐 {bold('Recent')}\n"
        f"{bq(recent_lines)}\n\n"
        f"📊 {code(stats.get('uptime', '—'))}  •  "
        f"🎵 {code(str(stats.get('total_plays', 0)))} plays  •  "
        f"⚡ {code(str(stats.get('commands', 0)))} cmds"
    )
