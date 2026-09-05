"""Miss Rose-style moderation: ban, mute, kick, warn, purge, pin, promote."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.services.database import database
from bot.services.moderation import WARN_ACTIONS, moderation
from bot.utils.guards import (
    MUTED_PERMISSIONS,
    UNMUTED_PERMISSIONS,
    admin_ids,
    bot_can,
    extract_target,
    humanize_seconds,
    invalidate_admin_cache,
    is_admin,
    is_admin_or_auth,
    is_group,
    is_sudo,
    is_user_admin_protected,
    mention_id,
    split_duration_reason,
)
from bot.utils.rich import RichCard, a, b, c, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="moderation")


def _icon(icon: str):
    """Leading emoji as a plain span."""
    return plain(f"{icon} ")


# ─────────────────────────────────────────────────────────────────────────────
# Shared guard
# ─────────────────────────────────────────────────────────────────────────────

async def _guard(
    message: Message, bot: Bot, *, need_right: str = "can_restrict_members"
) -> bool:
    """Common preconditions for a moderation command."""
    if not is_group(message):
        await send_html(message, "⚠️ <b>This command only works in groups.</b>")
        return False
    user = message.from_user
    if not user:
        return False
    if not await is_admin_or_auth(bot, message.chat.id, user.id):
        await send_html(message, "🚫 <b>You need to be an admin to do that.</b>")
        return False
    if need_right and not await bot_can(bot, message.chat.id, need_right):
        pretty = need_right.replace("can_", "").replace("_", " ")
        await send_html(
            message, f"🚫 <b>I need the <code>{pretty}</code> admin right to do that.</b>"
        )
        return False
    return True


async def _resolve(message: Message, bot: Bot) -> tuple[int | None, str, str]:
    uid, name, reason = await extract_target(message, bot)
    if uid is None:
        await send_html(
            message,
            "⚠️ <b>Who?</b>\nReply to a user, or pass an @username / user id.",
        )
    return uid, name, reason


async def _protected(message: Message, bot: Bot, uid: int, name: str) -> bool:
    if await is_user_admin_protected(bot, message.chat.id, uid):
        await send_html(message, f"🛡 <b>{mention_id(uid, name)} is protected — I won't touch them.</b>")
        return True
    return False


def _action_card(
    icon: str,
    action: str,
    target: str,
    admin: str,
    reason: str,
    extra: list[tuple[str, str]] | None = None,
) -> RichCard:
    card = RichCard().heading([_icon(icon), b(action)], size=1)
    rows = [("User", target), ("By", admin)]
    if extra:
        rows.extend(extra)
    rows.append(("Reason", reason or "No reason given"))
    card.quote([[b(f"{k}: "), plain(v)] for k, v in rows])
    return card


# ─────────────────────────────────────────────────────────────────────────────
# Ban / unban / kick
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("ban", "dban", "sban"))
async def cmd_ban(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    if await _protected(message, bot, uid, name):
        return

    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    seconds, reason = split_duration_reason(reason)
    until = None
    if seconds:
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    try:
        await bot.ban_chat_member(message.chat.id, uid, until_date=until)
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not ban:</b> <code>{exc}</code>")
        return

    if cmd == "dban" and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass
    if cmd == "sban":
        try:
            await message.delete()
        except Exception:
            pass
        return

    admin = message.from_user.full_name if message.from_user else "admin"
    extra = [("Duration", humanize_seconds(seconds))] if seconds else None
    card = _action_card("🔨", "Banned" if not seconds else "Temporarily Banned",
                        name, admin, reason, extra)
    card.footer("They can be restored any time with /unban.")
    await send_card(message, card)
    await _log_action(bot, message, "ban", uid, name, reason)


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    try:
        await bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not unban:</b> <code>{exc}</code>")
        return
    await send_html(message, f"✅ <b>{mention_id(uid, name)} has been unbanned.</b>")
    await _log_action(bot, message, "unban", uid, name, reason)


@router.message(Command("kick", "punch", "dkick"))
async def cmd_kick(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    if await _protected(message, bot, uid, name):
        return
    try:
        await bot.ban_chat_member(message.chat.id, uid)
        await bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not kick:</b> <code>{exc}</code>")
        return
    if (message.text or "").lstrip("/").startswith("dkick") and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass
    admin = message.from_user.full_name if message.from_user else "admin"
    card = _action_card("👢", "Kicked", name, admin, reason)
    card.footer("They may rejoin with a new invite link.")
    await send_card(message, card)
    await _log_action(bot, message, "kick", uid, name, reason)


@router.message(Command("kickme"))
async def cmd_kickme(message: Message, bot: Bot) -> None:
    if not is_group(message) or not message.from_user:
        return
    uid = message.from_user.id
    if await is_admin(bot, message.chat.id, uid):
        await send_html(message, "😅 <b>You're an admin — I can't kick you.</b>")
        return
    try:
        await bot.ban_chat_member(message.chat.id, uid)
        await bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
        await send_html(message, "👋 <b>See you around!</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Mute / unmute
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("mute", "tmute", "dmute"))
async def cmd_mute(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    if await _protected(message, bot, uid, name):
        return

    seconds, reason = split_duration_reason(reason)
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
    try:
        await bot.restrict_chat_member(
            message.chat.id, uid, permissions=MUTED_PERMISSIONS, until_date=until
        )
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not mute:</b> <code>{exc}</code>")
        return

    if (message.text or "").lstrip("/").startswith("dmute") and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass

    admin = message.from_user.full_name if message.from_user else "admin"
    extra = [("Duration", humanize_seconds(seconds))] if seconds else None
    card = _action_card("🔇", "Muted" if not seconds else "Temporarily Muted",
                        name, admin, reason, extra)
    await send_card(message, card)
    await _log_action(bot, message, "mute", uid, name, reason)


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    try:
        await bot.restrict_chat_member(message.chat.id, uid, permissions=UNMUTED_PERMISSIONS)
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not unmute:</b> <code>{exc}</code>")
        return
    await send_html(message, f"🔊 <b>{mention_id(uid, name)} can speak again.</b>")
    await _log_action(bot, message, "unmute", uid, name, reason)


# ─────────────────────────────────────────────────────────────────────────────
# Warnings
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("warn", "dwarn"))
async def cmd_warn(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot):
        return
    uid, name, reason = await _resolve(message, bot)
    if uid is None:
        return
    if await _protected(message, bot, uid, name):
        return

    count, limit, action = await moderation.add_warn(message.chat.id, uid, reason)

    if (message.text or "").lstrip("/").startswith("dwarn") and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass

    if count >= limit:
        await moderation.reset_warns(message.chat.id, uid)
        applied = "muted"
        try:
            if action == "ban":
                await bot.ban_chat_member(message.chat.id, uid)
                applied = "banned"
            elif action == "kick":
                await bot.ban_chat_member(message.chat.id, uid)
                await bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
                applied = "kicked"
            else:
                await bot.restrict_chat_member(
                    message.chat.id, uid, permissions=MUTED_PERMISSIONS
                )
        except Exception as exc:
            await send_html(message, f"⚠️ <b>Warn limit reached but action failed:</b> <code>{exc}</code>")
            return

        card = (
            RichCard()
            .heading([_icon("⛔"), b("Warn Limit Reached")], size=1)
            .para([b(name), plain(f" hit {limit}/{limit} warnings and was "), b(applied), plain(".")])
            .quote([[b("Last reason: "), plain(reason or "No reason given")]])
            .footer("Their warn counter has been reset.")
        )
        await send_card(message, card)
        await _log_action(bot, message, f"warn-limit ({applied})", uid, name, reason)
        return

    card = (
        RichCard()
        .heading([_icon("⚠️"), b("Warning")], size=1)
        .quote(
            [
                [b("User: "), plain(name)],
                [b("Warns: "), c(f"{count}/{limit}")],
                [b("Reason: "), plain(reason or "No reason given")],
            ]
        )
        .footer(f"At {limit} warnings the user will be {action}ed.")
    )
    from bot.keyboards.moderation import warn_actions_kb

    await send_card(message, card, reply_markup=warn_actions_kb(uid))
    await _log_action(bot, message, "warn", uid, name, reason)


@router.message(Command("warns"))
async def cmd_warns(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    uid, name, _ = await extract_target(message, bot)
    if uid is None and message.from_user:
        uid, name = message.from_user.id, message.from_user.full_name
    if uid is None:
        return
    data = await moderation.get_warns(message.chat.id, uid)
    settings = await moderation.warn_settings(message.chat.id)
    count = int(data.get("count", 0))
    if not count:
        await send_html(message, f"✅ <b>{mention_id(uid, name)} has no warnings.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("⚠️"), b(f"Warnings for {name}")], size=1)
        .para([b("Total: "), c(f"{count}/{settings['limit']}")])
    )
    reasons = [r.get("reason", "—") for r in data.get("reasons", [])]
    if reasons:
        card.bullets(reasons, ordered=True)
    card.footer(f"Action at limit: {settings['action']}")
    await send_card(message, card)


@router.message(Command("resetwarn", "resetwarns", "unwarn"))
async def cmd_resetwarn(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right=""):
        return
    uid, name, _ = await _resolve(message, bot)
    if uid is None:
        return
    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    if cmd == "unwarn":
        left = await moderation.remove_one_warn(message.chat.id, uid)
        await send_html(message, f"✅ <b>Removed one warning from {mention_id(uid, name)}</b> — {left} left.")
    else:
        await moderation.reset_warns(message.chat.id, uid)
        await send_html(message, f"✅ <b>Cleared all warnings for {mention_id(uid, name)}.</b>")


@router.message(Command("warnlimit"))
async def cmd_warnlimit(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right=""):
        return
    args = (message.text or "").split()[1:]
    if not args or not args[0].isdigit():
        settings = await moderation.warn_settings(message.chat.id)
        await send_html(
            message,
            f"⚠️ <b>Warn limit:</b> <code>{settings['limit']}</code>\n"
            f"<b>Action:</b> <code>{settings['action']}</code>\n\n"
            f"Set with <code>/warnlimit 5</code>",
        )
        return
    limit = await moderation.set_warn_limit(message.chat.id, int(args[0]))
    await send_html(message, f"✅ <b>Warn limit set to <code>{limit}</code>.</b>")


@router.message(Command("warnmode", "warnaction"))
async def cmd_warnmode(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right=""):
        return
    args = (message.text or "").split()[1:]
    if not args or args[0].lower() not in WARN_ACTIONS:
        await send_html(
            message,
            f"⚠️ <b>Usage:</b> <code>/warnmode {'|'.join(WARN_ACTIONS)}</code>",
        )
        return
    action = await moderation.set_warn_action(message.chat.id, args[0])
    await send_html(message, f"✅ <b>Warn action set to <code>{action}</code>.</b>")


@router.callback_query(F.data.startswith("warn:remove:"))
async def cb_warn_remove(query: CallbackQuery, bot: Bot) -> None:
    if not query.message or not query.from_user:
        return
    if not await is_admin_or_auth(bot, query.message.chat.id, query.from_user.id):
        await query.answer("Admins only.", show_alert=True)
        return
    uid = int(query.data.split(":")[-1])
    left = await moderation.remove_one_warn(query.message.chat.id, uid)
    await query.answer(f"Warning removed — {left} left.")
    try:
        await query.message.edit_text(
            f"{query.message.html_text}\n\n✅ <i>One warning removed by "
            f"{query.from_user.full_name} — {left} remaining.</i>",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Purge / delete
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("purge"))
async def cmd_purge(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_delete_messages"):
        return
    if not message.reply_to_message:
        await send_html(message, "⚠️ <b>Reply to the message you want to purge from.</b>")
        return

    start_id = message.reply_to_message.message_id
    end_id = message.message_id
    if end_id - start_id > 400:
        await send_html(message, "⚠️ <b>That's too many messages — purge up to 400 at a time.</b>")
        return

    deleted = 0
    batch: list[int] = []
    for mid in range(start_id, end_id + 1):
        batch.append(mid)
        if len(batch) == 100:
            deleted += await _delete_batch(bot, message.chat.id, batch)
            batch = []
    if batch:
        deleted += await _delete_batch(bot, message.chat.id, batch)

    note = await message.answer(f"🧹 <b>Purged {deleted} messages.</b>", parse_mode="HTML")
    await asyncio.sleep(4)
    try:
        await note.delete()
    except Exception:
        pass


async def _delete_batch(bot: Bot, chat_id: int, ids: list[int]) -> int:
    try:
        await bot.delete_messages(chat_id, ids)
        return len(ids)
    except Exception:
        count = 0
        for mid in ids:
            try:
                await bot.delete_message(chat_id, mid)
                count += 1
            except Exception:
                continue
        return count


@router.message(Command("del"))
async def cmd_del(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_delete_messages"):
        return
    if not message.reply_to_message:
        await send_html(message, "⚠️ <b>Reply to the message you want deleted.</b>")
        return
    try:
        await message.reply_to_message.delete()
        await message.delete()
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Pin / unpin
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("pin"))
async def cmd_pin(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_pin_messages"):
        return
    if not message.reply_to_message:
        await send_html(message, "⚠️ <b>Reply to the message you want pinned.</b>")
        return
    loud = "loud" in (message.text or "").lower() or "notify" in (message.text or "").lower()
    try:
        await bot.pin_chat_message(
            message.chat.id, message.reply_to_message.message_id,
            disable_notification=not loud,
        )
        await send_html(message, "📌 <b>Pinned.</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


@router.message(Command("unpin"))
async def cmd_unpin(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_pin_messages"):
        return
    try:
        if message.reply_to_message:
            await bot.unpin_chat_message(message.chat.id, message.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(message.chat.id)
        await send_html(message, "📌 <b>Unpinned.</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


@router.message(Command("unpinall"))
async def cmd_unpinall(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_pin_messages"):
        return
    try:
        await bot.unpin_all_chat_messages(message.chat.id)
        await send_html(message, "📌 <b>All messages unpinned.</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Promote / demote / title
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("promote", "fullpromote"))
async def cmd_promote(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_promote_members"):
        return
    uid, name, _ = await _resolve(message, bot)
    if uid is None:
        return
    full = (message.text or "").lstrip("/").lower().startswith("fullpromote")
    try:
        await bot.promote_chat_member(
            message.chat.id,
            uid,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_invite_users=True,
            can_change_info=full,
            can_promote_members=full,
        )
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not promote:</b> <code>{exc}</code>")
        return
    invalidate_admin_cache(message.chat.id)
    level = "full admin" if full else "admin"
    await send_html(message, f"⬆️ <b>{mention_id(uid, name)} is now {level}.</b>")
    await _log_action(bot, message, "promote", uid, name, level)


@router.message(Command("demote"))
async def cmd_demote(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_promote_members"):
        return
    uid, name, _ = await _resolve(message, bot)
    if uid is None:
        return
    try:
        await bot.promote_chat_member(
            message.chat.id, uid,
            can_manage_chat=False, can_delete_messages=False,
            can_manage_video_chats=False, can_restrict_members=False,
            can_pin_messages=False, can_invite_users=False,
            can_change_info=False, can_promote_members=False,
        )
    except Exception as exc:
        await send_html(message, f"❌ <b>Could not demote:</b> <code>{exc}</code>")
        return
    invalidate_admin_cache(message.chat.id)
    await send_html(message, f"⬇️ <b>{mention_id(uid, name)} has been demoted.</b>")
    await _log_action(bot, message, "demote", uid, name, "")


@router.message(Command("title", "settitle"))
async def cmd_title(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right="can_promote_members"):
        return
    uid, name, title = await _resolve(message, bot)
    if uid is None:
        return
    if not title:
        await send_html(message, "⚠️ <b>Give me a title:</b> <code>/title @user Music Boss</code>")
        return
    try:
        await bot.set_chat_administrator_custom_title(message.chat.id, uid, title[:16])
        await send_html(message, f"🏷 <b>{mention_id(uid, name)} is now “{title[:16]}”.</b>")
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")


# ─────────────────────────────────────────────────────────────────────────────
# Admin list & permissions overview
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("admins", "adminlist"))
async def cmd_adminlist(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    try:
        members = await bot.get_chat_administrators(message.chat.id)
    except Exception as exc:
        await send_html(message, f"❌ <code>{exc}</code>")
        return

    creator = [m for m in members if m.status == "creator"]
    admins = [m for m in members if m.status == "administrator" and not m.user.is_bot]
    bots = [m for m in members if m.user.is_bot]

    card = RichCard().heading([_icon("👮"), b(f"Admins in {message.chat.title or 'this chat'}")], size=1)
    if creator:
        owner = creator[0].user
        card.para([b("Owner: "), plain(owner.full_name)])
    if admins:
        card.para([b("Administrators")])
        card.bullets([
            f"{m.user.full_name}" + (f" — {m.custom_title}" if getattr(m, "custom_title", None) else "")
            for m in admins
        ])
    if bots:
        card.para([b("Bots")])
        card.bullets([m.user.full_name for m in bots])
    card.footer(f"{len(members)} admins total")
    await send_card(message, card)


@router.message(Command("id"))
async def cmd_id(message: Message, bot: Bot) -> None:
    lines: list[tuple[str, str]] = [("Chat ID", str(message.chat.id))]
    if message.from_user:
        lines.append(("Your ID", str(message.from_user.id)))
    if message.reply_to_message and message.reply_to_message.from_user:
        r = message.reply_to_message.from_user
        lines.append((f"{r.full_name}", str(r.id)))
    if message.reply_to_message and message.reply_to_message.forward_from:
        f = message.reply_to_message.forward_from
        lines.append((f"Forwarded from {f.full_name}", str(f.id)))
    card = RichCard().heading([_icon("🆔"), b("Identifiers")], size=1)
    card.table(["What", "ID"], [[k, c(v)] for k, v in lines])
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("report"))
@router.message(F.text.regexp(r"^@admins?\b", flags=__import__("re").IGNORECASE))
async def cmd_report(message: Message, bot: Bot) -> None:
    if not is_group(message) or not message.from_user:
        return
    if not await moderation.reports_enabled(message.chat.id):
        return
    if not message.reply_to_message:
        await send_html(message, "⚠️ <b>Reply to the message you want to report.</b>")
        return

    reported = message.reply_to_message.from_user
    if not reported:
        return
    if await is_admin(bot, message.chat.id, reported.id):
        await send_html(message, "😅 <b>You can't report an admin.</b>")
        return

    admins = await admin_ids(bot, message.chat.id)
    pings = "".join(f'<a href="tg://user?id={uid}">\u2060</a>' for uid in list(admins)[:12])
    link = ""
    if message.chat.username:
        link = f"https://t.me/{message.chat.username}/{message.reply_to_message.message_id}"

    card = (
        RichCard()
        .heading([_icon("🚨"), b("Report")], size=1)
        .quote(
            [
                [b("Reported: "), plain(reported.full_name)],
                [b("By: "), plain(message.from_user.full_name)],
                [b("Reason: "), plain(" ".join((message.text or "").split()[1:]) or "not given")],
            ]
        )
    )
    if link:
        card.para([a("Jump to message", link)])
    card.footer("Admins have been notified.")
    await send_card(message, card, reply=True)
    if pings:
        try:
            ping_msg = await message.answer(pings, parse_mode="HTML")
            await asyncio.sleep(1)
            await ping_msg.delete()
        except Exception:
            pass


@router.message(Command("reports"))
async def cmd_reports_toggle(message: Message, bot: Bot) -> None:
    if not await _guard(message, bot, need_right=""):
        return
    args = (message.text or "").split()[1:]
    if not args:
        state = await moderation.reports_enabled(message.chat.id)
        await send_html(message, f"🚨 <b>Reports are {'enabled' if state else 'disabled'}.</b>")
        return
    enabled = args[0].lower() in ("on", "yes", "true", "enable")
    await moderation.set_reports(message.chat.id, enabled)
    await send_html(message, f"🚨 <b>Reports {'enabled' if enabled else 'disabled'}.</b>")


# ─────────────────────────────────────────────────────────────────────────────
# Log channel mirroring
# ─────────────────────────────────────────────────────────────────────────────

async def _log_action(
    bot: Bot, message: Message, action: str, uid: int, name: str, reason: str
) -> None:
    from bot.config import config

    if not config.log_group_id:
        return
    admin = message.from_user.full_name if message.from_user else "unknown"
    text = (
        f"#{action.upper().replace(' ', '_').replace('-', '_')}\n"
        f"<b>Chat:</b> {message.chat.title or message.chat.id} "
        f"(<code>{message.chat.id}</code>)\n"
        f"<b>User:</b> {mention_id(uid, name)} (<code>{uid}</code>)\n"
        f"<b>Admin:</b> {admin}\n"
        f"<b>Reason:</b> {reason or '—'}"
    )
    try:
        await bot.send_message(config.log_group_id, text, parse_mode="HTML")
    except Exception as exc:
        logger.debug("Log delivery failed: %s", exc)
