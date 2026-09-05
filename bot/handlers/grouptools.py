"""Notes, filters, blacklist words, locks, rules, welcome, AFK, disable."""

from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.services.moderation import DISABLEABLE, LOCK_TYPES, moderation
from bot.utils.guards import (
    MUTED_PERMISSIONS,
    fill_placeholders,
    is_admin,
    is_admin_or_auth,
    is_group,
    mention,
    time_since,
)
from bot.utils.rich import RichCard, b, c, i, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="grouptools")


def _icon(icon: str):
    return plain(f"{icon} ")


async def _admin_only(message: Message, bot: Bot) -> bool:
    if not is_group(message):
        await send_html(message, "⚠️ <b>This command only works in groups.</b>")
        return False
    if not message.from_user:
        return False
    if not await is_admin_or_auth(bot, message.chat.id, message.from_user.id):
        await send_html(message, "🚫 <b>Admins only.</b>")
        return False
    return True


def _content_from(message: Message, text_override: str = "") -> dict:
    """Capture a note/filter payload from a message or its reply."""
    src = message.reply_to_message or message
    payload: dict = {"text": text_override or "", "type": "text"}

    if text_override:
        payload["text"] = text_override
    elif src is message.reply_to_message:
        payload["text"] = src.html_text if (src.text or src.caption) else ""

    if src.photo:
        payload |= {"type": "photo", "file_id": src.photo[-1].file_id}
    elif src.animation:
        payload |= {"type": "animation", "file_id": src.animation.file_id}
    elif src.video:
        payload |= {"type": "video", "file_id": src.video.file_id}
    elif src.sticker:
        payload |= {"type": "sticker", "file_id": src.sticker.file_id}
    elif src.audio:
        payload |= {"type": "audio", "file_id": src.audio.file_id}
    elif src.document:
        payload |= {"type": "document", "file_id": src.document.file_id}
    return payload


