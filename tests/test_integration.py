"""Integration checks: routing, rich rendering, moderation logic, storage.

Run with:  .venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point storage at a scratch dir before anything imports the config.
_TMP = tempfile.mkdtemp(prefix="umb-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("SESSION_STRING", "teststring")
os.environ.setdefault("WEB_ENABLED", "false")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def test_config_parsing():
    from bot.config import _parse_bool, _parse_int_list, _parse_str_list

    assert _parse_int_list("1, 2 3") == [1, 2, 3]
    assert _parse_int_list("-100123, bad, 7") == [-100123, 7]
    assert _parse_int_list("") == []
    assert _parse_bool("yes") is True
    assert _parse_bool("0") is False
    assert _parse_bool(None, True) is True
    assert _parse_str_list("/ !") == ["/", "!"]


def test_config_validates_clean():
    from bot.config import config

    assert config.validate() == []


# ─────────────────────────────────────────────────────────────────────────────
# Rich rendering
# ─────────────────────────────────────────────────────────────────────────────

def test_rich_card_renders_both_forms():
    from bot.utils.rich import RichCard, a, b, c, plain

    card = (
        RichCard()
        .heading([plain("▶️ "), b("Now Playing")], size=1)
        .para([b("Title"), plain(" — "), c("3:21")])
        .quote(["line one", "line two"], credit="tester")
        .expandable("a long body of lyrics")
        .bullets(["one", "two"], ordered=True)
        .checklist([(True, "done"), (False, "todo")])
        .table(["A", "B"], [["1", "2"]])
        .divider()
        .footer("footer text")
    )

    html = card.to_html()
    assert "<b>▶️ Now Playing</b>" in html
    assert "<blockquote expandable>" in html
    assert "☑ done" in html and "☐ todo" in html
    assert "<b>A</b>" in html
    assert html.count("<b><b>") == 0, "styles must not double-wrap"

    rich = card.to_rich_message()
    assert rich.blocks and len(rich.blocks) == 9
    payload = rich.model_dump_json(exclude_none=True)
    assert '"type":"heading"' in payload
    assert '"type":"expandable_blockquote"' in payload
    assert '"is_checked":true' in payload


def test_rich_escapes_html_injection():
    from bot.utils.rich import RichCard, plain

    card = RichCard().para([plain("<script>alert(1)</script>")])
    assert "<script>" not in card.to_html()
    assert "&lt;script&gt;" in card.to_html()


def test_nested_span_styles_collapse():
    from bot.utils.rich import a, b

    span = b(a("Title", "https://example.com"))
    assert span.style == "url"
    assert span.url == "https://example.com"
    assert span.to_html() == '<a href="https://example.com">Title</a>'


def test_html_output_respects_telegram_limit():
    from bot.utils.rich import RichCard, plain

    card = RichCard()
    for _ in range(400):
        card.para([plain("x" * 40)])
    assert len(card.to_html()) <= 4096


# ─────────────────────────────────────────────────────────────────────────────
# Routing — the real dispatcher, no duplicate command owners
# ─────────────────────────────────────────────────────────────────────────────

def test_no_duplicate_command_handlers():
    """Two routers claiming the same command silently shadows one of them."""
    import inspect
    import re

    from bot.handlers import (
        admin, advanced, controls, dashboard, extras, grouptools,
        misc, moderation as mod, play, settings as settings_h, start,
    )

    modules = {
        "admin": admin, "advanced": advanced, "controls": controls,
        "dashboard": dashboard, "extras": extras, "grouptools": grouptools,
        "misc": misc, "moderation": mod, "play": play,
        "settings": settings_h, "start": start,
    }

    owners: dict[str, list[str]] = {}
    for name, module in modules.items():
        src = inspect.getsource(module)
        for match in re.finditer(r"Command\(([^)]*)\)", src):
            for cmd in re.findall(r'"([a-z_]+)"', match.group(1)):
                owners.setdefault(cmd, []).append(name)

    dupes = {c: sorted(set(m)) for c, m in owners.items() if len(set(m)) > 1}
    # /stop is intentionally shared: grouptools only claims it with an argument.
    dupes.pop("stop", None)
    assert not dupes, f"Commands registered in multiple routers: {dupes}"


@pytest.mark.asyncio
async def test_dispatcher_builds_and_resolves_updates():
    from aiogram import Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.handlers import (
        admin, advanced, callbacks, controls, dashboard, extras, grouptools,
        inline_mode, misc, moderation as mod, play, settings as settings_h, start,
    )
    from bot.middlewares.enforcement import EnforcementMiddleware
    from bot.middlewares.gatekeeper import GatekeeperMiddleware

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(GatekeeperMiddleware())
    dp.message.middleware(EnforcementMiddleware())
    for router in (
        start.router, settings_h.router, mod.router, admin.router,
        controls.router, play.router, advanced.router, misc.router,
        extras.router, dashboard.router, inline_mode.router,
        callbacks.router, grouptools.router,
    ):
        dp.include_router(router)

    used = dp.resolve_used_update_types()
    assert "message" in used and "callback_query" in used and "inline_query" in used


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_database_json_roundtrip():
    from bot.services.database import database

    await database.connect()
    assert database.backend == "json"

    await database.set_chat_value(-100777, "language", "es")
    assert await database.get_chat_value(-100777, "language", "en") == "es"
    assert await database.get_chat_value(-100777, "missing", "fallback") == "fallback"

    await database.touch_chat(-100777, "Test Chat")
    assert -100777 in await database.known_chats()


@pytest.mark.asyncio
async def test_favorites_and_playlists():
    from bot.services.database import database

    track = {"id": "abc", "title": "Song", "artist": "Artist", "url": "u", "duration": 100}
    assert await database.add_favorite(42, track) is True
    assert await database.add_favorite(42, track) is False, "duplicates must be rejected"
    assert len(await database.get_favorites(42)) == 1
    assert (await database.remove_favorite(42, 0))["title"] == "Song"
    assert await database.remove_favorite(42, 5) is None

    assert await database.save_playlist(42, "Mix", [track, track]) == 2
    assert "Mix" in await database.get_playlists(42)
    assert await database.delete_playlist(42, "Mix") is True


@pytest.mark.asyncio
async def test_stats_and_top_tracks():
    from bot.services.database import database

    for _ in range(3):
        await database.record_play(-100888, 7, {"id": "t1", "title": "Hit", "url": "u"})
    await database.record_play(-100888, 7, {"id": "t2", "title": "Other", "url": "u2"})

    top = await database.top_tracks(-100888)
    assert top[0]["title"] == "Hit" and top[0]["count"] == 3
    counters = await database.global_counters()
    assert counters["total_plays"] >= 4


@pytest.mark.asyncio
async def test_gban_and_blacklist():
    from bot.services.database import database

    await database.ban_user(999, "spam")
    assert await database.is_banned(999) is True
    assert await database.unban_user(999) is True
    assert await database.is_banned(999) is False

    await database.blacklist_chat(-1234, "abuse")
    assert await database.is_blacklisted(-1234) is True
    await database.whitelist_chat(-1234)
    assert await database.is_blacklisted(-1234) is False


# ─────────────────────────────────────────────────────────────────────────────
# Moderation logic
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_warn_escalation():
    from bot.services.moderation import moderation

    chat = -100999
    await moderation.set_warn_limit(chat, 3)
    await moderation.set_warn_action(chat, "ban")

    for expected in (1, 2, 3):
        count, limit, action = await moderation.add_warn(chat, 55, f"reason {expected}")
        assert count == expected and limit == 3 and action == "ban"

    assert (await moderation.get_warns(chat, 55))["count"] == 3
    assert await moderation.remove_one_warn(chat, 55) == 2
    assert await moderation.reset_warns(chat, 55) is True
    assert (await moderation.get_warns(chat, 55))["count"] == 0


@pytest.mark.asyncio
async def test_blacklist_word_matching_is_word_boundary_safe():
    from bot.services.moderation import moderation

    chat = -100111
    await moderation.add_blacklist_word(chat, "spam")
    assert await moderation.match_blacklist(chat, "this is SPAM here") == "spam"
    # Must not fire inside a longer word.
    assert await moderation.match_blacklist(chat, "spamming is different") is None
    assert await moderation.match_blacklist(chat, "nothing wrong") is None

    await moderation.add_blacklist_word(chat, "bad*")
    assert await moderation.match_blacklist(chat, "that is badword") == "bad*"


@pytest.mark.asyncio
async def test_filters_match_whole_words():
    from bot.services.moderation import moderation

    chat = -100222
    await moderation.save_filter(chat, "hello", {"text": "Hi!", "type": "text"})
    assert (await moderation.match_filter(chat, "hello there"))["text"] == "Hi!"
    assert await moderation.match_filter(chat, "helloween") is None

    await moderation.save_filter(chat, "good morning", {"text": "☀️", "type": "text"})
    assert (await moderation.match_filter(chat, "a good morning to you"))["text"] == "☀️"


@pytest.mark.asyncio
async def test_locks_all_toggles_every_type():
    from bot.services.moderation import LOCK_TYPES, moderation

    chat = -100333
    await moderation.set_lock(chat, "all", True)
    locks = await moderation.locks(chat)
    assert all(locks.get(k) for k in LOCK_TYPES if k != "all")
    await moderation.set_lock(chat, "sticker", False)
    assert await moderation.is_locked(chat, "sticker") is False
    assert await moderation.is_locked(chat, "photo") is True


@pytest.mark.asyncio
async def test_notes_and_disabled_commands():
    from bot.services.moderation import moderation

    chat = -100444
    await moderation.save_note(chat, "Rules", {"text": "be nice", "type": "text"})
    assert (await moderation.get_note(chat, "rules"))["text"] == "be nice"
    assert "rules" in await moderation.list_notes(chat)
    assert await moderation.delete_note(chat, "RULES") is True

    assert await moderation.disable_command(chat, "ping") is True
    assert await moderation.is_command_disabled(chat, "ping") is True
    assert await moderation.disable_command(chat, "ban") is False, "ban must not be disableable"
    assert await moderation.enable_command(chat, "ping") is True


# ─────────────────────────────────────────────────────────────────────────────
# Queue
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_operations():
    from bot.services.queue import LoopMode, QueueManager

    q = QueueManager(max_size=4)
    chat = 1
    for n in range(4):
        await q.add(chat, {"title": f"t{n}", "duration": 60})
    with pytest.raises(ValueError):
        await q.add(chat, {"title": "overflow"})

    assert await q.size(chat) == 4
    assert (await q.next_track(chat))["title"] == "t0"
    assert (await q.get_current(chat))["title"] == "t0"

    await q.add_front(chat, {"title": "priority"})
    assert (await q.next_track(chat))["title"] == "priority"

    assert await q.move(chat, 0, 1) is True
    assert await q.move(chat, 99, 0) is False
    assert await q.drop_before(chat, 1) == 1
    assert await q.clear(chat) >= 0


@pytest.mark.asyncio
async def test_queue_loop_modes():
    from bot.services.queue import LoopMode, QueueManager

    q = QueueManager()
    chat = 2
    await q.add(chat, {"title": "a"})
    await q.add(chat, {"title": "b"})
    await q.next_track(chat)  # current = a

    await q.set_loop(chat, LoopMode.SINGLE)
    assert (await q.next_track(chat))["title"] == "a", "single loop repeats current"

    # skip must break out of a single loop
    assert (await q.skip(chat))["title"] == "b"
    assert await q.get_loop(chat) == LoopMode.OFF

    await q.set_loop_count(chat, 2)
    assert (await q.next_track(chat))["title"] == "b"
    assert (await q.next_track(chat))["title"] == "b"
    assert await q.get_loop_count(chat) == 0


@pytest.mark.asyncio
async def test_volume_clamped():
    from bot.services.queue import QueueManager

    q = QueueManager()
    assert await q.set_volume(3, 500) == 200
    assert await q.set_volume(3, -10) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Guards & helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_duration_parsing():
    from bot.utils.guards import humanize_seconds, parse_duration, split_duration_reason

    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("1w") == 604800
    assert parse_duration("5s") is None, "under the 30s floor"
    assert parse_duration("nonsense") is None

    secs, reason = split_duration_reason("30m being rude")
    assert secs == 1800 and reason == "being rude"
    secs, reason = split_duration_reason("just rude")
    assert secs is None and reason == "just rude"

    assert humanize_seconds(3661) == "1h 1m"


def test_welcome_placeholders():
    from types import SimpleNamespace

    from bot.utils.guards import fill_placeholders

    user = SimpleNamespace(
        id=7, first_name="Ann", last_name="Lee", full_name="Ann Lee", username="annlee"
    )
    chat = SimpleNamespace(title="My Group")
    out = fill_placeholders("Hi {first} ({username}) to {chatname}! id={id}", user, chat)
    assert out == "Hi Ann (@annlee) to My Group! id=7"

    evil = SimpleNamespace(
        id=8, first_name="<b>x", last_name="", full_name="<b>x", username=None
    )
    assert "<b>x" not in fill_placeholders("{first}", evil, chat)


def test_seek_parsing():
    from bot.handlers.controls import _parse_seek

    assert _parse_seek("90") == 90
    assert _parse_seek("1:30") == 90
    assert _parse_seek("1:01:00") == 3660
    assert _parse_seek("2m30s") == 150
    assert _parse_seek("abc") is None


def test_progress_bar():
    from bot.utils.cards import fmt_duration, progress_bar

    assert fmt_duration(None) == "—"
    assert fmt_duration(3725) == "1:02:05"
    assert fmt_duration(75) == "1:15"
    assert "LIVE" in progress_bar(0, None)

    # Constant width regardless of position, so the card does not reflow as
    # the track plays.
    for elapsed in (0, 25, 50, 75, 100):
        assert len(progress_bar(elapsed, 100, width=10)) == 10

    # The knob has to actually track position, or the bar is decoration.
    assert progress_bar(0, 100, width=10).index("◉") == 0
    assert progress_bar(100, 100, width=10).index("◉") == 9
    assert progress_bar(0, 100, width=10).index("◉") < progress_bar(
        50, 100, width=10
    ).index("◉") < progress_bar(100, 100, width=10).index("◉")

    # Out-of-range elapsed values must clamp, not overflow the bar.
    assert len(progress_bar(-5, 100, width=10)) == 10
    assert len(progress_bar(9999, 100, width=10)) == 10

    from bot.utils.cards import meter

    assert meter(0, width=10) == "▱" * 10
    assert meter(100, width=10) == "▰" * 10
    assert meter(50, width=10).count("▰") == 5


# ─────────────────────────────────────────────────────────────────────────────
# i18n
# ─────────────────────────────────────────────────────────────────────────────

def test_translations_fall_back_to_english():
    from bot.services.i18n import Lang, translator

    assert "en" in translator.languages
    assert translator.get("play.now_playing", "en") == "Now Playing"
    # Spanish has this key…
    assert translator.get("play.now_playing", "es") == "Reproduciendo Ahora"
    # …and unknown keys degrade to the key itself rather than raising.
    assert translator.get("does.not.exist", "es") == "does.not.exist"
    # Missing key in a partial locale falls back to English.
    assert translator.get("queue.position", "es") == "Position"

    lang = Lang("en")
    assert lang("err.queue_full", max=50) == "Queue is full (max 50 tracks)."
    # Missing placeholders must not raise.
    assert "{" in lang("err.queue_full") or "max" in lang("err.queue_full")


def test_all_locales_are_valid_json_and_subset_of_english():
    import json
    from pathlib import Path

    from bot.services.i18n import LOCALES_DIR

    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    for path in LOCALES_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data, f"{path.name} is empty"
        unknown = set(data) - set(english)
        assert not unknown, f"{path.name} has keys missing from en.json: {unknown}"


def test_lock_types_and_disableable_are_sane():
    from bot.services.moderation import DISABLEABLE, LOCK_TYPES

    assert "all" in LOCK_TYPES and "sticker" in LOCK_TYPES
    # Moderation commands must never be disableable.
    for dangerous in ("ban", "mute", "warn", "promote", "purge"):
        assert dangerous not in DISABLEABLE


def test_no_shadowed_imports_between_card_modules():
    """cards.py and formatters.py export same-named builders with different
    signatures — importing both in one module silently shadows one of them."""
    import pathlib
    import re

    offenders = []
    for path in pathlib.Path("bot").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        from_cards = set()
        from_fmt = set()
        for m in re.finditer(r"from bot\.utils\.cards import ([^\n(]+)", src):
            from_cards |= {x.strip() for x in m.group(1).split(",") if x.strip()}
        for m in re.finditer(r"from bot\.utils\.formatters import ([^\n(]+)", src):
            from_fmt |= {x.strip() for x in m.group(1).split(",") if x.strip()}
        clash = from_cards & from_fmt
        if clash:
            offenders.append(f"{path}: {sorted(clash)}")
    assert not offenders, "Shadowed card imports: " + "; ".join(offenders)


def test_every_handler_module_exposes_a_router():
    import importlib

    from aiogram import Router

    names = [
        "admin", "advanced", "callbacks", "controls", "dashboard", "extras",
        "grouptools", "inline_mode", "misc", "moderation", "play", "settings", "start",
    ]
    for name in names:
        module = importlib.import_module(f"bot.handlers.{name}")
        assert isinstance(getattr(module, "router", None), Router), f"{name} has no router"


def test_details_summary_is_not_double_wrapped():
    """heading/footer/details all wrap in a style tag; a caller-supplied bold
    span must not produce invalid <b><b>…</b></b> nesting."""
    from bot.utils.rich import RichCard, b, plain

    card = (
        RichCard()
        .heading([plain("x "), b("bold")])
        .details([plain("s "), b("summary")], ["line"])
        .footer([plain("f "), b("end")])
    )
    html = card.to_html()
    for tag in ("b", "i"):
        assert f"<{tag}><{tag}>" not in html
        assert f"</{tag}></{tag}>" not in html


def test_start_keyboard_uses_new_button_features():
    from bot.keyboards.inline import start_kb

    kb = start_kb("mybot")
    flat = [btn for row in kb.inline_keyboard for btn in row]

    add_me = next(b for b in flat if "ᴀᴅᴅ ᴍᴇ" in b.text)
    assert "startgroup=true" in add_me.url
    assert "manage_video_chats" in add_me.url, "must request VC rights up front"

    assert any(b.switch_inline_query_chosen_chat for b in flat), "chat-picker button missing"
    assert any(b.copy_text for b in flat), "copy-to-clipboard share button missing"
    assert any(b.callback_data == "menu:features" for b in flat)

    # Every button must carry exactly one action, or Telegram rejects the markup.
    for btn in flat:
        actions = [
            btn.url, btn.callback_data, btn.copy_text,
            btn.switch_inline_query, btn.switch_inline_query_current_chat,
            btn.switch_inline_query_chosen_chat, btn.web_app, btn.login_url,
        ]
        assert sum(a is not None for a in actions) == 1, f"{btn.text} has ambiguous action"


def test_start_keyboard_omits_unconfigured_links():
    """Owner/support/updates buttons must vanish rather than render dead links."""
    import importlib

    import bot.config
    import bot.keyboards.inline as kbmod

    original = bot.config.config
    try:
        bot.config.config = importlib.import_module("dataclasses").replace(
            original, owner_username="", support_chat="", support_channel=""
        )
        kbmod.config = bot.config.config
        flat = [b for row in kbmod.start_kb("mybot").inline_keyboard for b in row]
        assert not any(t in b.text for b in flat for t in ("ᴏᴡɴᴇʀ", "sᴜᴘᴘᴏʀᴛ", "ᴜᴘᴅᴀᴛᴇs"))
    finally:
        bot.config.config = original
        kbmod.config = original


def test_feature_cards_all_render():
    from bot.utils.cards import feature_card

    for section in ("overview", "music", "group", "power", "bogus"):
        card = feature_card(section)
        html = card.to_html()
        assert len(html) <= 4096, f"{section} too long: {len(html)}"
        assert "<b><b>" not in html
        card.to_rich_message().model_dump_json(exclude_none=True)


def test_every_keyboard_callback_has_a_handler():
    """A button whose callback_data nobody handles just spins forever."""
    import importlib
    import inspect
    import re

    import bot.keyboards.inline as kbmod

    # Collect literal callback_data values produced by the keyboard factories.
    emitted = set(re.findall(r'callback_data="([a-z_]+:[a-z_]+)"', inspect.getsource(kbmod)))

    handled: set[str] = set()
    prefixes: list[str] = []
    for name in ("start", "callbacks", "settings", "dashboard", "advanced", "extras"):
        src = inspect.getsource(importlib.import_module(f"bot.handlers.{name}"))
        handled |= set(re.findall(r'F\.data == "([^"]+)"', src))
        prefixes += re.findall(r'F\.data\.startswith\("([^"]+)"\)', src)

    orphans = {
        cb for cb in emitted
        if cb not in handled and not any(cb.startswith(p) for p in prefixes)
    }
    assert not orphans, f"Buttons with no handler: {sorted(orphans)}"


# ─────────────────────────────────────────────────────────────────────────────
# Download cache
# ─────────────────────────────────────────────────────────────────────────────

def test_cache_key_is_stable_and_mode_aware():
    from bot.services.downloads import cache_key

    a = {"id": "abc123", "title": "Song", "source": "Youtube"}
    b = {"id": "abc123", "title": "Different search text", "source": "youtube"}
    assert cache_key(a) == cache_key(b), "same track id must hit the same cache slot"
    assert cache_key(a) != cache_key(a, video=True), "audio and video are separate files"

    # No id: fall back to url, then title — never collide on empty.
    assert cache_key({"url": "u1"}) != cache_key({"url": "u2"})
    assert len(cache_key({"id": "x" * 500})) <= 180


def test_safe_filename_strips_path_and_control_chars():
    from bot.services.downloads import safe_filename

    assert "/" not in safe_filename("AC/DC - Back in Black", "mp3")
    assert safe_filename("../../etc/passwd", "mp3") == "....etcpasswd.mp3"
    assert safe_filename("", "mp3") == "track.mp3"
    assert safe_filename("x" * 200, "mp3").endswith(".mp3")
    assert len(safe_filename("x" * 200, "mp3")) <= 64


@pytest.mark.asyncio
async def test_file_id_roundtrip_through_database():
    from bot.services.downloads import cache_key, cached_file_id, remember_file_id

    track = {"id": "trk1", "title": "Cached Song", "url": "u", "source": "youtube"}
    assert await cached_file_id(track) is None
    await remember_file_id(track, "BQACAgIAAx0EF4k")
    assert await cached_file_id(track) == "BQACAgIAAx0EF4k"
    # The video variant must not pick up the audio file_id.
    assert await cached_file_id(track, video=True) is None


@pytest.mark.asyncio
async def test_stale_file_id_is_cleared_not_reused():
    """A rotted file_id must be blanked so the next call re-downloads."""
    from bot.services.database import database
    from bot.services.downloads import cache_key, cached_file_id, remember_file_id

    track = {"id": "trk2", "title": "Rotten", "url": "u", "source": "youtube"}
    await remember_file_id(track, "old_id")
    await database.cache_track(cache_key(track), {**track, "file_id": ""})
    assert await cached_file_id(track) is None


# ─────────────────────────────────────────────────────────────────────────────
# Vote skip
# ─────────────────────────────────────────────────────────────────────────────

def test_voteskip_threshold_scales_with_listeners():
    from bot.services.voteskip import MIN_VOTES, voteskip

    # Tiny rooms still need the floor, never more than the people present.
    assert voteskip.needed(2, 0.5) == MIN_VOTES
    assert voteskip.needed(3, 0.5) == 2
    assert voteskip.needed(11, 0.5) == 5     # 10 humans, half
    assert voteskip.needed(21, 0.5) == 10
    assert voteskip.needed(11, 1.0) == 10    # unanimous
    # Never demand more votes than there are humans to cast them.
    for listeners in range(1, 30):
        for ratio in (0.1, 0.5, 0.9, 1.0):
            assert voteskip.needed(listeners, ratio) <= max(1, listeners - 1) or \
                   voteskip.needed(listeners, ratio) == MIN_VOTES


def test_voteskip_counts_once_per_user():
    from bot.services.voteskip import voteskip

    chat, track = -100555, {"id": "t1", "title": "A"}
    voteskip.reset(chat)

    assert voteskip.add_vote(chat, 1, track) == (1, True)
    assert voteskip.add_vote(chat, 1, track) == (1, False), "double vote must not count"
    assert voteskip.add_vote(chat, 2, track) == (2, True)
    voteskip.reset(chat)
    assert voteskip.current(chat, track) is None


def test_voteskip_resets_when_the_track_changes():
    """Votes against one song must not carry over to the next."""
    from bot.services.voteskip import voteskip

    chat = -100556
    voteskip.reset(chat)
    voteskip.add_vote(chat, 1, {"id": "song_a"})
    voteskip.add_vote(chat, 2, {"id": "song_a"})
    assert voteskip.current(chat, {"id": "song_a"}).voters == {1, 2}

    # Different track -> the old vote is discarded.
    assert voteskip.current(chat, {"id": "song_b"}) is None
    assert voteskip.add_vote(chat, 1, {"id": "song_b"}) == (1, True)


def test_voteskip_vote_expires():
    import time

    from bot.services.voteskip import VOTE_TTL, voteskip

    chat = -100557
    voteskip.reset(chat)
    voteskip.add_vote(chat, 1, {"id": "t"})
    vote = voteskip._votes[chat]
    vote.started = time.time() - VOTE_TTL - 1
    assert voteskip.current(chat, {"id": "t"}) is None


@pytest.mark.asyncio
async def test_voteskip_ratio_is_clamped():
    from bot.services.voteskip import voteskip

    chat = -100558
    assert await voteskip.set_ratio(chat, 5.0) == 1.0
    assert await voteskip.set_ratio(chat, 0.0) == 0.1
    assert await voteskip.set_ratio(chat, 0.6) == 0.6
    assert await voteskip.ratio(chat) == 0.6


# ─────────────────────────────────────────────────────────────────────────────
# Thumbnails
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_thumbnail_renders_and_is_cached():
    from bot.services.thumbnails import now_playing_image

    track = {
        "id": "thumbtest", "title": "A Reasonably Long Track Title That Wraps",
        "artist": "Some Artist", "duration": 240, "requester": "Tester", "thumbnail": "",
    }
    first = await now_playing_image(track, elapsed=60, bot_name="TestBot")
    assert first is not None and first.exists()
    assert first.stat().st_size > 5000, "suspiciously small render"

    mtime = first.stat().st_mtime
    again = await now_playing_image(track, elapsed=62, bot_name="TestBot")
    assert again == first and again.stat().st_mtime == mtime, "should reuse the cached render"


@pytest.mark.asyncio
async def test_thumbnail_survives_a_broken_cover_url():
    from bot.services.thumbnails import now_playing_image

    path = await now_playing_image(
        {"id": "brokencover", "title": "T", "artist": "A", "duration": 100,
         "thumbnail": "http://127.0.0.1:1/nope.jpg"},
        elapsed=0, bot_name="TestBot",
    )
    assert path is not None, "a dead cover URL must not stop the card rendering"


@pytest.mark.asyncio
async def test_thumbnail_handles_live_and_missing_duration():
    from bot.services.thumbnails import now_playing_image

    for duration in (None, 0, "bogus"):
        path = await now_playing_image(
            {"id": f"dur{duration}", "title": "Live Stream", "artist": "",
             "duration": duration, "thumbnail": ""},
            elapsed=0, bot_name="TestBot",
        )
        assert path is not None, f"duration={duration!r} broke the renderer"


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_when_accepts_the_documented_formats():
    from bot.services.scheduler import parse_when

    # 24h, 12h, compact, and with/without minutes
    for text in ("07:00", "7:00", "0700", "7am", "7:30 pm", "19:45"):
        assert parse_when(text, 0) is not None, f"{text} should parse"

    # daily, either word order
    for text in ("daily 07:00", "07:00 daily"):
        epoch, daily = parse_when(text, 0)
        assert epoch == 0 and daily == "07:00"

    # relative
    for text in ("in 30m", "45 minutes", "2h", "in 3 hours"):
        epoch, daily = parse_when(text, 0)
        assert daily == "" and epoch > 0

    for bad in ("bogus", "25:00", "7:99", "", "tomorrow", "in 0m", "in 999h"):
        assert parse_when(bad, 0) is None, f"{bad!r} should be rejected"


def test_parse_when_respects_timezone_and_rolls_forward():
    import time

    from bot.services.scheduler import parse_when

    # A one-shot clock time is always in the future, never in the past.
    for offset in (-480, 0, 330, 840):
        epoch, _ = parse_when("07:00", offset)
        assert epoch > time.time(), f"offset {offset} produced a past time"
        assert epoch - time.time() <= 24 * 3600 + 60


def test_twelve_hour_conversion_is_correct():
    from datetime import datetime, timezone

    from bot.services.scheduler import parse_when

    def hour_utc(text):
        epoch, _ = parse_when(text, 0)
        return datetime.fromtimestamp(epoch, timezone.utc).hour

    assert hour_utc("12am") == 0, "12am is midnight"
    assert hour_utc("12pm") == 12, "12pm is noon"
    assert hour_utc("1am") == 1
    assert hour_utc("11pm") == 23


def test_daily_job_next_epoch_is_always_ahead():
    import time

    from bot.services.scheduler import Job

    for offset in (-300, 0, 330):
        for clock in ("00:00", "07:00", "12:30", "23:59"):
            job = Job(id="x", chat_id=1, user_id=1, query="q",
                      daily_at=clock, tz_offset_min=offset)
            assert job.next_epoch() > time.time()


@pytest.mark.asyncio
async def test_scheduler_persists_and_expires_jobs():
    import time

    from bot.services.scheduler import scheduler

    chat = -100811
    await scheduler.clear(chat)

    one_shot = await scheduler.add(chat, 1, "lofi", "in 5m", 0)
    daily = await scheduler.add(chat, 1, "jazz", "daily 07:00", 330)
    assert one_shot and daily

    # Survives a reload straight from storage.
    assert len(await scheduler.list_jobs(chat)) == 2

    # Force the one-shot due and run a tick by hand.
    jobs = await scheduler._load(chat)
    for job in jobs:
        if job.id == one_shot.id:
            job.run_at = time.time() - 1
    await scheduler._save(chat, jobs)

    due = [j.id for j in await scheduler.due_jobs()]
    assert one_shot.id in due

    for job in await scheduler.due_jobs():
        await scheduler._complete(job)

    remaining = {j.id for j in await scheduler.list_jobs(chat)}
    assert one_shot.id not in remaining, "one-shot must be removed after firing"
    assert daily.id in remaining, "daily must survive firing"
    await scheduler.clear(chat)


@pytest.mark.asyncio
async def test_scheduler_enforces_the_per_chat_cap():
    from bot.services.scheduler import MAX_JOBS_PER_CHAT, scheduler

    chat = -100812
    await scheduler.clear(chat)
    for n in range(MAX_JOBS_PER_CHAT):
        assert await scheduler.add(chat, 1, f"t{n}", "in 10m", 0)
    with pytest.raises(ValueError):
        await scheduler.add(chat, 1, "one too many", "in 10m", 0)
    assert await scheduler.clear(chat) == MAX_JOBS_PER_CHAT


@pytest.mark.asyncio
async def test_scheduler_listing_is_soonest_first():
    from bot.services.scheduler import scheduler

    chat = -100813
    await scheduler.clear(chat)
    await scheduler.add(chat, 1, "later", "in 3h", 0)
    await scheduler.add(chat, 1, "sooner", "in 10m", 0)
    jobs = await scheduler.list_jobs(chat)
    assert [j.query for j in jobs] == ["sooner", "later"]
    await scheduler.clear(chat)


@pytest.mark.asyncio
async def test_scheduler_remove_is_exact():
    from bot.services.scheduler import scheduler

    chat = -100814
    await scheduler.clear(chat)
    job = await scheduler.add(chat, 1, "x", "in 10m", 0)
    assert await scheduler.remove(chat, "nosuchid") is False
    assert await scheduler.remove(chat, job.id.upper()) is True, "ids are case-insensitive"
    assert await scheduler.list_jobs(chat) == []


def test_scheduler_jobs_are_json_serialisable():
    """Jobs round-trip through the JSON store, so no exotic field types."""
    import json
    from dataclasses import asdict

    from bot.services.scheduler import Job

    job = Job(id="a1", chat_id=-1, user_id=2, query="q", daily_at="07:00", tz_offset_min=330)
    restored = Job(**json.loads(json.dumps(asdict(job))))
    assert restored == job


# ─────────────────────────────────────────────────────────────────────────────
# Real download pipeline (skipped without ffmpeg)
# ─────────────────────────────────────────────────────────────────────────────

def _have_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
@pytest.mark.asyncio
async def test_real_download_produces_a_valid_mp3(tmp_path):
    """End-to-end: fetch a real file, transcode it, verify the bitrate."""
    import subprocess
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    # Generate a genuine 3-second tone to serve.
    source = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3", "-ac", "2", str(source)],
        check=True,
    )

    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    from bot.services.downloads import cleanup, download_track

    try:
        track = {
            "id": "tonetest", "title": "Tone Test", "artist": "Generator",
            "url": f"http://127.0.0.1:{port}/tone.wav", "source": "generic",
        }
        path = await download_track(track)
        try:
            assert path.exists() and path.suffix == ".mp3"
            assert path.stat().st_size > 1000

            probe = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(path), "-f", "null", "-"],
                capture_output=True, text=True,
            ).stderr
            assert "Audio: mp3" in probe, f"not a valid mp3:\n{probe[-400:]}"
        finally:
            await cleanup(path)
        assert not path.parent.exists(), "temp directory leaked"
    finally:
        server.shutdown()


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
@pytest.mark.asyncio
async def test_failed_download_leaves_no_temp_directories():
    from bot.config import DOWNLOAD_DIR
    from bot.services.downloads import DownloadError, download_track

    before = len(list(DOWNLOAD_DIR.iterdir())) if DOWNLOAD_DIR.exists() else 0
    with pytest.raises(DownloadError):
        # Port 9 is the discard service — nothing will answer.
        await download_track({"id": "dead", "title": "Dead", "url": "http://127.0.0.1:9/x.mp3"})
    after = len(list(DOWNLOAD_DIR.iterdir())) if DOWNLOAD_DIR.exists() else 0
    assert after == before, "a failed download leaked its temp directory"


@pytest.mark.asyncio
async def test_missing_ffmpeg_gives_a_clear_error(monkeypatch):
    import bot.services.downloads as downloads

    monkeypatch.setattr(downloads.shutil, "which", lambda _: None)
    with pytest.raises(downloads.DownloadError) as excinfo:
        await downloads.download_track({"id": "x", "title": "X", "url": "http://example.com/a.mp3"})
    assert "ffmpeg" in str(excinfo.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Deployment / dependency guards
#
# These reproduce the Render startup crash:
#   ImportError: cannot import name 'GroupcallForbidden' from 'pyrogram.errors'
# caused by installing official pyrogram (unreleased since 2023) instead of a
# maintained fork. py-tgcalls imports that symbol at module load.
# ─────────────────────────────────────────────────────────────────────────────

def test_pyrogram_fork_provides_the_symbols_pytgcalls_needs():
    from pyrogram.errors import GroupcallForbidden, GroupcallInvalid  # noqa: F401

    import pyrogram

    assert pyrogram.__version__ >= "2.1", (
        f"pyrogram {pyrogram.__version__} looks like official pyrogram, which "
        "cannot drive voice calls. Install kurigram."
    )


def test_pytgcalls_client_module_imports():
    """The exact import chain that crashed the deploy."""
    from pytgcalls.mtproto.pyrogram_client import PyrogramClient  # noqa: F401


def test_pytgcalls_constructs_against_the_installed_client():
    """Reproduces main.py's stream_manager.setup() crash site."""
    import pyrogram
    from pytgcalls import PyTgCalls

    client = pyrogram.Client("regression", api_id=1, api_hash="x", in_memory=True)
    assert PyTgCalls(client) is not None


