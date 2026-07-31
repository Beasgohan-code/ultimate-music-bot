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
        f"{bq('Premium Telegram music & video streaming — voice chats, live streams, lyrics, and smart suggestions.')}\n\n"
        f"🎧 {bold('Audio')} — YouTube, search, URLs, files\n"
        f"🎬 {bold('Video')} — MKV, MP4, live streams\n"
        f"📻 {bold('Live')} — m3u8, YouTube Live\n"
        f"📝 {bold('Lyrics')} — instant song lyrics\n"
        f"💡 {bold('Suggestions')} — AI-powered picks\n\n"
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
        f"/search — Interactive search picker\n\n"
        f"{bq('Controls')}\n"
        f"/pause /resume /skip /stop\n"
        f"/queue /shuffle /loop /clear\n"
        f"/volume — Set volume (1–200)\n\n"
        f"{bq('Extras')}\n"
        f"/lyrics — Get song lyrics\n"
        f"/suggest — Song suggestions\n"
        f"/now — Current track info\n"
        f"/panel — Open control panel\n"
        f"/help — This menu"
    )