async def _send_content(message: Message, content: dict, user_fill: bool = True) -> None:
    """Render a stored note/filter back into the chat."""
    text = content.get("text", "") or ""
    if user_fill and message.from_user and text:
        text = fill_placeholders(text, message.from_user, message.chat)
    ctype = content.get("type", "text")
    fid = content.get("file_id")
    try:
        if ctype == "photo" and fid:
            await message.reply_photo(fid, caption=text or None, parse_mode="HTML")
        elif ctype == "animation" and fid:
            await message.reply_animation(fid, caption=text or None, parse_mode="HTML")
        elif ctype == "video" and fid:
            await message.reply_video(fid, caption=text or None, parse_mode="HTML")
        elif ctype == "sticker" and fid:
            await message.reply_sticker(fid)
            if text:
                await message.reply(text, parse_mode="HTML")
        elif ctype == "audio" and fid:
            await message.reply_audio(fid, caption=text or None, parse_mode="HTML")
        elif ctype == "document" and fid:
            await message.reply_document(fid, caption=text or None, parse_mode="HTML")
        elif text:
            await message.reply(text, parse_mode="HTML")
    except Exception as exc:
        logger.debug("Could not render stored content: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Notes
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("save"))
async def cmd_save_note(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 2:
        await send_html(
            message,
            "⚠️ <b>Usage:</b> <code>/save name Your note text</code>\n"
            "…or reply to any message with <code>/save name</code>.",
        )
        return
    name = args[1].lstrip("#").lower()
    body = args[2] if len(args) > 2 else ""
    if not body and not message.reply_to_message:
        await send_html(message, "⚠️ <b>Give the note some content or reply to a message.</b>")
        return
    await moderation.save_note(message.chat.id, name, _content_from(message, body))
    await send_html(
        message, f"📝 <b>Saved note</b> <code>{name}</code>.\nGet it with <code>#{name}</code> or <code>/get {name}</code>."
    )


@router.message(Command("get"))
async def cmd_get_note(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/get notename</code>")
        return
    note = await moderation.get_note(message.chat.id, args[1].strip().lstrip("#"))
    if not note:
        await send_html(message, "❌ <b>No note by that name.</b>")
        return
    await _send_content(message, note)


@router.message(Command("notes", "saved"))
async def cmd_notes(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    if await moderation.is_command_disabled(message.chat.id, "notes"):
        return
    names = await moderation.list_notes(message.chat.id)
    if not names:
        await send_html(message, "📝 <b>No notes saved in this chat yet.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("📝"), b("Saved Notes")], size=1)
        .para([plain(f"{len(names)} note(s) — tap or type "), c("#name"), plain(" to fetch one.")])
        .bullets([c(f"#{n}") for n in names])
        .footer("Admins can add notes with /save name text")
    )
    await send_card(message, card)


@router.message(Command("clearnote", "rmnote", "delnote"))
async def cmd_clear_note(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/clearnote notename</code>")
        return
    ok = await moderation.delete_note(message.chat.id, args[1].strip().lstrip("#"))
    await send_html(
        message, "🗑 <b>Note deleted.</b>" if ok else "❌ <b>No note by that name.</b>"
    )


@router.message(F.text.startswith("#"), F.chat.type.in_({"group", "supergroup"}))
async def hashtag_note(message: Message) -> None:
    """``#notename`` fetches a saved note, Rose-style."""
    name = (message.text or "").split()[0][1:].lower()
    if not name or len(name) > 64:
        return
    note = await moderation.get_note(message.chat.id, name)
    if note:
        await _send_content(message, note)


# ─────────────────────────────────────────────────────────────────────────────
# Custom filters
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("filter", "addfilter"))
async def cmd_filter(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 2:
        await send_html(
            message,
            "⚠️ <b>Usage:</b> <code>/filter hello Hi there!</code>\n"
            "Use quotes for multi-word triggers: <code>/filter \"good morning\" ☀️</code>",
        )
        return

    rest = (message.text or "").split(maxsplit=1)[1]
    if rest.startswith('"'):
        end = rest.find('"', 1)
        trigger = rest[1:end].strip().lower() if end > 0 else args[1].lower()
        body = rest[end + 1:].strip() if end > 0 else ""
    else:
        trigger = args[1].lower()
        body = args[2] if len(args) > 2 else ""

    if not body and not message.reply_to_message:
        await send_html(message, "⚠️ <b>Give the filter a reply or reply to a message.</b>")
        return
    await moderation.save_filter(message.chat.id, trigger, _content_from(message, body))
    await send_html(message, f"✅ <b>Filter saved for</b> <code>{trigger}</code>.")


@router.message(Command("stop", "delfilter"))
async def cmd_stop_filter(message: Message, bot: Bot) -> None:
    """Note: /stop here removes a filter only when an argument is given —
    otherwise the music /stop handler takes over."""
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        return  # let the playback router handle a bare /stop
    if not await _admin_only(message, bot):
        return
    ok = await moderation.delete_filter(message.chat.id, args[1].strip().lower())
    await send_html(message, "🗑 <b>Filter removed.</b>" if ok else "❌ <b>No such filter.</b>")


@router.message(Command("filters"))
async def cmd_filters(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    if await moderation.is_command_disabled(message.chat.id, "filters"):
        return
    filters = await moderation.list_filters(message.chat.id)
    if not filters:
        await send_html(message, "🔍 <b>No filters in this chat.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("🔍"), b("Active Filters")], size=1)
        .para([plain(f"{len(filters)} trigger(s) configured.")])
        .bullets([c(t) for t in sorted(filters)])
        .footer("Remove one with /stop <trigger>")
    )
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Word blacklist
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("blacklist", "addblacklist"))
async def cmd_blacklist(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        words = await moderation.blacklist_words(message.chat.id)
        mode = await moderation.blacklist_mode(message.chat.id)
        if not words:
            await send_html(message, "🚫 <b>No blacklisted words here.</b>")
            return
        card = (
            RichCard()
            .heading([_icon("🚫"), b("Blacklisted Words")], size=1)
            .para([b("Mode: "), c(mode)])
            .bullets([c(w) for w in words])
            .footer("Add with /blacklist word — remove with /unblacklist word")
        )
        await send_card(message, card)
        return
    if not await _admin_only(message, bot):
        return
    added = [w for w in args[1].split() if await moderation.add_blacklist_word(message.chat.id, w)]
    await send_html(message, f"🚫 <b>Blacklisted:</b> <code>{', '.join(added) or 'nothing new'}</code>")


@router.message(Command("unblacklist", "rmblacklist"))
async def cmd_unblacklist(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/unblacklist word</code>")
        return
    removed = [
        w for w in args[1].split() if await moderation.remove_blacklist_word(message.chat.id, w)
    ]
    await send_html(message, f"✅ <b>Removed:</b> <code>{', '.join(removed) or 'nothing'}</code>")


@router.message(Command("blacklistmode"))
async def cmd_blacklistmode(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split()[1:]
    if not args:
        mode = await moderation.blacklist_mode(message.chat.id)
        await send_html(
            message,
            f"🚫 <b>Blacklist mode:</b> <code>{mode}</code>\n"
            f"Options: <code>delete warn mute kick ban</code>",
        )
        return
    mode = await moderation.set_blacklist_mode(message.chat.id, args[0])
    await send_html(message, f"✅ <b>Blacklist mode set to</b> <code>{mode}</code>.")


# ─────────────────────────────────────────────────────────────────────────────
# Locks
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("locktypes"))
async def cmd_locktypes(message: Message) -> None:
    card = (
        RichCard()
        .heading([_icon("🔒"), b("Lockable Types")], size=1)
        .table(["Type", "Blocks"], [[c(k), v] for k, v in LOCK_TYPES.items()])
        .footer("Usage: /lock sticker  •  /unlock sticker  •  /locks")
    )
    await send_card(message, card)


@router.message(Command("lock", "unlock"))
async def cmd_lock(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    args = (message.text or "").split()[1:]
    if not args:
        await send_html(
            message,
            f"⚠️ <b>Usage:</b> <code>/{cmd} type</code>\nSee <code>/locktypes</code> for the list.",
        )
        return
    locking = cmd == "lock"
    applied, unknown = [], []
    for arg in args:
        key = arg.lower()
        if key not in LOCK_TYPES:
            unknown.append(key)
            continue
        await moderation.set_lock(message.chat.id, key, locking)
        applied.append(key)
    verb = "Locked" if locking else "Unlocked"
    text = f"{'🔒' if locking else '🔓'} <b>{verb}:</b> <code>{', '.join(applied) or 'nothing'}</code>"
    if unknown:
        text += f"\n⚠️ Unknown: <code>{', '.join(unknown)}</code>"
    await send_html(message, text)


@router.message(Command("locks"))
async def cmd_locks(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    locks = await moderation.locks(message.chat.id)
    active = {k: v for k, v in locks.items() if v}
    card = RichCard().heading([_icon("🔒"), b("Lock Status")], size=1)
    if not active:
        card.para([plain("Nothing is locked in this chat.")])
    else:
        card.checklist([(True, f"{k} — {LOCK_TYPES.get(k, '')}") for k in sorted(active)])
    card.footer("/lock <type> to restrict  •  /locktypes for all options")
    await send_card(message, card)


# ─────────────────────────────────────────────────────────────────────────────
# Anti-flood
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("setflood"))
async def cmd_setflood(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split()[1:]
    if not args:
        limit = await moderation.flood_limit(message.chat.id)
        action = await moderation.flood_action(message.chat.id)
        await send_html(
            message,
            f"🌊 <b>Anti-flood:</b> "
            f"{'<code>off</code>' if not limit else f'<code>{limit}</code> messages'}\n"
            f"<b>Action:</b> <code>{action}</code>\n\n"
            f"Set with <code>/setflood 10</code> or <code>/setflood off</code>",
        )
        return
    raw = args[0].lower()
    limit = 0 if raw in ("off", "no", "0", "disable") else (int(raw) if raw.isdigit() else -1)
    if limit < 0:
        await send_html(message, "⚠️ <b>Give a number or <code>off</code>.</b>")
        return
    value = await moderation.set_flood_limit(message.chat.id, limit)
    await send_html(
        message,
        "🌊 <b>Anti-flood disabled.</b>" if not value
        else f"🌊 <b>Anti-flood set to <code>{value}</code> consecutive messages.</b>",
    )


@router.message(Command("floodmode"))
async def cmd_floodmode(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split()[1:]
    if not args:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/floodmode mute|kick|ban</code>")
        return
    action = await moderation.set_flood_action(message.chat.id, args[0])
    await send_html(message, f"🌊 <b>Flood action set to <code>{action}</code>.</b>")


# ─────────────────────────────────────────────────────────────────────────────
# Rules
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("rules"))
async def cmd_rules(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    if await moderation.is_command_disabled(message.chat.id, "rules"):
        return
    rules = await moderation.get_rules(message.chat.id)
    if not rules:
        await send_html(message, "📋 <b>No rules set. Admins can add them with</b> <code>/setrules</code>.")
        return
    card = (
        RichCard()
        .heading([_icon("📋"), b(f"Rules for {message.chat.title or 'this chat'}")], size=1)
        .quote([line for line in rules.split("\n") if line.strip()])
        .footer("Breaking the rules may result in a warn, mute or ban.")
    )
    await send_card(message, card)


@router.message(Command("setrules"))
async def cmd_setrules(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=1)
    body = args[1] if len(args) > 1 else (
        message.reply_to_message.html_text if message.reply_to_message and message.reply_to_message.text else ""
    )
    if not body:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/setrules Be nice…</code> or reply to a message.")
        return
    await moderation.set_rules(message.chat.id, body)
    await send_html(message, "📋 <b>Rules updated.</b> View them with /rules.")


@router.message(Command("clearrules"))
async def cmd_clearrules(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    await moderation.clear_rules(message.chat.id)
    await send_html(message, "🗑 <b>Rules cleared.</b>")


# ─────────────────────────────────────────────────────────────────────────────
# Welcome / goodbye
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("setwelcome"))
async def cmd_setwelcome(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=1)
    body = args[1] if len(args) > 1 else ""
    if not body:
        await send_html(
            message,
            "⚠️ <b>Usage:</b> <code>/setwelcome Welcome {mention} to {chatname}!</code>\n\n"
            "<b>Placeholders:</b> <code>{first} {last} {fullname} {username} "
            "{mention} {id} {chatname} {count}</code>",
        )
        return
    await moderation.set_welcome(message.chat.id, body)
    await moderation.toggle_welcome(message.chat.id, True)
    await send_html(message, "👋 <b>Welcome message saved.</b>")


@router.message(Command("setgoodbye"))
async def cmd_setgoodbye(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await send_html(message, "⚠️ <b>Usage:</b> <code>/setgoodbye Bye {first}!</code>")
        return
    await moderation.set_goodbye(message.chat.id, args[1])
    await moderation.toggle_goodbye(message.chat.id, True)
    await send_html(message, "👋 <b>Goodbye message saved.</b>")


@router.message(Command("welcome", "goodbye"))
async def cmd_welcome_toggle(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    args = (message.text or "").split()[1:]
    settings = await moderation.welcome_settings(message.chat.id)
    if not args:
        state = settings["enabled"] if cmd == "welcome" else settings["goodbye_enabled"]
        text = settings["text"] if cmd == "welcome" else settings["goodbye_text"]
        card = (
            RichCard()
            .heading([_icon("👋"), b(f"{cmd.title()} Settings")], size=1)
            .para([b("Status: "), c("on" if state else "off")])
        )
        if text:
            card.details("Current message", [text])
        card.footer(f"Toggle with /{cmd} on|off")
        await send_card(message, card)
        return
    enabled = args[0].lower() in ("on", "yes", "true", "enable")
    if cmd == "welcome":
        await moderation.toggle_welcome(message.chat.id, enabled)
    else:
        await moderation.toggle_goodbye(message.chat.id, enabled)
    await send_html(message, f"👋 <b>{cmd.title()} messages {'enabled' if enabled else 'disabled'}.</b>")


@router.message(F.new_chat_members)
async def on_join(message: Message, bot: Bot) -> None:
    settings = await moderation.welcome_settings(message.chat.id)
    if not settings["enabled"]:
        return
    me = await bot.get_me()
    for user in message.new_chat_members or []:
        if user.id == me.id:
            await send_html(
                message,
                "🎵 <b>Thanks for adding me!</b>\n\n"
                "Start a voice chat and send <code>/play song name</code> to begin. "
                "Use /help for the full command list, and /settings to configure this chat.",
            )
            continue

        if settings["clean"] and settings["last_id"]:
            try:
                await bot.delete_message(message.chat.id, settings["last_id"])
            except Exception:
                pass

        template = settings["text"] or "👋 Welcome {mention} to <b>{chatname}</b>!"
        text = fill_placeholders(template, user, message.chat)
        try:
            sent = await message.answer(text, parse_mode="HTML")
            await moderation.remember_welcome_msg(message.chat.id, sent.message_id)
        except Exception as exc:
            logger.debug("Welcome failed: %s", exc)


@router.message(F.left_chat_member)
async def on_leave(message: Message, bot: Bot) -> None:
    settings = await moderation.welcome_settings(message.chat.id)
    if not settings["goodbye_enabled"] or not settings["goodbye_text"]:
        return
    user = message.left_chat_member
    if not user:
        return
    me = await bot.get_me()
    if user.id == me.id:
        return
    try:
        await message.answer(
            fill_placeholders(settings["goodbye_text"], user, message.chat), parse_mode="HTML"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# AFK
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("afk", "brb"))
async def cmd_afk(message: Message) -> None:
    if not message.from_user:
        return
    if await moderation.is_command_disabled(message.chat.id, "afk"):
        return
    args = (message.text or "").split(maxsplit=1)
    reason = args[1] if len(args) > 1 else ""
    await moderation.set_afk(message.from_user.id, reason)
    text = f"💤 <b>{message.from_user.full_name} is now AFK.</b>"
    if reason:
        text += f"\n<i>{reason}</i>"
    await send_html(message, text)


@router.message(F.text, F.chat.type.in_({"group", "supergroup"}))
async def afk_watcher(message: Message) -> None:
    """Announce returns and notify when an AFK user is mentioned."""
    if not message.from_user or (message.text or "").startswith("/"):
        return

    back = await moderation.clear_afk(message.from_user.id)
    if back:
        away = time_since(float(back.get("since", time.time())))
        await send_html(message, f"👋 <b>{message.from_user.full_name} is back</b> — away for {away}.")
        return

    targets: list[int] = []
    if message.reply_to_message and message.reply_to_message.from_user:
        targets.append(message.reply_to_message.from_user.id)
    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user:
            targets.append(ent.user.id)
        elif ent.type == "mention":
            username = (message.text or "")[ent.offset + 1 : ent.offset + ent.length]
            from bot.services.database import database

            for uid in await database.known_users():
                profile = await database.get_user(uid)
                if profile.get("username", "").lower() == username.lower():
                    targets.append(uid)
                    break

    for uid in dict.fromkeys(targets):
        if uid == message.from_user.id:
            continue
        afk = await moderation.get_afk(uid)
        if not afk:
            continue
        from bot.services.database import database

        profile = await database.get_user(uid)
        name = profile.get("name", "That user")
        away = time_since(float(afk.get("since", time.time())))
        text = f"💤 <b>{name} is AFK</b> — away for {away}."
        if afk.get("reason"):
            text += f"\n<i>{afk['reason']}</i>"
        await send_html(message, text)
        break


# ─────────────────────────────────────────────────────────────────────────────
# Disable / enable commands
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("disable", "enable"))
async def cmd_disable(message: Message, bot: Bot) -> None:
    if not await _admin_only(message, bot):
        return
    cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
    args = (message.text or "").split()[1:]
    if not args:
        await send_html(message, f"⚠️ <b>Usage:</b> <code>/{cmd} command</code> — see /disableable")
        return
    done, failed = [], []
    for arg in args:
        ok = (
            await moderation.disable_command(message.chat.id, arg)
            if cmd == "disable"
            else await moderation.enable_command(message.chat.id, arg)
        )
        (done if ok else failed).append(arg.lower())
    text = f"✅ <b>{cmd.title()}d:</b> <code>{', '.join(done) or 'nothing'}</code>"
    if failed:
        text += f"\n⚠️ Not applicable: <code>{', '.join(failed)}</code>"
    await send_html(message, text)


@router.message(Command("disableable"))
async def cmd_disableable(message: Message) -> None:
    disabled = await moderation.disabled_commands(message.chat.id) if is_group(message) else []
    card = (
        RichCard()
        .heading([_icon("🎛"), b("Disableable Commands")], size=1)
        .para([plain("Ticked commands are currently disabled in this chat.")])
        .checklist([(cmd in disabled, c(f"/{cmd}")) for cmd in sorted(DISABLEABLE)])
        .footer("/disable <cmd> and /enable <cmd> — admins only")
    )
    await send_card(message, card)