def test_requirements_do_not_pull_in_official_pyrogram():
    """`py-tgcalls[pyrogram]` would reinstall official pyrogram over the fork —
    both occupy the same `pyrogram` package name, so the extra silently wins."""
    import pathlib

    text = pathlib.Path("requirements.txt").read_text()
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    assert any(ln.lower().startswith("kurigram") for ln in lines), "kurigram must be pinned"
    for line in lines:
        assert not line.lower().startswith("pyrogram"), f"official pyrogram pinned: {line}"
        assert "[pyrogram]" not in line.lower(), (
            f"the [pyrogram] extra reinstalls official pyrogram: {line}"
        )


def test_session_string_format_matches_official_pyrogram():
    """Swapping to the fork must not invalidate existing SESSION_STRINGs."""
    from pyrogram.storage.storage import Storage

    # These struct formats are what encode/decode a session string. If they
    # ever diverge from official pyrogram's, every user must log in again.
    assert Storage.SESSION_STRING_FORMAT == ">BI?256sQ?"
    assert Storage.OLD_SESSION_STRING_FORMAT == ">B?256sI?"
    assert Storage.OLD_SESSION_STRING_FORMAT_64 == ">B?256sQ?"


def test_assistant_client_accepts_our_constructor_arguments():
    import inspect

    from pyrogram import Client

    params = inspect.signature(Client.__init__).parameters
    for name in ("api_id", "api_hash", "session_string", "in_memory"):
        assert name in params, f"Client() lost the {name} parameter"


