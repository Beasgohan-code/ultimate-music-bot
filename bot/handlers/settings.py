"""Interactive /settings panel and the paginated /help menu."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.keyboards.moderation import (
    help_back_kb,
    help_menu_kb,
    language_kb,
    locks_kb,
    settings_back_kb,
    settings_root_kb,
    toggle_kb,
)
from bot.services.database import database
from bot.services.i18n import get_lang, translator
from bot.services.moderation import LOCK_TYPES, moderation
from bot.utils.guards import is_admin_or_auth, is_group
from bot.utils.rich import RichCard, b, c, plain, send_card, send_html

logger = logging.getLogger(__name__)
router = Router(name="settings")


def _icon(icon: str):
    return plain(f"{icon} ")


# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────

HELP_CATEGORIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "play": (
        "🎵 Playback",
        [
            ("/play <song|url>", "Play audio in the voice chat"),
            ("/vplay <query>", "Play video (MP4/MKV/YouTube)"),
            ("/cplay <query>", "Play into a linked channel's VC"),
            ("/playforce <query>", "Play immediately, keeping the queue"),
            ("/playnext <query>", "Queue a track next"),
            ("/stream <url>", "Live stream (m3u8, radio, YouTube Live)"),
            ("/playlist <url>", "Load a whole playlist"),
            ("/search <query>", "Pick from search results"),
            ("/radio", "Built-in internet radio stations"),
            ("/mood <name>", "Mood-based playlists"),
        ],
    ),
    "control": (
        "🎛 Controls",
        [
            ("/pause  /resume", "Pause or resume the stream"),
            ("/skip [n]", "Skip current or to queue position n"),
            ("/stop  /end", "Stop playback and clear the queue"),
            ("/mute  /unmute", "Mute the assistant in the VC"),
            ("/volume 1-200", "Set stream volume"),
            ("/seek 30  /seekback 30", "Seek forwards or backwards"),
            ("/loop off|1-10", "Repeat the current track"),
            ("/shuffle", "Shuffle the queue"),
            ("/queue", "Show the queue"),
            ("/remove <n>", "Remove a queued track"),
            ("/speed 0.5-2.0", "Change playback speed"),
        ],
    ),
    "admin": (
        "👮 Admin",
        [
            ("/ban  /unban  /kick", "Remove troublesome members"),
            ("/mute  /tmute 30m", "Silence a member, optionally timed"),
            ("/warn  /warns  /resetwarn", "Warning system"),
            ("/warnlimit  /warnmode", "Configure warn escalation"),
            ("/purge  /del", "Bulk-delete messages"),
            ("/pin  /unpin  /unpinall", "Manage pinned messages"),
            ("/promote  /demote  /title", "Manage admins"),
            ("/lock  /unlock  /locks", "Restrict message types"),
            ("/setflood  /floodmode", "Anti-flood protection"),
            ("/blacklist  /blacklistmode", "Blocked words"),
            ("/auth  /unauth  /authusers", "Let non-admins use controls"),
        ],
    ),
    "tools": (
        "🧰 Group Tools",
        [
            ("/save  /get  /notes  /clear", "Saved notes — fetch with #name"),
            ("/filter  /stop  /filters", "Auto-reply triggers"),
            ("/setrules  /rules", "Chat rules"),
            ("/setwelcome  /welcome on|off", "Greeting messages"),
            ("/setgoodbye  /goodbye on|off", "Farewell messages"),
            ("/afk [reason]", "Mark yourself away"),
            ("/report", "Alert the admins (reply)"),
            ("/disable  /enable", "Turn commands off per chat"),
            ("/admins", "List chat admins"),
            ("/id", "Show chat and user IDs"),
        ],
    ),
    "extras": (
        "✨ Extras",
        [
            ("/lyrics [song]", "Fetch lyrics, expandable inline"),
            ("/suggest", "Smart recommendations"),
            ("/fav  /favs  /unfav", "Favourite tracks"),
            ("/playlists  /saveplaylist", "Save & replay your own playlists"),
            ("/download <song>", "Download as MP3"),
            ("/song <query>", "Search & send audio file"),
            ("/history", "Recently played"),
            ("/top", "Most played tracks"),
            ("/stats", "Bot statistics"),
            ("/ping", "Latency & system load"),
        ],
    ),
}


def _help_root_card() -> RichCard:
    return (
        RichCard()
        .heading([_icon("📖"), b(f"{config.bot_name} — Help")], size=1)
        .para([plain("A music bot and a group manager in one. Pick a category below.")])
        .table(
            ["Category", "What's inside"],
            [
                ["🎵 Playback", "play, video, radio, playlists"],
                ["🎛 Controls", "pause, skip, seek, loop, volume"],
                ["👮 Admin", "ban, mute, warn, purge, locks"],
                ["🧰 Group Tools", "notes, filters, rules, welcome"],
                ["✨ Extras", "lyrics, favourites, stats"],
            ],
        )
        .footer("Tip: /settings opens the interactive control panel.")
    )


def _category_card(key: str) -> RichCard:
    title, commands = HELP_CATEGORIES[key]
    card = RichCard().heading([b(title)], size=1)
    card.table(["Command", "Description"], [[c(cmd), desc] for cmd, desc in commands])
    card.footer("Arguments in <angle brackets> are required, [square] optional.")
    return card


@router.message(Command("help", "commands"))
async def cmd_help(message: Message) -> None:
    cats = [(k, v[0]) for k, v in HELP_CATEGORIES.items()]
    await send_card(message, _help_root_card(), reply_markup=help_menu_kb(cats))


@router.callback_query(F.data == "help:root")
async def cb_help_root(query: CallbackQuery) -> None:
    cats = [(k, v[0]) for k, v in HELP_CATEGORIES.items()]
    await _edit(query, _help_root_card(), help_menu_kb(cats))


@router.callback_query(F.data == "help:close")
async def cb_help_close(query: CallbackQuery) -> None:
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.answer("Closed")


@router.callback_query(F.data.startswith("help:"))
async def cb_help_category(query: CallbackQuery) -> None:
    key = query.data.split(":", 1)[1]
    if key not in HELP_CATEGORIES:
        await query.answer()
        return
    await _edit(query, _category_card(key), help_back_kb())


async def _edit(query: CallbackQuery, card: RichCard, markup) -> None:
    try:
        await query.message.edit_text(
            card.to_html(), parse_mode="HTML", reply_markup=markup,
            link_preview_options=_no_preview(),
        )
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            logger.debug("Help edit failed: %s", exc)
    await query.answer()


def _no_preview():
    from aiogram.types import LinkPreviewOptions

    return LinkPreviewOptions(is_disabled=True)


# ─────────────────────────────────────────────────────────────────────────────
# Settings panel
# ─────────────────────────────────────────────────────────────────────────────

async def _settings_card(chat_id: int, title: str) -> RichCard:
    doc = await database.get_chat(chat_id)
    warns = await moderation.warn_settings(chat_id)
    locks = await moderation.locks(chat_id)
    flood = await moderation.flood_limit(chat_id)
    welcome = await moderation.welcome_settings(chat_id)
    lang = str(doc.get("language", config.default_language))

    return (
        RichCard()
        .heading([_icon("⚙️"), b(f"Settings — {title}")], size=1)
        .table(
            ["Setting", "Value"],
            [
                ["🌐 Language", c(translator.display_name(lang))],
                ["🎧 Audio quality", c(str(doc.get("audio_quality", config.audio_quality)))],
                ["🎬 Video quality", c(str(doc.get("video_quality", config.video_quality)))],
                ["🎛 Controls", c("admins" if doc.get("control_admins_only", True) else "everyone")],
                ["▶️ Play access", c("admins" if doc.get("play_admins_only", config.admins_only) else "everyone")],
                ["⚠️ Warn limit", c(f"{warns['limit']} → {warns['action']}")],
                ["🔒 Locks active", c(str(sum(1 for v in locks.values() if v)))],
                ["🌊 Anti-flood", c(str(flood) if flood else "off")],
                ["👋 Welcome", c("on" if welcome["enabled"] else "off")],
                ["🧹 Clean mode", c("on" if doc.get("clean_mode", False) else "off")],
            ],
        )
        .footer("Tap a button to change a section.")
    )


@router.message(Command("settings", "config"))
async def cmd_settings(message: Message, bot: Bot) -> None:
    if not is_group(message):
        await send_html(
            message,
            "⚙️ <b>Settings are per-group.</b>\nRun /settings inside a group you administrate.",
        )
        return
    if not message.from_user or not await is_admin_or_auth(bot, message.chat.id, message.from_user.id):
        await send_html(message, "🚫 <b>Admins only.</b>")
        return
    card = await _settings_card(message.chat.id, message.chat.title or "this chat")
    await send_card(message, card, reply_markup=settings_root_kb())


async def _guard_cb(query: CallbackQuery, bot: Bot) -> bool:
    if not query.message or not query.from_user:
        return False
    if not await is_admin_or_auth(bot, query.message.chat.id, query.from_user.id):
        await query.answer("Admins only.", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "gs:root")
async def cb_settings_root(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    card = await _settings_card(query.message.chat.id, query.message.chat.title or "this chat")
    await _edit(query, card, settings_root_kb())


@router.callback_query(F.data == "gs:close")
async def cb_settings_close(query: CallbackQuery) -> None:
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.answer("Closed")


@router.callback_query(F.data == "gs:lang")
async def cb_settings_lang(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    current = str(
        await database.get_chat_value(query.message.chat.id, "language", config.default_language)
    )
    langs = [(code, translator.display_name(code)) for code in translator.languages]
    card = (
        RichCard()
        .heading([_icon("🌐"), b("Language")], size=1)
        .para([plain("Choose the language for bot replies in this chat.")])
        .para([b("Current: "), c(translator.display_name(current))])
        .footer(f"{len(langs)} languages available — translations welcome!")
    )
    await _edit(query, card, language_kb(langs, current))


@router.callback_query(F.data.startswith("lang:set:"))
async def cb_lang_set(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    code = query.data.split(":")[-1]
    if not translator.has(code):
        await query.answer("Unknown language.", show_alert=True)
        return
    await database.set_chat_value(query.message.chat.id, "language", code)
    await query.answer(f"Language set to {translator.display_name(code)}")
    langs = [(c_, translator.display_name(c_)) for c_ in translator.languages]
    lang = await get_lang(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("🌐"), b("Language")], size=1)
        .para([plain(lang("settings.language_set", language=translator.display_name(code)))])
        .footer("Music and moderation replies now use this language.")
    )
    await _edit(query, card, language_kb(langs, code))


@router.callback_query(F.data == "gs:locks")
async def cb_settings_locks(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    locks = await moderation.locks(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("🔒"), b("Locks")], size=1)
        .para([plain("Tap a type to lock or unlock it for non-admins.")])
        .footer("🔒 = locked  •  🔓 = allowed")
    )
    await _edit(query, card, locks_kb(locks, 0))


@router.callback_query(F.data.startswith("lock:page:"))
async def cb_lock_page(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    page = int(query.data.split(":")[-1])
    locks = await moderation.locks(query.message.chat.id)
    try:
        await query.message.edit_reply_markup(reply_markup=locks_kb(locks, page))
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data.startswith("lock:toggle:"))
async def cb_lock_toggle(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    _, _, key, page = query.data.split(":")
    locks = await moderation.locks(query.message.chat.id)
    new_state = not locks.get(key, False)
    await moderation.set_lock(query.message.chat.id, key, new_state)
    locks[key] = new_state
    await query.answer(f"{key}: {'locked' if new_state else 'unlocked'}")
    try:
        await query.message.edit_reply_markup(reply_markup=locks_kb(locks, int(page)))
    except Exception:
        pass


@router.callback_query(F.data == "gs:warns")
async def cb_settings_warns(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    settings = await moderation.warn_settings(query.message.chat.id)
    warned = await moderation.all_warned(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("⚠️"), b("Warnings")], size=1)
        .table(
            ["Setting", "Value"],
            [["Limit", c(str(settings["limit"]))], ["Action at limit", c(settings["action"])],
             ["Users with warns", c(str(len(warned)))]],
        )
        .para([plain("Change with "), c("/warnlimit 5"), plain(" and "), c("/warnmode ban"), plain(".")])
        .footer("Warns reset automatically once the limit action fires.")
    )
    await _edit(query, card, settings_back_kb())


@router.callback_query(F.data == "gs:flood")
async def cb_settings_flood(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    limit = await moderation.flood_limit(query.message.chat.id)
    action = await moderation.flood_action(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("🌊"), b("Anti-flood")], size=1)
        .para([plain("Automatically acts on users sending many messages in a row.")])
        .table(["Setting", "Value"], [["Limit", c(str(limit) if limit else "off")], ["Action", c(action)]])
        .para([plain("Set with "), c("/setflood 10"), plain(" • "), c("/floodmode mute")])
    )
    await _edit(query, card, settings_back_kb())


@router.callback_query(F.data == "gs:welcome")
async def cb_settings_welcome(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    s = await moderation.welcome_settings(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("👋"), b("Greetings")], size=1)
        .checklist(
            [
                (s["enabled"], "Welcome new members"),
                (s["goodbye_enabled"], "Say goodbye when members leave"),
                (s["clean"], "Delete the previous welcome message"),
            ]
        )
    )
    if s["text"]:
        card.details("Current welcome message", [s["text"]])
    card.para([plain("Placeholders: "), c("{mention} {first} {chatname} {count}")])
    card.footer("/setwelcome <text> • /welcome on|off")
    await _edit(
        query,
        card,
        toggle_kb(
            [
                ("Welcome", "gsw:toggle:welcome", s["enabled"]),
                ("Goodbye", "gsw:toggle:goodbye", s["goodbye_enabled"]),
            ]
        ),
    )


@router.callback_query(F.data.startswith("gsw:toggle:"))
async def cb_welcome_toggle(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    which = query.data.split(":")[-1]
    s = await moderation.welcome_settings(query.message.chat.id)
    if which == "welcome":
        new = not s["enabled"]
        await moderation.toggle_welcome(query.message.chat.id, new)
    else:
        new = not s["goodbye_enabled"]
        await moderation.toggle_goodbye(query.message.chat.id, new)
    await query.answer(f"{which.title()}: {'on' if new else 'off'}")
    await cb_settings_welcome(query, bot)


@router.callback_query(F.data == "gs:blacklist")
async def cb_settings_blacklist(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    words = await moderation.blacklist_words(query.message.chat.id)
    mode = await moderation.blacklist_mode(query.message.chat.id)
    card = (
        RichCard()
        .heading([_icon("🚫"), b("Word Blacklist")], size=1)
        .para([b("Mode: "), c(mode), plain(f"  •  {len(words)} word(s)")])
    )
    if words:
        card.details("Blacklisted words", [", ".join(words[:80])])
    card.para([plain("Manage with "), c("/blacklist word"), plain(" and "), c("/unblacklist word")])
    card.footer("Supports * wildcards, e.g. spam*")
    await _edit(query, card, settings_back_kb())


@router.callback_query(F.data == "gs:clean")
async def cb_settings_clean(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    chat_id = query.message.chat.id
    doc = await database.get_chat(chat_id)
    clean = bool(doc.get("clean_mode", False))
    cmd_clean = bool(doc.get("clean_commands", False))
    card = (
        RichCard()
        .heading([_icon("🧹"), b("Clean Mode")], size=1)
        .para([plain("Keeps the chat tidy by removing the bot's own clutter.")])
        .checklist(
            [
                (clean, f"Delete status and error replies after {config.clean_mode_seconds // 60} min"),
                (cmd_clean, "Delete /play commands as soon as they run"),
            ]
        )
        .footer("Now Playing cards are always kept — the player buttons live on them.")
    )
    await _edit(
        query,
        card,
        toggle_kb(
            [
                ("Clean player messages", "gsc:toggle:clean_mode", clean),
                ("Clean command messages", "gsc:toggle:clean_commands", cmd_clean),
            ]
        ),
    )


@router.callback_query(F.data.startswith("gsc:toggle:"))
async def cb_clean_toggle(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    key = query.data.split(":")[-1]
    current = bool(await database.get_chat_value(query.message.chat.id, key, False))
    await database.set_chat_value(query.message.chat.id, key, not current)
    await query.answer(f"{'Enabled' if not current else 'Disabled'}")
    await cb_settings_clean(query, bot)


@router.callback_query(F.data == "gs:music")
async def cb_settings_music(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    chat_id = query.message.chat.id
    doc = await database.get_chat(chat_id)
    play_admins = bool(doc.get("play_admins_only", config.admins_only))
    ctrl_admins = bool(doc.get("control_admins_only", True))
    announce = bool(doc.get("announce_tracks", True))
    card = (
        RichCard()
        .heading([_icon("🎵"), b("Music Settings")], size=1)
        .table(
            ["Setting", "Value"],
            [
                ["Audio quality", c(str(doc.get("audio_quality", config.audio_quality)))],
                ["Video quality", c(str(doc.get("video_quality", config.video_quality)))],
                ["Who can play", c("admins" if play_admins else "everyone")],
                ["Who can control", c("admins" if ctrl_admins else "everyone")],
                ["Duration limit", c(f"{config.duration_limit_min} min")],
                ["Announce tracks", c("on" if announce else "off")],
            ],
        )
        .footer("Authorised users (/auth) bypass the admin restriction.")
    )
    await _edit(
        query,
        card,
        toggle_kb(
            [
                ("Play: admins only", "gsm:toggle:play_admins_only", play_admins),
                ("Controls: admins only", "gsm:toggle:control_admins_only", ctrl_admins),
                ("Announce each track", "gsm:toggle:announce_tracks", announce),
            ]
        ),
    )


@router.callback_query(F.data.startswith("gsm:toggle:"))
async def cb_music_toggle(query: CallbackQuery, bot: Bot) -> None:
    if not await _guard_cb(query, bot):
        return
    key = query.data.split(":")[-1]
    # play_admins_only follows the global default; every other toggle is on.
    default = config.admins_only if key == "play_admins_only" else True
    current = bool(await database.get_chat_value(query.message.chat.id, key, default))
    await database.set_chat_value(query.message.chat.id, key, not current)
    await query.answer("Updated")
    await cb_settings_music(query, bot)


@router.callback_query(F.data == "noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()


# ─────────────────────────────────────────────────────────────────────────────
# Language command shortcut
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("lang", "language", "setlang"))
async def cmd_lang(message: Message, bot: Bot) -> None:
    if not is_group(message):
        await send_html(message, "🌐 <b>Set the language inside a group.</b>")
        return
    args = (message.text or "").split()[1:]
    current = str(
        await database.get_chat_value(message.chat.id, "language", config.default_language)
    )
    if args:
        if not message.from_user or not await is_admin_or_auth(bot, message.chat.id, message.from_user.id):
            await send_html(message, "🚫 <b>Admins only.</b>")
            return
        code = args[0].lower()
        if not translator.has(code):
            await send_html(
                message,
                f"❌ <b>Unknown language.</b> Available: <code>{', '.join(translator.languages)}</code>",
            )
            return
        await database.set_chat_value(message.chat.id, "language", code)
        await send_html(message, f"🌐 <b>Language set to {translator.display_name(code)}.</b>")
        return

    langs = [(code, translator.display_name(code)) for code in translator.languages]
    card = (
        RichCard()
        .heading([_icon("🌐"), b("Language")], size=1)
        .para([b("Current: "), c(translator.display_name(current))])
        .footer("Pick one below or use /lang <code>")
    )
    await send_card(message, card, reply_markup=language_kb(langs, current))


# ─────────────────────────────────────────────────────────────────────────────
# Auth users
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("auth", "unauth"))
async def cmd_auth(message: Message, bot: Bot) -> None:
    from bot.utils.guards import extract_target, is_admin, mention_id

    if not is_group(message) or not message.from_user:
        return
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await send_html(message, "🚫 <b>Admins only.</b>")
        return
    uid, name, _ = await extract_target(message, bot)
    if uid is None:
        await send_html(message, "⚠️ <b>Reply to a user or pass a @username.</b>")
        return
    adding = (message.text or "").lstrip("/").lower().startswith("auth")
    if adding:
        ok = await database.add_auth_user(message.chat.id, uid, name)
        await send_html(
            message,
            f"✅ <b>{mention_id(uid, name)} can now use control commands.</b>"
            if ok else "⚠️ <b>Already authorised.</b>",
        )
    else:
        ok = await database.remove_auth_user(message.chat.id, uid)
        await send_html(
            message,
            f"✅ <b>{mention_id(uid, name)} is no longer authorised.</b>"
            if ok else "⚠️ <b>They weren't authorised.</b>",
        )


@router.message(Command("authusers", "authlist"))
async def cmd_authusers(message: Message, bot: Bot) -> None:
    if not is_group(message):
        return
    users = await database.auth_users(message.chat.id)
    if not users:
        await send_html(message, "👥 <b>No authorised users in this chat.</b>")
        return
    card = (
        RichCard()
        .heading([_icon("👥"), b("Authorised Users")], size=1)
        .para([plain("These members can use control commands without being admins.")])
        .bullets([f"{v.get('name') or uid} — {c(uid)}" for uid, v in users.items()])
        .footer("/unauth <user> to revoke")
    )
    await send_card(message, card)
