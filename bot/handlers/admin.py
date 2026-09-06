"""Owner / sudo tools: broadcast, global bans, chat blacklist, maintenance."""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config
from bot.services import errors
from bot.services.database import database
from bot.services.queue import queue_manager
from bot.services.stats import bot_stats
from bot.services.stream import stream_manager
from bot.utils.guards import extract_target, is_sudo, mention_id
from bot.utils.cards import success_card
from bot.utils.rich import RichCard, b, c, i, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="admin")

#: Runtime flag toggled by /maintenance.
MAINTENANCE = {"on": False}


def _icon(icon: str):
    return plain(f"{icon} ")


async def _sudo_only(message: Message) -> bool:
    if not message.from_user or not is_sudo(message.from_user.id):
        await send_html(message, "🚫 <b>This command is for the bot owner only.</b>")
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("broadcast", "gcast"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not await _sudo_only(message):
        return

    args = (message.text or "").split(maxsplit=1)
    body = args[1] if len(args) > 1 else ""
    reply = message.reply_to_message
    if not body and not reply:
        await send_html(
            message,
            "📢 <b>Usage:</b> <code>/broadcast Your message</code>\n"
            "…or reply to any message to forward it.\n\n"
            "<b>Flags:</b> <code>-users</code> to include private chats, "
            "<code>-pin</code> to pin in each chat.",
        )
        return

    include_users = "-users" in body
    do_pin = "-pin" in body
    for flag in ("-users", "-pin"):
        body = body.replace(flag, "")
    body = body.strip()

    targets: list[int] = await database.known_chats()
    if include_users:
        targets += await database.known_users()
    targets = list(dict.fromkeys(targets))
    if not targets:
        await send_html(message, "📢 <b>No chats recorded yet.</b>")
        return

    status = await message.answer(
        f"📢 <b>Broadcasting to {len(targets)} chats…</b>", parse_mode="HTML"
    )
    sent = failed = 0
    for idx, chat_id in enumerate(targets, 1):
        try:
            if reply:
                out = await reply.copy_to(chat_id)
            else:
                out = await bot.send_message(chat_id, body, parse_mode="HTML")
            sent += 1
            if do_pin:
                try:
                    mid = getattr(out, "message_id", None)
                    if mid:
                        await bot.pin_chat_message(chat_id, mid, disable_notification=True)
                except Exception:
                    pass
        except Exception:
            failed += 1
        # Stay well inside Telegram's ~30 msg/sec ceiling.
        await asyncio.sleep(0.06)
        if idx % 25 == 0:
            try:
                await status.edit_text(
                    f"📢 <b>Broadcasting…</b> {idx}/{len(targets)} "
                    f"(<code>{sent}</code> ok, <code>{failed}</code> failed)",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    card = (
        RichCard()
        .heading([_icon("📢"), b("Broadcast Complete")], size=1)
        .table(
            ["Result", "Count"],
            [["Delivered", c(str(sent))], ["Failed", c(str(failed))], ["Total", c(str(len(targets)))]],
        )
    )
    try:
        await status.edit_text(card.to_html(), parse_mode="HTML")
    except Exception:
        await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Global bans
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("gban", "globalban"))
async def cmd_gban(message: Message, bot: Bot) -> None:
    if not await _sudo_only(message):
        return
    uid, name, reason = await extract_target(message, bot)
    if uid is None:
        await send_html(message, "⚠️ <b>Reply to a user or pass an id/@username.</b>")
        return
    if is_sudo(uid):
        await send_html(message, "🛡 <b>You can't gban another sudo user.</b>")
        return

    await database.ban_user(uid, reason)
    enforced = 0
    for chat_id in await database.known_chats():
        try:
            await bot.ban_chat_member(chat_id, uid)
            enforced += 1
        except Exception:
            continue
        await asyncio.sleep(0.05)

    card = (
        RichCard()
        .heading([_icon("🌍"), b("Globally Banned")], size=1)
        .quote(
            [
                [b("User: "), plain(f"{name} ({uid})")],
                [b("Reason: "), plain(reason or "No reason given")],
                [b("Enforced in: "), c(f"{enforced} chats")],
            ]
        )
        .footer("They are blocked from using the bot everywhere.")
    )
    await send_card(message, card)


@router.message(Command("ungban", "globalunban"))
async def cmd_ungban(message: Message, bot: Bot) -> None:
    if not await _sudo_only(message):
        return
    uid, name, _ = await extract_target(message, bot)
    if uid is None:
        await send_html(message, "⚠️ <b>Reply to a user or pass an id/@username.</b>")
        return
    ok = await database.unban_user(uid)
    if ok:
        for chat_id in await database.known_chats():
            try:
                await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            except Exception:
                continue
            await asyncio.sleep(0.05)
    await send_html(
        message,
        f"✅ <b>{mention_id(uid, name)} is no longer globally banned.</b>" if ok
        else "⚠️ <b>That user wasn't globally banned.</b>",
    )


@router.message(Command("gbanlist", "bannedusers"))
async def cmd_gbanlist(message: Message) -> None:
    if not await _sudo_only(message):
        return
    users = await database.banned_users()
    if not users:
        await send_html(message, "🌍 <b>No globally banned users.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("🌍"), b("Globally Banned Users")], size=1)
        .bullets([c(str(uid)) for uid in users[:60]])
        .footer(f"{len(users)} total")
    )
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Chat blacklist
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("blacklistchat", "blchat"))
async def cmd_blacklistchat(message: Message, bot: Bot) -> None:
    if not await _sudo_only(message):
        return
    args = (message.text or "").split()[1:]
    chat_id = int(args[0]) if args and args[0].lstrip("-").isdigit() else message.chat.id
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    await database.blacklist_chat(chat_id, reason)
    await send_html(message, f"🚫 <b>Chat <code>{chat_id}</code> blacklisted.</b>")
    if chat_id == message.chat.id:
        try:
            await bot.leave_chat(chat_id)
        except Exception:
            pass


