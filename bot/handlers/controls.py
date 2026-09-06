"""Extended playback controls: seek, mute, loop counts, skip-to, move, top."""

from __future__ import annotations

import logging
import re

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config
from bot.services.database import database
from bot.services.queue import LoopMode, queue_manager
from bot.services.stream import stream_manager
from bot.utils.guards import is_admin_or_auth, is_group
from bot.utils.rich import RichCard, b, c, i, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="controls")


def _icon(icon: str):
    return plain(f"{icon} ")


def fmt_time(seconds: int | None) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: int, duration: int | None, width: int = 14) -> str:
    if not duration:
        return "🔴 LIVE"
    ratio = max(0.0, min(1.0, elapsed / duration))
    filled = int(ratio * width)
    return "▬" * filled + "🔘" + "▬" * (width - filled - 1)


async def _can_control(message: Message, bot: Bot) -> bool:
    """Respect the per-chat 'controls: admins only' setting."""
    if not is_group(message) or not message.from_user:
        return True
    admins_only = bool(
        await database.get_chat_value(message.chat.id, "control_admins_only", True)
    )
    if not admins_only:
        return True
    if await is_admin_or_auth(bot, message.chat.id, message.from_user.id):
        return True
    await send_html(message, "🚫 <b>Only admins can control playback here.</b>")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Seeking
# ─────────────────────────────────────────────────────────────────────────────

def _parse_seek(raw: str) -> int | None:
    """Accept ``90``, ``1:30`` or ``1m30s``."""
    raw = raw.strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if ":" in raw:
        parts = raw.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        total = 0
        for n in nums:
            total = total * 60 + n
        return total
    total, num = 0, ""
    for ch in raw:
        if ch.isdigit():
            num += ch
        elif ch in "hms" and num:
            total += int(num) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
        else:
            return None
    return total + (int(num) if num else 0)