def test_python_version_pin_is_present_and_sane():
    """Render ignores runtime.txt now; it reads .python-version."""
    import pathlib

    pin = pathlib.Path(".python-version")
    assert pin.exists(), ".python-version is required — Render ignores runtime.txt"
    version = pin.read_text().strip()
    major, minor = (int(x) for x in version.split(".")[:2])
    assert major == 3 and 10 <= minor <= 13, (
        f"{version}: native deps (ntgcalls, TgCrypto) need a version with wheels"
    )
    assert not pathlib.Path("runtime.txt").exists(), (
        "runtime.txt is ignored by Render and contradicts .python-version"
    )


def test_preflight_rejects_a_pyrogram_without_groupcall_errors(monkeypatch):
    """The guard must exit with guidance, not a bare ImportError."""
    import builtins
    import importlib

    main = importlib.import_module("main")
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pyrogram.errors" and "GroupcallForbidden" in (fromlist or ()):
            raise ImportError("cannot import name 'GroupcallForbidden'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as excinfo:
        main._preflight()
    assert excinfo.value.code == 1


def test_ensure_ffmpeg_falls_back_to_the_bundled_binary(monkeypatch):
    """Hosts without apt still need a working ffmpeg on PATH."""
    import importlib
    import shutil as shutil_mod
    import subprocess

    main = importlib.import_module("main")

    # Pretend no system ffmpeg exists, but only for the lookup inside
    # _ensure_ffmpeg — the assertion below needs the real shutil.which.
    real_which = shutil_mod.which
    monkeypatch.setattr(main.shutil, "which", lambda name: None)

    assert main._ensure_ffmpeg() is True
    monkeypatch.undo()
    resolved = real_which("ffmpeg", path=os.environ["PATH"])
    assert resolved, "ffmpeg was not placed on PATH"
    out = subprocess.run([resolved, "-version"], capture_output=True, text=True)
    assert out.returncode == 0 and "ffmpeg version" in out.stdout


# ──────────────────────────────────────────────────────────────────────────
# YouTube extraction: JS runtime, cookies/proxy plumbing, error reporting
#
# The live deploy hit "Failed to extract any player response" on every query.
# Root cause was not the query: without a JavaScript runtime yt-dlp falls back
# to a single player client, and datacenter IPs get refused by it.
# ──────────────────────────────────────────────────────────────────────────


def test_a_js_runtime_is_available_to_ytdlp():
    """Without one, yt-dlp uses a reduced client set that servers fail."""
    import importlib

    main = importlib.import_module("main")
    main._ensure_node()

    from bot.services.music import _js_runtimes

    assert _js_runtimes(), (
        "No JS runtime resolved. yt-dlp will fall back to a single player "
        "client and YouTube extraction will fail on this host."
    )


def test_js_runtime_widens_the_youtube_client_set():
    """The actual mechanism behind the fix, pinned so it cannot regress.

    Without a runtime yt-dlp requests only _DEFAULT_JSLESS_CLIENTS. One client
    means one chance and no fallback, which is why every query failed.
    """
    import importlib

    main = importlib.import_module("main")
    main._ensure_node()

    from yt_dlp.YoutubeDL import YoutubeDL

    from bot.services.music import _ydl_common

    def clients_for(opts):
        ydl = YoutubeDL({"quiet": True, "no_warnings": True, **opts})
        ie = ydl.get_info_extractor("Youtube")
        ie.set_downloader(ydl)
        ie.initialize()
        return ie._get_requested_clients("https://www.youtube.com/watch?v=x", {}, False)

    without = clients_for({})
    with_runtime = clients_for(_ydl_common())

    assert len(with_runtime) > len(without), (
        f"JS runtime did not widen the client set: {without} -> {with_runtime}"
    )


def test_streaming_extraction_applies_cookies_and_proxy(monkeypatch, tmp_path):
    """These were read from the environment but only used for /song.

    Streaming and search silently ignored them, so the documented escape hatch
    for an IP block did not work where it was needed most.
    """
    import dataclasses

    from bot.services import music

    import time

    cookies = tmp_path / "cookies.txt"
    expires = int(time.time()) + 90 * 86400
    cookies.write_text(
        "# Netscape HTTP Cookie File\n"
        f".youtube.com\tTRUE\t/\tFALSE\t{expires}\tSID\tvalue\n"
    )

    monkeypatch.setattr(
        music,
        "config",
        dataclasses.replace(
            music.config, cookies_file=str(cookies), ytdlp_proxy="socks5://127.0.0.1:9050"
        ),
    )

    opts = music._ydl_common()
    assert opts["cookiefile"] == str(cookies)
    assert opts["proxy"] == "socks5://127.0.0.1:9050"


def test_download_options_carry_the_js_runtime():
    """/song downloads hit the same YouTube gate as streaming."""
    import inspect

    from bot.services import downloads

    source = inspect.getsource(downloads)
    assert "js_runtimes" in source, "download path lacks a JS runtime"


def test_blocked_errors_are_told_apart_from_empty_results():
    """An IP block and a typo need different fixes, so they need different text."""
    from bot.services.music import looks_blocked

    assert looks_blocked(
        "ERROR: [youtube] IT6svoR9M-0: Failed to extract any player response"
    )
    assert looks_blocked("Sign in to confirm you're not a bot")
    assert looks_blocked("Your IP is likely being blocked by Youtube")
    assert not looks_blocked("Unsupported URL: https://example.com/x")
    assert not looks_blocked("")


def test_extraction_failure_is_recorded_for_the_user_message(monkeypatch):
    """last_error() is what lets handlers explain the real cause."""
    import asyncio

    from bot.services import music

    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **k):
            raise RuntimeError("Failed to extract any player response")

    monkeypatch.setattr(music.yt_dlp, "YoutubeDL", lambda *a, **k: _Boom())

    result = asyncio.run(music._run_ytdl({}, "ytsearch1:anything"))
    assert result is None
    assert music.looks_blocked(music.last_error())