@router.message(Command("whitelistchat", "unblchat"))
async def cmd_whitelistchat(message: Message) -> None:
    if not await _sudo_only(message):
        return
    args = (message.text or "").split()[1:]
    chat_id = int(args[0]) if args and args[0].lstrip("-").isdigit() else message.chat.id
    ok = await database.whitelist_chat(chat_id)
    await send_html(
        message,
        f"✅ <b>Chat <code>{chat_id}</code> removed from the blacklist.</b>" if ok
        else "⚠️ <b>That chat wasn't blacklisted.</b>",
    )


@router.message(Command("blacklistedchats"))
async def cmd_blacklistedchats(message: Message) -> None:
    if not await _sudo_only(message):
        return
    chats = await database.blacklisted_chats()
    if not chats:
        await send_html(message, "✅ <b>No blacklisted chats.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("🚫"), b("Blacklisted Chats")], size=1)
        .bullets([c(str(cid)) for cid in chats[:60]])
        .footer(f"{len(chats)} total")
    )
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance & control
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("maintenance"))
async def cmd_maintenance(message: Message) -> None:
    if not await _sudo_only(message):
        return
    args = (message.text or "").split()[1:]
    if not args:
        state = "on" if MAINTENANCE["on"] else "off"
        await send_html(message, f"🛠 <b>Maintenance mode is <code>{state}</code>.</b>")
        return
    MAINTENANCE["on"] = args[0].lower() in ("on", "yes", "true", "enable")
    await send_html(
        message,
        "🛠 <b>Maintenance mode enabled</b> — only sudo users can use the bot."
        if MAINTENANCE["on"] else "✅ <b>Maintenance mode disabled.</b>",
    )


@router.message(Command("astop", "forcestop"))
async def cmd_astop(message: Message) -> None:
    if not await _sudo_only(message):
        return
    await stream_manager.stop(message.chat.id)
    await queue_manager.reset(message.chat.id)
    await send_html(message, "⏹ <b>Playback force-stopped by the owner.</b>")


@router.message(Command("stopall"))
async def cmd_stopall(message: Message) -> None:
    if not await _sudo_only(message):
        return
    chats = list(stream_manager.active_chats)
    for chat_id in chats:
        try:
            await stream_manager.stop(chat_id)
            await queue_manager.reset(chat_id)
        except Exception:
            continue
    await send_html(message, f"⏹ <b>Stopped playback in {len(chats)} chat(s).</b>")


@router.message(Command("activevc", "activevoice"))
async def cmd_activevc(message: Message, bot: Bot) -> None:
    if not await _sudo_only(message):
        return
    chats = stream_manager.active_chats
    if not chats:
        await send_html(message, "📡 <b>No active voice chats.</b>")
        return
    rows = []
    for chat_id in chats:
        current = await queue_manager.get_current(chat_id)
        profile = await database.get_chat(chat_id)
        rows.append(
            [
                profile.get("title", str(chat_id))[:28],
                (current or {}).get("title", "—")[:32],
                c(str(await queue_manager.size(chat_id))),
            ]
        )
    card = (
        RichCard()
        .heading([_icon("📡"), b("Active Voice Chats")], size=1)
        .table(["Chat", "Now Playing", "Queue"], rows)
        .footer(f"{len(chats)} active stream(s)")
    )
    await send_card(message, card)