@router.message(Command("seek", "seekback"))
async def cmd_seek(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    chat_id = message.chat.id
    if not stream_manager.is_playing(chat_id):
        await send_html(message, "⚠️ <b>Nothing is playing.</b>")
        return

    current = await queue_manager.get_current(chat_id)
    if current and current.get("is_live"):
        await send_html(message, "⚠️ <b>You can't seek inside a live stream.</b>")
        return

    args = (message.text or "").split()[1:]
    if not args:
        await send_html(
            message,
            "⚠️ <b>Usage:</b> <code>/seek 90</code>, <code>/seek 1:30</code> "
            "or <code>/seekback 30</code>",
        )
        return

    delta = _parse_seek(args[0])
    if delta is None:
        await send_html(message, "⚠️ <b>I couldn't read that timestamp.</b>")
        return

    back = (message.text or "").lstrip("/").lower().startswith("seekback")
    try:
        position = await stream_manager.seek_relative(chat_id, -delta if back else delta)
    except Exception as exc:
        await send_html(message, f"❌ <b>Seek failed:</b> <code>{exc}</code>")
        return
    if position is None:
        await send_html(message, "⚠️ <b>Seeking isn't available for this track.</b>")
        return

    duration = (current or {}).get("duration")
    card = (
        RichCard()
        .heading([_icon("⏩" if not back else "⏪"), b("Seeked")], size=1)
        .para([c(fmt_time(position)), plain(" / "), c(fmt_time(duration))])
        .para([plain(progress_bar(position, duration))])
    )
    await send_card(message, card)


@router.message(Command("position", "elapsed"))
async def cmd_position(message: Message) -> None:
    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    if not current:
        await send_html(message, "⚠️ <b>Nothing is playing.</b>")
        return
    elapsed = stream_manager.elapsed(chat_id)
    duration = current.get("duration")
    card = (
        RichCard()
        .heading([_icon("▶️"), b(current.get("title", "Now Playing"))], size=1)
        .para([plain(progress_bar(elapsed, duration))])
        .para([c(fmt_time(elapsed)), plain(" / "), c(fmt_time(duration))])
    )
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Mute / unmute the assistant
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("mutevc", "vcmute"))
async def cmd_mute_vc(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    if not stream_manager.is_playing(message.chat.id):
        await send_html(message, "⚠️ <b>Nothing is playing.</b>")
        return
    try:
        await stream_manager.mute(message.chat.id)
        await send_html(message, "🔇 <b>Stream muted.</b> Use /unmutevc to restore.")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


@router.message(Command("unmutevc", "vcunmute"))
async def cmd_unmute_vc(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    try:
        await stream_manager.unmute(message.chat.id)
        await send_html(message, "🔊 <b>Stream unmuted.</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Loop with counts
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("loop", "repeat"))
async def cmd_loop(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    if not args:
        mode = await queue_manager.toggle_loop(chat_id)
        await send_html(message, f"🔁 <b>Loop mode:</b> <code>{mode.value}</code>")
        return

    arg = args[0].lower()
    if arg in ("off", "disable", "no", "0"):
        await queue_manager.set_loop(chat_id, LoopMode.OFF)
        await send_html(message, "🔁 <b>Looping disabled.</b>")
    elif arg in ("on", "enable", "single", "one", "current"):
        await queue_manager.set_loop(chat_id, LoopMode.SINGLE)
        await send_html(message, "🔂 <b>Looping the current track.</b>")
    elif arg in ("all", "queue"):
        await queue_manager.set_loop(chat_id, LoopMode.ALL)
        await send_html(message, "🔁 <b>Looping the whole queue.</b>")
    elif arg.isdigit():
        count = await queue_manager.set_loop_count(chat_id, int(arg))
        if count:
            await send_html(message, f"🔂 <b>Will repeat this track <code>{count}</code> more time(s).</b>")
        else:
            await send_html(message, "🔁 <b>Looping disabled.</b>")
    else:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/loop off|single|all|1-10</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Skip to position / move / remove
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("skipto", "jump"))
async def cmd_skipto(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    args = (message.text or "").split()[1:]
    if not args or not args[0].isdigit():
        await send_html(message, "⚠️ <b>Usage:</b> <code>/skipto 3</code>")
        return
    position = int(args[0])
    track = await stream_manager.skip(message.chat.id, to=position)
    if track:
        await send_html(message, f"⏭ <b>Now playing:</b> {track.get('title', 'Unknown')}")
    else:
        await send_html(message, "⏹ <b>Queue finished.</b>")


@router.message(Command("move"))
async def cmd_move(message: Message, bot: Bot) -> None:
    if not await _can_control(message, bot):
        return
    args = (message.text or "").split()[1:]
    if len(args) < 2 or not all(a.isdigit() for a in args[:2]):
        await send_html(message, "⚠️ <b>Usage:</b> <code>/move 5 1</code> (from → to)")
        return
    ok = await queue_manager.move(message.chat.id, int(args[0]) - 1, int(args[1]) - 1)
    await send_html(
        message,
        f"↕️ <b>Moved track {args[0]} → position {args[1]}.</b>" if ok
        else "❌ <b>Invalid queue positions.</b>",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Top tracks
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("top", "toptracks"))
async def cmd_top(message: Message) -> None:
    from bot.services.moderation import moderation

    if is_group(message) and await moderation.is_command_disabled(message.chat.id, "top"):
        return
    args = (message.text or "").split()[1:]
    scope_global = bool(args and args[0].lower() in ("global", "all"))
    chat_id = None if scope_global else message.chat.id
    tracks = await database.top_tracks(chat_id, limit=10)

    if not tracks:
        await send_html(message, "📊 <b>No plays recorded yet — start with /play!</b>")
        return

    card = (
        RichCard()
        .heading(
            [_icon("🏆"), b("Top Tracks — " + ("Global" if scope_global else "This Chat"))],
            size=1,
        )
        .table(
            ["#", "Track", "Plays"],
            [
                [c(str(idx)), t.get("title", "Unknown")[:44], c(str(t.get("count", 0)))]
                for idx, t in enumerate(tracks, 1)
            ],
        )
        .footer("/top global for the bot-wide leaderboard")
    )
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Saved playlists
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("saveplaylist"))
async def cmd_saveplaylist(message: Message) -> None:
    if not message.from_user:
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/saveplaylist My Mix</code>")
        return

    chat_id = message.chat.id
    current = await queue_manager.get_current(chat_id)
    tracks = ([current] if current else []) + await queue_manager.get_queue(chat_id)
    if not tracks:
        await send_html(message, "⚠️ <b>Nothing in the queue to save.</b>")
        return

    count = await database.save_playlist(
        message.from_user.id, args[1].strip(), tracks, limit=config.max_playlist_size
    )
    await send_html(
        message,
        f"💾 <b>Saved “{args[1].strip()}”</b> with <code>{count}</code> track(s).\n"
        f"Play it later with <code>/playlists</code>.",
    )


@router.message(Command("playlists", "myplaylists"))
async def cmd_playlists(message: Message) -> None:
    if not message.from_user:
        return
    lists = await database.get_playlists(message.from_user.id)
    if not lists:
        await send_html(
            message,
            "💾 <b>No saved playlists.</b>\nQueue some tracks then use <code>/saveplaylist Name</code>.",
        )
        return

    card = (
        RichCard()
        .heading([_icon("💾"), b("Your Playlists")], size=1)
        .table(
            ["Name", "Tracks"],
            [[name, c(str(len(tracks)))] for name, tracks in lists.items()],
        )
    )
    for name, tracks in list(lists.items())[:4]:
        card.details(
            f"{name} — {len(tracks)} tracks",
            [f"{idx}. {t.get('title', 'Unknown')}" for idx, t in enumerate(tracks[:20], 1)],
        )
    card.footer("/playplaylist <name> to queue one • /delplaylist <name> to remove")
    await send_card(message, card)


@router.message(Command("delplaylist", "deleteplaylist"))
async def cmd_delplaylist(message: Message) -> None:
    if not message.from_user:
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/delplaylist Name</code>")
        return
    ok = await database.delete_playlist(message.from_user.id, args[1].strip())
    await send_html(message, "🗑 <b>Playlist deleted.</b>" if ok else "❌ <b>No playlist by that name.</b>")


@router.message(Command("playplaylist", "loadplaylist"))
async def cmd_playplaylist(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/playplaylist Name</code>")
        return

    lists = await database.get_playlists(message.from_user.id)
    name = args[1].strip()
    tracks = lists.get(name)
    if tracks is None:  # case-insensitive retry
        for key, value in lists.items():
            if key.lower() == name.lower():
                tracks, name = value, key
                break
    if not tracks:
        await send_html(message, f"❌ <b>No playlist named “{name}”.</b>")
        return

    status = await message.answer(
        f"💾 <b>Loading “{name}” — {len(tracks)} tracks…</b>", parse_mode="HTML"
    )

    from bot.services.music import get_stream_url
    from bot.utils.play_helpers import play_track

    first = tracks[0]
    resolved = await get_stream_url(first.get("url") or first.get("title", ""))
    if not resolved:
        await status.edit_text("❌ <b>Could not load the first track.</b>", parse_mode="HTML")
        return

    await play_track(message, resolved, edit_msg=status)
    queued = await queue_manager.add_many(message.chat.id, tracks[1:])
    if queued:
        await send_html(message, f"➕ <b>Queued {queued} more track(s) from “{name}”.</b>")


@router.message(Command("voteskip", "vs"))
async def cmd_voteskip_config(message: Message) -> None:
    """Show or configure vote-skip for this chat."""
    from bot.services.voteskip import count_listeners, voteskip
    from bot.utils.cards import error_card, success_card

    if not is_group(message):
        await send_card(message, error_card("Vote-skip only applies to groups.", "In a private chat, just use /skip."))
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    if not args:
        enabled = await voteskip.enabled(chat_id)
        ratio = await voteskip.ratio(chat_id)
        listeners = await count_listeners(message.bot, chat_id)
        card = (
            RichCard()
            .heading([plain("🗳 "), b("Vote Skip")], size=1)
            .table(
                ["Setting", "Value"],
                [
                    ["Status", "enabled" if enabled else "disabled"],
                    ["Threshold", f"{int(ratio * 100)}% of listeners"],
                    ["Listeners now", str(listeners)],
                    ["Votes needed", str(voteskip.needed(listeners, ratio))],
                ],
            )
            .para([i("Admins and the track's requester always skip instantly.")])
            .footer("/voteskip on|off  •  /voteskip 60  (percent)")
        )
        await send_card(message, card)
        return

    if not await _can_control(message, message.bot):
        return

    arg = args[0].lower()
    if arg in {"on", "enable", "yes"}:
        await voteskip.set_enabled(chat_id, True)
        await send_card(message, success_card("Vote-skip enabled."))
        return
    if arg in {"off", "disable", "no"}:
        await voteskip.set_enabled(chat_id, False)
        await send_card(message, success_card("Vote-skip disabled.", "Anyone can now skip freely."))
        return

    try:
        pct = float(arg.rstrip("%"))
    except ValueError:
        await send_card(message, error_card("I did not understand that setting.", "Usage: /voteskip on | off | 50 (percent needed to skip)."))
        return
    if not 10 <= pct <= 100:
        await send_card(message, error_card("Pick a percentage between 10 and 100.", "50 means half the listeners must agree."))
        return
    ratio = await voteskip.set_ratio(chat_id, pct / 100)
    await send_card(message, success_card(f"Vote threshold set to {int(ratio * 100)}% of listeners."))


# ─────────────────────────────────────────────────────────────────────────────
# Scheduled playback
# ─────────────────────────────────────────────────────────────────────────────

_SCHEDULE_USAGE = (
    RichCard()
    .heading([plain("⏰ "), b("Scheduled Playback")], size=1)
    .para([plain("Start music automatically at a set time.")])
    .table(
        ["Command", "Effect"],
        [
            ["/schedule 07:00 lofi beats", "once, at the next 07:00"],
            ["/schedule daily 07:00 lofi", "every morning at 07:00"],
            ["/schedule in 30m my playlist", "30 minutes from now"],
            ["/schedule 7:30pm chill mix", "12-hour clock works too"],
            ["/schedules", "list what's queued up"],
            ["/unschedule <id>", "cancel one job"],
            ["/unschedule all", "cancel everything"],
        ],
    )
    .para(
        [
            plain("Set your local time first with "),
            c("/timezone +5:30"),
            plain(" so times mean what you expect."),
        ]
    )
    .footer("The voice chat must be open when the job fires.")
)


async def _chat_tz(chat_id: int) -> int:
    raw = await database.get_chat_value(
        chat_id, "tz_offset_min", config.default_tz_offset_min
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _fmt_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    h, m = divmod(abs(int(minutes)), 60)
    return f"UTC{sign}{h:02d}:{m:02d}"


@router.message(Command("timezone", "settz"))
async def cmd_timezone(message: Message, bot: Bot) -> None:
    """Set the chat's UTC offset so schedules use local time."""
    from bot.utils.cards import error_card, success_card

    args = (message.text or "").split(maxsplit=1)
    current = await _chat_tz(message.chat.id)

    if len(args) < 2:
        await send_card(
            message,
            RichCard()
            .heading([plain("🌍 "), b("Timezone")], size=1)
            .para([plain("This chat is set to "), c(_fmt_offset(current)), plain(".")])
            .para([plain("Change it with "), c("/timezone +5:30"), plain(" or "), c("/timezone -4")])
            .footer("Used by /schedule to interpret clock times."),
        )
        return

    if not await _can_control(message, bot):
        return

    raw = args[1].strip().upper().replace("UTC", "").replace("GMT", "").strip()
    m = re.match(r"^([+-]?)(\d{1,2})(?::?(\d{2}))?$", raw)
    if not m:
        await send_card(message, error_card("Usage: /timezone +5:30", "Offsets from UTC-12:00 to UTC+14:00."))
        return

    sign = -1 if m.group(1) == "-" else 1
    offset = sign * (int(m.group(2)) * 60 + int(m.group(3) or 0))
    if not -720 <= offset <= 840:
        await send_card(message, error_card("That offset is out of range (UTC-12:00 … UTC+14:00)."))
        return

    await database.set_chat_value(message.chat.id, "tz_offset_min", offset)
    await send_card(message, success_card(f"Timezone set to {_fmt_offset(offset)}."))


@router.message(Command("schedule", "sched"))
async def cmd_schedule(message: Message, bot: Bot) -> None:
    """Schedule playback: /schedule [daily] <time> <song or playlist>."""
    from bot.services.scheduler import scheduler
    from bot.utils.cards import error_card

    if not is_group(message):
        await send_card(message, error_card("Scheduling only works in groups.", "Add me to a group with a voice chat to schedule playback."))
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_card(message, _SCHEDULE_USAGE)
        return

    if not await _can_control(message, bot):
        return

    rest = args[1].strip()
    tz = await _chat_tz(message.chat.id)

    # Pull the time spec off the front, longest match first so "daily 07:00"
    # and "in 30m" win over a bare "07:00".
    from bot.services.scheduler import parse_when

    when_text, query = "", ""
    words = rest.split()
    for take in range(min(4, len(words)), 0, -1):
        candidate = " ".join(words[:take])
        if parse_when(candidate, tz):
            when_text, query = candidate, " ".join(words[take:])
            break

    if not when_text:
        await send_card(
            message,
            error_card(
                "I could not read that time.",
                "Try /schedule 07:00 lofi, /schedule daily 8pm jazz, or /schedule in 30m rock.",
            ),
        )
        return
    if not query:
        await send_card(message, error_card("Tell me what to play.", f"/schedule {when_text} <song or playlist>"))
        return

    try:
        job = await scheduler.add(
            message.chat.id,
            message.from_user.id if message.from_user else 0,
            query,
            when_text,
            tz,
        )
    except ValueError as exc:
        await send_card(message, error_card(str(exc), "Remove one with /unschedule <id>."))
        return

    if not job:
        await send_card(message, error_card("I could not read that time.", "Try 21:30, 9:30pm, or +45m for a relative time."))
        return

    await send_card(
        message,
        RichCard()
        .heading([plain("⏰ "), b("Scheduled")], size=1)
        .para([plain("I will play "), b(query[:80]), plain(" ")])
        .table(
            ["Field", "Value"],
            [
                ["When", job.describe()],
                ["Timezone", _fmt_offset(tz)],
                ["Job id", job.id],
            ],
        )
        .footer(f"Cancel with /unschedule {job.id}  •  see all with /schedules"),
    )


@router.message(Command("schedules", "scheduled"))
async def cmd_schedules(message: Message) -> None:
    from bot.services.scheduler import scheduler

    jobs = await scheduler.list_jobs(message.chat.id)
    if not jobs:
        await send_card(
            message,
            RichCard()
            .heading([plain("⏰ "), b("Schedules")], size=1)
            .para([i("Nothing scheduled in this chat.")])
            .footer("Add one with /schedule 07:00 <song>"),
        )
        return

    tz = await _chat_tz(message.chat.id)
    rows = [
        [job.id, job.describe(), (job.query[:36] + "…") if len(job.query) > 36 else job.query]
        for job in jobs
    ]
    await send_card(
        message,
        RichCard()
        .heading([plain("⏰ "), b("Schedules")], size=1)
        .table(["ID", "When", "What"], rows)
        .para([i(f"Times are {_fmt_offset(tz)}.")])
        .footer("/unschedule <id> to cancel  •  /unschedule all to clear"),
    )


@router.message(Command("unschedule", "delschedule"))
async def cmd_unschedule(message: Message, bot: Bot) -> None:
    from bot.services.scheduler import scheduler
    from bot.utils.cards import error_card, success_card

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_card(message, error_card("Usage: /unschedule <id>", "Or /unschedule all."))
        return
    if not await _can_control(message, bot):
        return

    target = args[1].strip().lower()
    if target == "all":
        count = await scheduler.clear(message.chat.id)
        await send_card(message, success_card(f"Cleared {count} schedule(s)."))
        return

    if await scheduler.remove(message.chat.id, target):
        await send_card(message, success_card(f"Schedule {target} cancelled."))
    else:
        await send_card(message, error_card(f"No schedule with id {target}.", "See /schedules."))