def test_play_handler_reports_a_block_distinctly():
    """The user-visible payoff: no more 'could not find that media' for a block."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play)
    assert "looks_blocked" in source
    assert "BLOCKED_HINT" in source


def test_documented_extraction_env_vars_are_actually_read():
    """A documented variable no code reads is a bug, not documentation."""
    from bot.config import config

    env_example = (Path(__file__).resolve().parent.parent / ".env.example").read_text()

    for key, attr in (
        ("COOKIES_FILE", "cookies_file"),
        ("YTDLP_PROXY", "ytdlp_proxy"),
        ("YTDLP_JS_RUNTIME", "js_runtime"),
    ):
        assert key in env_example, f"{key} is not documented in .env.example"
        assert hasattr(config, attr), f"config has no field for {key}"


def test_extra_player_clients_widen_the_fallback_chain():
    """Each YouTube client is an independent chance past an IP refusal.

    yt-dlp's default is two clients even with a JS runtime. On a flagged
    datacenter IP that is very little to fall back on, so extra clients are
    requested explicitly.
    """
    import importlib

    main = importlib.import_module("main")
    main._ensure_node()

    from yt_dlp.YoutubeDL import YoutubeDL

    from bot.services.music import _ydl_common

    def clients_for(opts):
        ydl = YoutubeDL({"quiet": True, "no_warnings": True, **opts})
        ie = ydl.get_info_extractor("Youtube")
        ie.set_downloader(ydl)
        ie.initialize()
        return ie._get_requested_clients("https://www.youtube.com/watch?v=x", {}, False)

    stock = clients_for({})
    ours = clients_for(_ydl_common())

    assert len(ours) >= len(stock) + 3, f"expected a wider chain, got {ours}"
    # The default set must still be included, not replaced.
    assert set(stock).issubset(set(ours)), f"{stock} not preserved in {ours}"


def test_youtube_clients_env_var_overrides_the_defaults(monkeypatch):
    """Operators need to pin clients without a redeploy of the code."""
    import dataclasses

    from bot.services import music

    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, youtube_clients="tv, ios")
    )
    assert music._player_clients() == ["tv", "ios"]

    opts = music._ydl_common()
    assert opts["extractor_args"]["youtube"]["player_client"] == ["tv", "ios"]


def test_cookies_can_be_supplied_without_a_file(monkeypatch, tmp_path):
    """PaaS hosts have nowhere to put a cookie file, and committing one leaks it."""
    import base64
    import dataclasses

    from bot.services import music

    jar = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"

    monkeypatch.setattr(music, "DATA_DIR", tmp_path)
    for encoding in (jar, base64.b64encode(jar.encode()).decode(), jar.replace("\n", "\\n")):
        monkeypatch.setattr(
            music,
            "config",
            dataclasses.replace(music.config, cookies_file="", cookies_data=encoding),
        )
        path = music.materialize_cookies()
        assert path, "no cookie jar produced"
        written = Path(path).read_text()
        assert written.startswith("# Netscape"), f"not a valid jar: {written[:40]!r}"
        assert "SID\tsecret" in written
        assert music._ydl_common()["cookiefile"] == path


def test_a_real_cookie_file_wins_over_inline_data(monkeypatch, tmp_path):
    import dataclasses

    from bot.services import music

    real = tmp_path / "real.txt"
    real.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(music, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        music,
        "config",
        dataclasses.replace(music.config, cookies_file=str(real), cookies_data="ignored"),
    )
    assert music.materialize_cookies() == str(real)


def test_missing_cookies_are_not_an_error(monkeypatch, tmp_path):
    """No cookies configured is the normal case, not a failure."""
    import dataclasses

    from bot.services import music

    monkeypatch.setattr(music, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, cookies_file="", cookies_data="")
    )
    assert music.materialize_cookies() == ""
    assert "cookiefile" not in music._ydl_common()


def test_build_id_identifies_the_running_code(monkeypatch):
    """A stale deploy is otherwise indistinguishable from a broken fix."""
    import importlib

    main = importlib.import_module("main")

    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567890")
    assert main.build_id() == "abcdef1"

    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    assert main.build_id(), "build id should fall back to the local checkout"


def test_downloads_share_the_streaming_extraction_config():
    """/song hits the same YouTube gate, so it needs the same workarounds."""
    import inspect

    from bot.services import downloads

    source = inspect.getsource(downloads)
    assert "materialize_cookies" in source
    assert "_player_clients" in source
    assert "_js_runtimes" in source


# ──────────────────────────────────────────────────────────────────────────
# Streaming-platform links (Spotify / Apple Music / Deezer)
#
# yt-dlp ships no Spotify extractor, so these links previously failed while
# the UI advertised support for them. They are resolved to metadata instead
# and matched against a streamable source.
# ──────────────────────────────────────────────────────────────────────────


def test_platform_links_are_detected():
    from bot.services.platforms import detect

    assert detect("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT") == "spotify"
    assert detect("https://open.spotify.com/intl-de/album/1DFixLWuPkv3KT3TnV35m3") == "spotify"
    assert detect("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "spotify"
    assert detect("https://www.deezer.com/en/track/3135556") == "deezer"
    assert detect("https://music.apple.com/us/album/thriller/1440857781") == "apple"
    assert detect("https://youtube.com/watch?v=dQw4w9WgXcQ") == ""
    assert detect("") == ""


def test_drm_only_services_are_named_not_silently_failed():
    """Tidal and Amazon have no public metadata, so say so instead of retrying."""
    from bot.services.platforms import unsupported_service

    assert unsupported_service("https://tidal.com/browse/track/1") == "Tidal"
    assert unsupported_service("https://music.amazon.com/albums/B01") == "Amazon Music"
    assert unsupported_service("https://open.spotify.com/track/x") == ""


def test_spotify_id_extraction_handles_every_url_shape():
    from bot.services.platforms import _SPOTIFY_RE

    cases = [
        ("https://open.spotify.com/track/4cOdK2", "track", "4cOdK2"),
        ("https://open.spotify.com/intl-de/album/1DFix", "album", "1DFix"),
        ("spotify:playlist:37i9dQ", "playlist", "37i9dQ"),
        ("https://open.spotify.com/artist/1dfeR4H?si=abc", "artist", "1dfeR4H"),
    ]
    for url, kind, ident in cases:
        m = _SPOTIFY_RE.search(url)
        assert m, f"no match for {url}"
        assert (m.group(1) or m.group(2)).lower() == kind
        assert m.group(3) == ident


def test_resolved_builds_searchable_queries():
    from bot.services.platforms import Resolved

    r = Resolved(
        platform="spotify",
        kind="album",
        tracks=[
            {"title": "Bohemian Rhapsody", "artist": "Queen"},
            {"title": "Instrumental", "artist": ""},
            {"title": "", "artist": "Nobody"},
        ],
    )
    queries = r.queries()
    assert queries[0] == "Queen - Bohemian Rhapsody"
    assert queries[1] == "Instrumental"  # no artist, no dangling dash
    assert len(queries) == 2  # the titleless row is dropped


def test_platform_resolution_never_raises(monkeypatch):
    """A bad link must not take down the handler."""
    import asyncio

    from bot.services import platforms

    async def boom(*a, **k):
        raise RuntimeError("network on fire")

    monkeypatch.setitem(platforms._RESOLVERS, "spotify", boom)
    platforms.clear_cache()
    assert asyncio.run(platforms.resolve("https://open.spotify.com/track/x")) is None


def test_resolve_query_uses_platform_metadata(monkeypatch):
    """A Spotify URL must become "Artist - Title", not be passed to yt-dlp."""
    import asyncio

    from bot.services import music, platforms

    async def fake_resolve(url):
        return platforms.Resolved(
            platform="spotify",
            kind="track",
            tracks=[{"title": "Kill Bill", "artist": "SZA"}],
        )

    monkeypatch.setattr(platforms, "resolve", fake_resolve)
    out = asyncio.run(music.resolve_query("https://open.spotify.com/track/x"))
    assert out == "SZA - Kill Bill"

    # Non-platform queries pass through untouched.
    assert asyncio.run(music.resolve_query("just a song name")) == "just a song name"


def test_source_badges_are_recognisable():
    from bot.utils.cards import source_badge

    assert "Spotify" in source_badge("Spotify")
    assert "Spotify" in source_badge("spotify:track")
    assert "Apple Music" in source_badge("AppleMusic")
    assert "SoundCloud" in source_badge("soundcloud")
    assert "YouTube" in source_badge("Youtube")
    # Unknown sources still render something sane rather than blowing up.
    assert source_badge("wat") and source_badge("") 


def test_import_card_reports_what_actually_happened():
    from bot.services.platforms import Resolved
    from bot.utils.cards import import_card

    resolved = Resolved(
        platform="spotify",
        kind="playlist",
        title="Today's Top Hits",
        subtitle="Spotify",
        tracks=[{"title": f"Song {n}", "artist": "Artist"} for n in range(8)],
        truncated=True,
    )
    html = import_card(resolved, added=6, queued=5).to_html()
    assert "Today&#x27;s Top Hits" in html or "Today's Top Hits" in html
    assert "Spotify" in html
    assert "6" in html and "5" in html
    assert "2" in html  # 8 found, 6 added -> 2 skipped
    assert "first" in html.lower()  # truncation is disclosed


def test_now_playing_card_shows_source_and_position():
    from bot.utils.cards import now_playing_card

    html = now_playing_card(
        {
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "duration": 354,
            "source": "Spotify",
            "requester": "Arjun",
        },
        elapsed=127,
        queue_len=4,
        volume=80,
        loop_mode="all",
    ).to_html()

    assert "Bohemian Rhapsody" in html
    assert "Spotify" in html  # badge, not a bare extractor key
    assert "2:07" in html and "5:54" in html
    assert "◉" in html
    assert "Arjun" in html


def test_live_tracks_have_no_fake_progress_bar():
    from bot.utils.cards import now_playing_card

    html = now_playing_card(
        {"title": "Lofi Radio", "is_live": True, "source": "radio"}
    ).to_html()
    assert "LIVE" in html
    assert "◉" not in html  # a seek bar on a live stream is a lie


def test_every_keyboard_button_has_a_handler():
    """A button that does nothing when tapped is worse than no button."""
    import re

    root = Path(__file__).resolve().parent.parent
    emitted: set[str] = set()
    for path in (root / "bot" / "keyboards").glob("*.py"):
        emitted |= set(re.findall(r'callback_data="([a-z_]+:[a-z_]+)"', path.read_text()))

    exact: set[str] = set()
    prefixes: set[str] = set()
    for path in (root / "bot" / "handlers").glob("*.py"):
        text = path.read_text()
        exact |= set(re.findall(r'F\.data == "([^"]+)"', text))
        prefixes |= set(re.findall(r'F\.data\.startswith\("([^"]+)"\)', text))

    dead = {
        data
        for data in emitted
        if data not in exact and not any(data.startswith(p) for p in prefixes)
    }
    assert not dead, f"buttons with no handler: {sorted(dead)}"
    assert emitted, "no callback buttons found — the scan is broken"


def test_player_panel_stays_thumb_sized():
    """The transport panel used to be 14 buttons over 5 rows."""
    from bot.keyboards.inline import player_more_kb, player_panel_kb

    rows = player_panel_kb(is_playing=True).inline_keyboard
    assert len(rows) <= 3, "player panel grew back into a wall of buttons"
    assert all(len(r) <= 4 for r in rows), "a row is too wide for a phone"

    # Everything moved off the main panel must still be reachable.
    more = {b.callback_data for r in player_more_kb().inline_keyboard for b in r}
    assert {"ctrl:video", "ctrl:live", "ctrl:clear", "ctrl:suggest"} <= more
    assert "ctrl:back" in more, "no way back from the More panel"


def test_search_keyboard_is_compact():
    from bot.keyboards.inline import search_results_kb

    results = [{"id": f"v{n}", "title": f"A very long song title {n}", "duration": 200} for n in range(8)]
    rows = search_results_kb(results).inline_keyboard
    picker = [r for r in rows if r and (r[0].callback_data or "").startswith("play:")]
    assert all(len(r) <= 4 for r in picker), "picker rows too wide"
    assert sum(len(r) for r in picker) == 8, "results went missing"


# ──────────────────────────────────────────────────────────────────────────
# Search backend fallback
#
# When a host's IP is blocked by YouTube, every YouTube query fails
# identically. Falling back to a different service keeps the bot usable.
# ──────────────────────────────────────────────────────────────────────────

_BLOCK_ERROR = "ERROR: [youtube] x: Failed to extract any player response"


def _stub_backends(monkeypatch, behaviour):
    """Replace _run_ytdl with a scripted responder; returns the call log."""
    from bot.services import music

    calls: list[str] = []

    async def fake(opts, query):
        calls.append(query)
        kind = behaviour(query)
        if kind == "block":
            music._last_error = _BLOCK_ERROR
            return None
        if kind == "empty":
            music._last_error = ""
            return {"entries": []}
        music._last_error = ""
        return {"entries": [{"id": "1", "title": f"hit:{query.split(':')[0]}", "url": "u"}]}

    monkeypatch.setattr(music, "_run_ytdl", fake)
    return calls


def test_blocked_youtube_falls_back_to_another_backend(monkeypatch):
    import asyncio

    from bot.services import music

    calls = _stub_backends(
        monkeypatch, lambda q: "block" if q.startswith("ytsearch") else "ok"
    )
    info = asyncio.run(music._search_with_fallback("some song", {}))

    assert info, "fallback produced nothing"
    assert info["entries"][0]["title"] == "hit:scsearch1"
    assert [c.split(":")[0] for c in calls] == ["ytsearch1", "scsearch1"]


def test_working_youtube_is_not_second_guessed(monkeypatch):
    """The fallback must not cost an extra request on the happy path."""
    import asyncio

    from bot.services import music

    calls = _stub_backends(monkeypatch, lambda q: "ok")
    asyncio.run(music._search_with_fallback("some song", {}))
    assert len(calls) == 1, f"expected one search, got {calls}"


def test_genuine_no_results_does_not_fall_back(monkeypatch):
    """Falling back here would return an unrelated song instead of nothing."""
    import asyncio

    from bot.services import music

    calls = _stub_backends(monkeypatch, lambda q: "empty")
    assert asyncio.run(music._search_with_fallback("kjhsdfkjhsdf", {})) is None
    assert len(calls) == 1, f"should not have tried another backend: {calls}"


def test_all_backends_blocked_gives_up_cleanly(monkeypatch):
    import asyncio

    from bot.services import music

    calls = _stub_backends(monkeypatch, lambda q: "block")
    assert asyncio.run(music._search_with_fallback("song", {})) is None
    assert len(calls) == len(music.SEARCH_BACKENDS)


def test_search_backends_are_configurable(monkeypatch):
    import dataclasses

    from bot.services import music

    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, search_backends="soundcloud")
    )
    assert music._backends() == (("scsearch", "SoundCloud"),)

    # Nonsense config must not disable search entirely.
    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, search_backends="nope")
    )
    assert music._backends() == music.SEARCH_BACKENDS


def test_non_media_links_are_called_out(monkeypatch):
    """A docs URL pasted into chat should not read as 'song not found'."""
    from bot.services.music import looks_blocked, looks_unsupported

    err = "ERROR: Unsupported URL: https://render.com/docs/web-services#port-binding"
    assert looks_unsupported(err)
    assert not looks_blocked(err)
    assert not looks_unsupported(_BLOCK_ERROR)


def test_polling_clears_a_stale_webhook():
    """Overlapping deploys and leftover webhooks both cause getUpdates conflicts."""
    import inspect

    import main

    source = inspect.getsource(main.main)
    assert "delete_webhook" in source
    assert "drop_pending_updates=True" in source
    assert source.index("delete_webhook") < source.index("start_polling")


# ──────────────────────────────────────────────────────────────────────────
# Platform branding
#
# Audio comes from YouTube/SoundCloud, but the user pasted a Spotify link.
# The card must reflect what they pasted, not the search backend that
# happened to serve the bytes.
# ──────────────────────────────────────────────────────────────────────────


def _spotify_resolved(**over):
    from bot.services.platforms import Resolved

    base = dict(
        platform="spotify",
        kind="track",
        title="Bohemian Rhapsody",
        subtitle="Queen",
        artwork="https://i.scdn.co/image/album.jpg",
        url="https://open.spotify.com/track/abc",
        tracks=[
            {
                "title": "Bohemian Rhapsody",
                "artist": "Queen",
                "artwork": "https://i.scdn.co/image/album.jpg",
            }
        ],
        truncated=False,
    )
    base.update(over)
    return Resolved(**base)


def _youtube_match():
    """What a search backend hands back: right audio, wrong branding."""
    return {
        "title": "Queen - Bohemian Rhapsody (Official Video Remastered)",
        "artist": "Queen Official",
        "duration": 354,
        "source": "Youtube",
        "thumbnail": "https://i.ytimg.com/vi/xyz/hq.jpg",
    }


def test_platform_link_keeps_its_identity():
    from bot.handlers.play import _brand

    track = _youtube_match()
    _brand(track, _spotify_resolved(), 0)

    assert track["source"] == "spotify"
    assert "scdn.co" in track["thumbnail"], "album art was lost"
    assert track["title"] == "Bohemian Rhapsody", "kept the noisy YouTube title"
    assert track["artist"] == "Queen"
    assert track["origin_url"] == "https://open.spotify.com/track/abc"


def test_branded_track_renders_the_right_badge():
    from bot.handlers.play import _brand
    from bot.utils.cards import now_playing_card

    track = _youtube_match()
    _brand(track, _spotify_resolved(), 0)
    html = now_playing_card(track, elapsed=127, queue_len=0).to_html()

    assert "🟢 Spotify" in html
    assert "YouTube" not in html
    assert "Official Video" not in html


def test_branding_survives_missing_metadata():
    """Resolvers without per-track rows must not blow up or wipe good data."""
    from bot.handlers.play import _brand

    track = _youtube_match()
    _brand(track, _spotify_resolved(tracks=[], artwork=""), 5)

    assert track["source"] == "spotify"
    # No album art available, so the existing thumbnail must be kept.
    assert track["thumbnail"] == "https://i.ytimg.com/vi/xyz/hq.jpg"
    assert track["title"]  # not blanked out


def test_falls_back_to_release_artwork_when_track_has_none():
    from bot.handlers.play import _brand

    track = _youtube_match()
    resolved = _spotify_resolved(tracks=[{"title": "Song", "artist": "A"}])
    _brand(track, resolved, 0)
    assert track["thumbnail"] == "https://i.scdn.co/image/album.jpg"


def test_single_track_links_no_longer_fall_through_to_plain_search():
    """A single Spotify track used to lose its badge and art via fall-through."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play._import_platform_link)
    single = source.split("is_single")[1]
    assert "_brand" in single, "single-track path must brand the result"
    assert "play_track" in single, "single-track path must play, not fall through"