@router.message(Command("sudolist", "sudoers"))
async def cmd_sudolist(message: Message) -> None:
    owners = config.owners
    if not owners:
        await send_html(message, "🛡 <b>No sudo users configured.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("🛡"), b("Sudo Users")], size=1)
        .bullets([c(str(uid)) + (" (owner)" if uid == config.owner_id else "") for uid in owners])
        .footer("Configure with OWNER_ID and SUDO_USERS in .env")
    )
    await send_card(message, card)


@router.message(Command("logs"))
async def cmd_logs(message: Message) -> None:
    if not await _sudo_only(message):
        return
    from pathlib import Path

    from aiogram.types import FSInputFile

    log_path = Path("data/bot.log")
    if not log_path.is_file():
        await send_html(message, "📄 <b>No log file found.</b>")
        return
    try:
        await message.answer_document(FSInputFile(log_path), caption="📄 Recent logs")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


@router.message(Command("sysinfo", "sys"))
async def cmd_sysinfo(message: Message) -> None:
    if not await _sudo_only(message):
        return
    stats = await bot_stats.summary()
    rows = [
        ["Python", c(sys.version.split()[0])],
        ["Platform", c(platform.system() + " " + platform.release())],
        ["Uptime", c(str(stats.get("uptime", "—")))],
        ["Storage", c(database.backend)],
        ["Active VCs", c(str(len(stream_manager.active_chats)))],
        ["Rich messages", c("on" if _rich_on() else "html fallback")],
    ]
    try:
        import psutil

        proc = psutil.Process()
        rows += [
            ["CPU", c(f"{psutil.cpu_percent(interval=0.3)}%")],
            ["RAM", c(f"{psutil.virtual_memory().percent}%")],
            ["Bot RSS", c(f"{proc.memory_info().rss / 1024 / 1024:.0f} MB")],
        ]
    except Exception:
        pass

    card = RichCard().heading([_icon("🖥"), b("System Info")], size=1).table(["Metric", "Value"], rows)
    await send_card(message, card)


def _rich_on() -> bool:
    from bot.utils.rich import rich_supported

    return rich_supported()


@router.message(Command("sudo", "owner"))
async def cmd_sudo_info(message: Message) -> None:
    if not await _sudo_only(message):
        return
    card = (
        RichCard()
        .heading([_icon("🛡"), b("Owner Control Panel")], size=1)
        .table(
            ["Command", "What it does"],
            [
                [c("/broadcast"), "Message every chat (-users, -pin)"],
                [c("/gban  /ungban"), "Global ban across all chats"],
                [c("/blacklistchat"), "Make the bot leave and refuse a chat"],
                [c("/maintenance on|off"), "Restrict the bot to sudo users"],
                [c("/stopall"), "Stop every active stream"],
                [c("/activevc"), "List active voice chats"],
                [c("/sysinfo"), "Runtime and resource usage"],
                [c("/logs"), "Fetch the log file"],
                [c("/sudolist"), "Show configured owners"],
            ],
        )
        .footer(f"Storage backend: {database.backend}")
    )
    await send_card(message, card)


@router.message(Command("errors", "bugs"))
async def cmd_errors(message: Message) -> None:
    """Recent unhandled exceptions, most frequent first.

    Reading a PaaS log stream to find out whether the bot is healthy is
    painful; this surfaces the same information where the operator already is.
    """
    if not is_sudo(message.from_user.id if message.from_user else None):
        return

    entries = errors.snapshot()
    if not entries:
        await send_card(
            message,
            success_card("No unhandled errors recorded.", "The bot is behaving."),
        )
        return

    card = RichCard().heading([_icon("🐞"), b("Recent Errors")], size=1)
    for item in entries[:10]:
        age = item["age"]
        when = f"{age}s ago" if age < 90 else f"{age // 60}m ago"
        card.para(
            [
                c(item["id"]),
                plain("  "),
                b(item["kind"]),
                plain(f"  ×{item['count']}"),
            ]
        )
        card.para([plain("     "), i(f"{item['message'][:90]}  •  {when}")])
        if item["contexts"]:
            card.para([plain("     "), i("from: " + ", ".join(item["contexts"][:3]))])

    total = sum(e["count"] for e in entries)
    card.footer(f"{len(entries)} distinct  •  {total} total  •  /clearerrors to reset")
    await send_card(message, card)


@router.message(Command("clearerrors"))
async def cmd_clear_errors(message: Message) -> None:
    if not is_sudo(message.from_user.id if message.from_user else None):
        return
    dropped = errors.clear()
    await send_card(message, success_card(f"Cleared {dropped} tracked error(s)."))