def test_empty_playlist_is_reported_not_crashed():
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play._import_platform_link)
    assert "if not queries:" in source, "empty track list must be handled"


# ──────────────────────────────────────────────────────────────────────────
# Clean mode
#
# /settings has exposed "clean mode" and "clean commands" toggles for a
# while, but nothing read them — flipping them did nothing at all.
# ──────────────────────────────────────────────────────────────────────────


class _FakeChat:
    def __init__(self, chat_type="supergroup"):
        self.id = -1001234
        self.type = chat_type


class _FakeMessage:
    def __init__(self, chat_type="supergroup"):
        self.deleted = False
        self.chat = _FakeChat(chat_type)

    async def delete(self):
        self.deleted = True


class _FakeDB:
    def __init__(self, doc):
        self.doc = doc

    async def get_chat(self, chat_id):
        return self.doc


def _with_settings(monkeypatch, doc):
    from bot.services import cleanup

    monkeypatch.setattr(cleanup, "database", _FakeDB(doc))
    return cleanup


def test_clean_commands_respects_the_toggle(monkeypatch):
    import asyncio

    cleanup = _with_settings(monkeypatch, {})
    off = _FakeMessage()
    asyncio.run(cleanup.clean_command(off))
    assert not off.deleted, "deleted a command without being asked to"

    cleanup = _with_settings(monkeypatch, {"clean_commands": True})
    on = _FakeMessage()
    asyncio.run(cleanup.clean_command(on))
    assert on.deleted


def test_private_chats_keep_their_commands(monkeypatch):
    """There is no clutter problem in a DM, and deleting there is confusing."""
    import asyncio

    cleanup = _with_settings(monkeypatch, {"clean_commands": True})
    dm = _FakeMessage("private")
    asyncio.run(cleanup.clean_command(dm))
    assert not dm.deleted


def test_clean_mode_deletes_transient_replies(monkeypatch):
    import asyncio

    cleanup = _with_settings(monkeypatch, {"clean_mode": True})

    async def run():
        msg = _FakeMessage()
        cleanup.schedule_cleanup(msg, delay=0)
        await asyncio.sleep(0.1)
        return msg

    assert asyncio.run(run()).deleted


def test_clean_mode_off_keeps_everything(monkeypatch):
    import asyncio

    cleanup = _with_settings(monkeypatch, {})

    async def run():
        msg = _FakeMessage()
        cleanup.schedule_cleanup(msg, delay=0)
        await asyncio.sleep(0.1)
        return msg

    assert not asyncio.run(run()).deleted


def test_zero_delay_is_not_mistaken_for_unset(monkeypatch):
    """`delay or default` would swallow a deliberate 0, since 0 is falsy."""
    import asyncio
    import inspect

    from bot.services import cleanup

    source = inspect.getsource(cleanup.schedule_cleanup)
    assert "delay is None" in source, "0 would fall through to the default delay"

    cleanup_mod = _with_settings(monkeypatch, {"clean_mode": True})

    async def run():
        msg = _FakeMessage()
        cleanup_mod.schedule_cleanup(msg, delay=0)
        await asyncio.sleep(0.1)
        return msg

    assert asyncio.run(run()).deleted


def test_settings_lookup_failure_never_breaks_playback(monkeypatch):
    import asyncio

    from bot.services import cleanup

    class Boom:
        async def get_chat(self, chat_id):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(cleanup, "database", Boom())
    msg = _FakeMessage()
    asyncio.run(cleanup.clean_command(msg))  # must not raise
    assert not msg.deleted


def test_shutdown_cancels_pending_deletions(monkeypatch):
    import asyncio

    cleanup = _with_settings(monkeypatch, {"clean_mode": True})

    async def run():
        msg = _FakeMessage()
        cleanup.schedule_cleanup(msg, delay=99)
        await asyncio.sleep(0)
        await cleanup.stop()
        return msg

    msg = asyncio.run(run())
    assert cleanup.pending() == 0
    assert not msg.deleted


def test_play_deletes_the_command_when_asked():
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play._resolve_and_play)
    assert "clean_command" in source
    # A failed play leaves a status message behind; it should not linger.
    assert "schedule_cleanup" in source


def test_platform_import_passes_queue_only_through():
    """A Spotify link via /cplay hit an undefined name before this."""
    import inspect

    from bot.handlers import play

    sig = inspect.signature(play._import_platform_link)
    assert "queue_only" in sig.parameters

    caller = inspect.getsource(play._resolve_and_play)
    assert "queue_only=queue_only" in caller


def test_ending_the_voice_chat_tears_down_state():
    """Otherwise the bot holds a queue for a call that no longer exists."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.on_video_chat_ended)
    assert "queue_manager.clear" in source
    assert "stream_manager.stop" in source
    # Both wrapped: a teardown that raises would strand the other half.
    assert source.count("except Exception") >= 2


def test_teardown_is_silent():
    """Ending a call is already visible; an extra card is the noise we removed."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.on_video_chat_ended)
    for noisy in ("message.answer", "send_card", "message.reply"):
        assert noisy not in source, f"teardown should not post: {noisy}"


# ──────────────────────────────────────────────────────────────────────────
# Cookie validation
#
# An expired jar behaves exactly like no jar: yt-dlp drops the dead entries
# and sends the request unauthenticated. Saying so up front turns a silent
# failure into a fixable message.
# ──────────────────────────────────────────────────────────────────────────


def _write_jar(tmp_path, name, *, expires, login=True):
    lines = ["# Netscape HTTP Cookie File"]
    if login:
        lines.append(f".youtube.com\tTRUE\t/\tFALSE\t{expires}\tSID\tv-{name}")
        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expires}\tLOGIN_INFO\tv-{name}")
    lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expires}\tPREF\tx")
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _future():
    import time

    return int(time.time()) + 90 * 86400


def _past():
    import time

    return int(time.time()) - 10 * 86400


def test_expired_cookie_jar_is_reported_not_ignored(tmp_path):
    from bot.services.music import inspect_cookies

    info = inspect_cookies(_write_jar(tmp_path, "dead.txt", expires=_past()))
    assert info["expired"] == 3
    assert info["live"] == 0
    assert info["problem"], "an unusable jar must explain itself"


def test_jar_without_login_cookies_is_rejected(tmp_path):
    """A jar of tracking cookies authenticates as nobody."""
    from bot.services.music import inspect_cookies

    info = inspect_cookies(_write_jar(tmp_path, "anon.txt", expires=_future(), login=False))
    assert info["live"] > 0, "cookies are live…"
    assert not info["authenticated"], "…but none of them is a login"
    assert "not signed in" in info["problem"]


def test_good_cookie_jar_passes(tmp_path):
    from bot.services.music import inspect_cookies

    info = inspect_cookies(_write_jar(tmp_path, "good.txt", expires=_future()))
    assert info["authenticated"]
    assert not info["problem"]
    assert info["next_expiry"]


def test_missing_and_corrupt_files_do_not_raise(tmp_path):
    from bot.services.music import inspect_cookies

    assert inspect_cookies("")["problem"]
    assert inspect_cookies(str(tmp_path / "nope.txt"))["problem"]

    junk = tmp_path / "junk.txt"
    junk.write_text("this is not a cookie file at all\n")
    assert inspect_cookies(str(junk))["problem"]


def test_cookie_pool_skips_unusable_jars(tmp_path, monkeypatch):
    import dataclasses

    from bot.services import music

    _write_jar(tmp_path, "a.txt", expires=_future())
    _write_jar(tmp_path, "b.txt", expires=_future())
    _write_jar(tmp_path, "expired.txt", expires=_past())
    _write_jar(tmp_path, "anon.txt", expires=_future(), login=False)

    monkeypatch.setattr(
        music,
        "config",
        dataclasses.replace(
            music.config, cookies_dir=str(tmp_path), cookies_file="", cookies_data=""
        ),
    )
    pool = [p.rsplit("/", 1)[-1] for p in music.cookie_pool()]
    # anon.txt has live cookies but no login: weaker, still usable, so kept.
    # expired.txt has nothing live at all, so it is dropped.
    assert "expired.txt" not in pool
    assert {"a.txt", "b.txt"} <= set(pool)


def test_cookie_pool_rotates_between_accounts(tmp_path, monkeypatch):
    """One jar carrying every request is what gets an account rate-limited."""
    import dataclasses

    from bot.services import music

    _write_jar(tmp_path, "a.txt", expires=_future())
    _write_jar(tmp_path, "b.txt", expires=_future())
    monkeypatch.setattr(
        music,
        "config",
        dataclasses.replace(
            music.config, cookies_dir=str(tmp_path), cookies_file="", cookies_data=""
        ),
    )

    seen = {music.pick_cookie_file().rsplit("/", 1)[-1] for _ in range(60)}
    assert seen == {"a.txt", "b.txt"}, f"expected rotation, saw {seen}"


def test_cookie_status_distinguishes_none_from_broken(tmp_path, monkeypatch):
    import dataclasses

    from bot.services import music

    def status(**kw):
        fields = dict(cookies_file="", cookies_data="", cookies_dir="")
        fields.update(kw)
        monkeypatch.setattr(
            music, "config", dataclasses.replace(music.config, **fields)
        )
        return music.cookie_status()

    assert status() == "none"

    dead = _write_jar(tmp_path, "dead.txt", expires=_past())
    assert "UNUSABLE" in status(cookies_file=dead)

    good = _write_jar(tmp_path, "good.txt", expires=_future())
    assert status(cookies_file=good).startswith("loaded")


def test_extraction_uses_the_pool_not_a_single_file():
    import inspect

    from bot.services import music

    source = inspect.getsource(music._ydl_common)
    assert "pick_cookie_file()" in source, "must rotate, not always use one jar"


def test_player_shows_a_progress_row_when_duration_is_known():
    from bot.keyboards.inline import player_panel_kb

    kb = player_panel_kb(True, False, elapsed=127, duration=354)
    top = kb.inline_keyboard[0]
    assert len(top) == 1, "progress row should span the panel"
    assert "2:07" in top[0].text and "5:54" in top[0].text
    assert "◉" in top[0].text


def test_live_streams_get_no_fake_progress_row():
    """A live stream has no end, so a bar would be a lie."""
    from bot.keyboards.inline import player_panel_kb

    kb = player_panel_kb(True, False, elapsed=90, duration=0)
    assert "◉" not in kb.inline_keyboard[0][0].text

    plain = player_panel_kb(True, False)
    assert len(plain.inline_keyboard) == 3


def test_progress_row_shows_zero_not_a_dash_at_start():
    """fmt_duration renders 0 as an em dash, which is wrong for 0:00."""
    from bot.keyboards.inline import player_panel_kb

    kb = player_panel_kb(True, False, elapsed=0, duration=200)
    assert kb.inline_keyboard[0][0].text.startswith("0:00")


def test_progress_row_does_not_displace_the_controls():
    from bot.keyboards.inline import player_panel_kb

    kb = player_panel_kb(True, False, elapsed=10, duration=100)
    glyphs = [b.text for b in kb.inline_keyboard[1]]
    assert glyphs == ["⏮", "⏸", "⏭", "⏹"]
    assert len(kb.inline_keyboard) == 4
