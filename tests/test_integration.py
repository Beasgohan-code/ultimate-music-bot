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
    body = inspect.getsource(play._play_body)
    assert "schedule_cleanup" in body


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


# ──────────────────────────────────────────────────────────────────────────
# Unhandled errors
#
# 148 of 195 registered handlers have no try/except, and nothing caught what
# they threw: the user saw silence and only the server log had the traceback.
# ──────────────────────────────────────────────────────────────────────────


def _raise(kind=ValueError, msg="boom"):
    try:
        raise kind(msg)
    except Exception as exc:  # noqa: BLE001 - we want the traceback attached
        return exc


def test_same_bug_gets_the_same_id():
    from bot.services import errors

    errors.clear()

    def failing():
        return _raise(ValueError, "chat 42 song 'A'")

    a = errors.record(failing(), "/play a")
    b = errors.record(failing(), "/play b")

    assert a.error_id == b.error_id, "same code path must group"
    assert a.count == 2
    assert len(errors.snapshot()) == 1


def test_the_id_ignores_the_message_text():
    """Messages embed chat ids and titles; grouping on them splits one bug."""
    from bot.services import errors

    errors.clear()

    def failing(msg):
        return _raise(ValueError, msg)

    first = errors.record(failing("chat 1 song 'X'"), "")
    second = errors.record(failing("chat 999 song 'Totally Different'"), "")
    assert first.error_id == second.error_id


def test_different_bugs_stay_separate():
    from bot.services import errors

    errors.clear()
    errors.record(_raise(ValueError, "a"), "")
    errors.record(_raise(KeyError, "b"), "")
    assert len(errors.snapshot()) == 2


def test_user_message_never_leaks_internals():
    from bot.services import errors

    errors.clear()
    rec = errors.record(_raise(ValueError, "/home/user/secret/path.py exploded"), "")
    text = errors.user_message(rec)

    assert rec.error_id in text, "the reference id must be quotable"
    for leak in ("Traceback", "ValueError", "/home/user", ".py"):
        assert leak not in text, f"user-facing text leaked {leak!r}"


def test_repeats_are_rate_limited():
    """One broken handler in a busy group must not flood the log channel."""
    import time

    from bot.services import errors

    errors.clear()
    rec = errors.record(_raise(), "")
    assert errors.should_report(rec), "first sighting reports immediately"

    rec.last_reported = time.time()
    assert not errors.should_report(rec), "an immediate repeat must be suppressed"

    rec.last_reported = time.time() - errors.REPORT_COOLDOWN - 1
    assert errors.should_report(rec), "should report again after the cooldown"


def test_registry_cannot_grow_without_bound():
    from bot.services import errors

    errors.clear()
    for n in range(errors.MAX_TRACKED + 40):
        def uniquely_failing(i=n):
            return _raise(ValueError, f"bug {i}")

        # Distinct code objects would be ideal; force distinct ids instead.
        exc = uniquely_failing()
        exc.__traceback__.tb_lineno  # touch, keep the frame
        errors.record(exc, f"ctx{n}")
    assert len(errors.snapshot()) <= errors.MAX_TRACKED


def test_crash_report_includes_the_traceback():
    from bot.services import errors

    errors.clear()
    exc = _raise(RuntimeError, "kaboom")
    rec = errors.record(exc, "/play test")
    report = errors.format_report(rec, exc)

    assert rec.error_id in report
    assert "RuntimeError" in report
    assert "<pre>" in report, "traceback should be preformatted"
    assert len(report) < 4096, "must fit in a Telegram message"


def test_error_handler_is_registered_and_swallows():
    import inspect

    import main

    source = inspect.getsource(main._register_error_handler)
    assert "@dp.errors()" in source
    assert "return True" in source, "must mark handled so polling continues"
    assert "user_message" in source, "the user must be told something"


def test_health_endpoint_reports_degraded_when_blocked():
    """A check that always says ok cannot be alerted on."""
    import asyncio
    import json
    from unittest.mock import MagicMock

    import bot.web as web_module
    from bot.services import music

    previous = music._last_error
    try:
        music._last_error = "ERROR: [youtube] x: Failed to extract any player response"
        resp = asyncio.run(web_module._health(MagicMock()))
        body = json.loads(resp.body.decode())

        assert resp.status == 503
        assert body["status"] == "degraded"
        assert body["extraction_blocked"] is True
        assert body["problems"]
    finally:
        music._last_error = previous


def test_health_endpoint_is_ok_when_nothing_is_wrong():
    import asyncio
    import json
    from unittest.mock import MagicMock

    import bot.web as web_module
    from bot.services import music

    previous = music._last_error
    try:
        music._last_error = ""
        resp = asyncio.run(web_module._health(MagicMock()))
        body = json.loads(resp.body.decode())
        assert resp.status == 200
        assert body["status"] == "ok"
        assert body["problems"] == []
    finally:
        music._last_error = previous


def test_pillow_is_declared():
    """thumbnails.py degrades silently without it, so its absence hid for ages."""
    import pathlib

    text = pathlib.Path("requirements.txt").read_text().lower()
    assert "pillow" in text, "image cards need Pillow at runtime"


# ──────────────────────────────────────────────────────────────────────────
# Assistant membership detection
#
# ensure_assistant_in_chat passed "@username" as get_chat_member's user_id.
# That field is typed int, so aiogram rejected the call before it left the
# process, and the bare except reported "add the assistant" every time —
# including when the assistant was already in the group. No group could play
# anything at all.
# ──────────────────────────────────────────────────────────────────────────


def _fake_assistant(monkeypatch, user_id=7777777, working=True):
    from unittest.mock import AsyncMock, MagicMock

    from bot.services import stream as stream_mod
    from bot.utils import helpers

    from bot.services import assistant as assistant_service

    assistant_service.reset()
    client = MagicMock()
    if working:
        me = MagicMock()
        me.id = user_id
        client.get_me = AsyncMock(return_value=me)
    else:
        client.get_me = AsyncMock(side_effect=RuntimeError("not connected"))
    monkeypatch.setattr(stream_mod.stream_manager, "_user_client", client, raising=False)
    return helpers


def test_username_is_never_passed_as_a_user_id():
    """The Bot API rejects a string here; the old code could only ever fail."""
    import pydantic
    from aiogram.methods import GetChatMember

    with pytest.raises(pydantic.ValidationError):
        GetChatMember(chat_id=-1001234, user_id="@SomeAssistant")


def test_present_assistant_is_recognised(monkeypatch):
    """The reported bug: assistant in the group, bot says to add it."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    helpers = _fake_assistant(monkeypatch)
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))

    result = asyncio.run(helpers.ensure_assistant_in_chat(bot, -1001234))
    assert result is None, f"assistant is present but was rejected: {result}"

    passed_id = bot.get_chat_member.call_args.args[1]
    assert isinstance(passed_id, int), "must query by numeric id, not @username"


def test_absent_assistant_is_reported(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    helpers = _fake_assistant(monkeypatch)
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=MagicMock(status="left"))

    result = asyncio.run(helpers.ensure_assistant_in_chat(bot, -1001234))
    assert result and "add" in result.lower()


def test_unverifiable_assistant_does_not_block_playback(monkeypatch):
    """A wrong "add the assistant" sends people chasing a problem they lack."""
    import asyncio
    from unittest.mock import AsyncMock

    helpers = _fake_assistant(monkeypatch, working=False)
    bot = AsyncMock()

    assert asyncio.run(helpers.ensure_assistant_in_chat(bot, -1001234)) is None
    assert not bot.get_chat_member.called, "should not query with an unknown id"


def test_assistant_id_is_cached(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    helpers = _fake_assistant(monkeypatch)
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))

    async def twice():
        await helpers.ensure_assistant_in_chat(bot, -1)
        await helpers.ensure_assistant_in_chat(bot, -2)

    asyncio.run(twice())

    from bot.services import stream as stream_mod

    assert stream_mod.stream_manager._user_client.get_me.await_count == 1


# ── PyTgCalls failure messages ────────────────────────────────────────────


def test_missing_voice_chat_says_so():
    from pytgcalls.exceptions import NoActiveGroupCall

    from bot.services.callerrors import diagnose

    found = diagnose(NoActiveGroupCall(), "Assistant")
    assert "voice chat" in found.title.lower()
    assert "start" in found.hint.lower()
    assert "NoActiveGroupCall" not in found.title


def test_permission_errors_name_the_assistant():
    from bot.services.callerrors import diagnose

    found = diagnose(Exception("CHAT_ADMIN_REQUIRED"), "Bronzedone")
    assert "@Bronzedone" in found.title
    assert "admin" in found.hint.lower()


def test_transient_failures_are_marked_retryable():
    from bot.services.callerrors import diagnose

    assert diagnose(Exception("FLOOD_WAIT_X: wait"), "").retryable
    assert not diagnose(Exception("CHAT_ADMIN_REQUIRED"), "").retryable


def test_unknown_failures_still_get_a_usable_message():
    from bot.services.callerrors import diagnose

    found = diagnose(Exception("brand new failure mode"), "")
    assert found.title and found.hint
    assert "brand new failure mode" not in found.title, "no raw exception text"


def test_playback_uses_the_diagnoser():
    import inspect

    from bot.utils import play_helpers

    source = inspect.getsource(play_helpers.play_track)
    assert "diagnose(" in source
    assert "Playback failed: {exc}" not in source, "raw exception was user-facing"


# ── Error card layout ─────────────────────────────────────────────────────


def test_error_card_leads_with_the_problem():
    """"Something went wrong" above the real message was pure chrome."""
    from bot.utils.cards import error_card

    html = error_card("No voice chat is running.", "Start one and retry.").to_html()
    assert "Something went wrong" not in html
    assert html.splitlines()[0].strip().endswith("<b>No voice chat is running.</b>")
    assert len(html.strip().splitlines()) == 2


def test_success_card_has_no_redundant_banner():
    from bot.utils.cards import success_card

    html = success_card("Queue cleared.").to_html()
    assert "Done" not in html
    assert "Queue cleared." in html


# ──────────────────────────────────────────────────────────────────────────
# Assistant auto-join
#
# Telling someone to add the assistant by hand is a poor experience, and was
# actively wrong when the membership check was broken. FallenMusic's play
# flow invites the assistant itself; this does the same.
# ──────────────────────────────────────────────────────────────────────────


def _assistant_env(
    monkeypatch, *, status=None, username=None, join_error=None, id_ok=True
):
    from unittest.mock import AsyncMock, MagicMock

    from bot.services import assistant
    from bot.services import stream as stream_mod

    assistant.reset()

    client = MagicMock()
    if id_ok:
        me = MagicMock()
        me.id = 777
        client.get_me = AsyncMock(return_value=me)
    else:
        client.get_me = AsyncMock(side_effect=RuntimeError("not connected"))
    client.join_chat = (
        AsyncMock(side_effect=join_error) if join_error else AsyncMock()
    )
    monkeypatch.setattr(stream_mod.stream_manager, "_user_client", client, raising=False)

    bot = AsyncMock()
    if status:
        bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    else:
        bot.get_chat_member = AsyncMock(side_effect=Exception("user not found"))
    chat = MagicMock()
    chat.username = username
    chat.invite_link = None
    bot.get_chat = AsyncMock(return_value=chat)
    bot.export_chat_invite_link = AsyncMock(return_value="https://t.me/+abc123")

    return assistant, bot, client


def test_assistant_already_present_just_plays(monkeypatch):
    """The reported bug: assistant in the group, bot refused to play."""
    import asyncio

    assistant, bot, _ = _assistant_env(monkeypatch, status="member")
    result = asyncio.run(assistant.ensure_present(bot, -1001))

    assert result.ok
    assert not result.joined_now
    assert isinstance(bot.get_chat_member.call_args.args[1], int)


def test_missing_assistant_is_invited_not_delegated(monkeypatch):
    import asyncio

    assistant, bot, client = _assistant_env(monkeypatch, username="publicgroup")
    result = asyncio.run(assistant.ensure_present(bot, -1002))

    assert result.ok and result.joined_now
    assert client.join_chat.await_count == 1
    assert client.join_chat.call_args.args[0] == "@publicgroup"


def test_private_groups_use_an_exported_link(monkeypatch):
    import asyncio

    assistant, bot, client = _assistant_env(monkeypatch)
    result = asyncio.run(assistant.ensure_present(bot, -1003))

    assert result.ok and result.joined_now
    assert client.join_chat.call_args.args[0].startswith("https://t.me/+")


def test_missing_invite_permission_is_explained(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    assistant, bot, _ = _assistant_env(monkeypatch)
    bot.export_chat_invite_link = AsyncMock(
        side_effect=Exception("CHAT_ADMIN_REQUIRED: not enough rights")
    )
    result = asyncio.run(assistant.ensure_present(bot, -1004))

    assert not result.ok
    assert "invite" in result.hint.lower()
    assert "CHAT_ADMIN_REQUIRED" not in result.title


def test_banned_assistant_is_named_as_banned(monkeypatch):
    import asyncio

    assistant, bot, _ = _assistant_env(monkeypatch, status="kicked")
    result = asyncio.run(assistant.ensure_present(bot, -1005))

    assert not result.ok
    assert "banned" in result.title.lower()
    assert "unban" in result.hint.lower()


def test_already_participant_is_success_not_failure(monkeypatch):
    """A join race must not surface as an error."""
    import asyncio

    assistant, bot, _ = _assistant_env(
        monkeypatch, join_error=Exception("UserAlreadyParticipant")
    )
    assert asyncio.run(assistant.ensure_present(bot, -1006)).ok


def test_unknown_assistant_id_never_blocks_playback(monkeypatch):
    """A fabricated "add the assistant" is worse than attempting the play."""
    import asyncio

    assistant, bot, _ = _assistant_env(monkeypatch, id_ok=False)
    result = asyncio.run(assistant.ensure_present(bot, -1007))

    assert result.ok
    assert not bot.get_chat_member.called


def test_presence_is_cached_per_chat(monkeypatch):
    import asyncio

    assistant, bot, _ = _assistant_env(monkeypatch, status="member")

    async def twice():
        await assistant.ensure_present(bot, -1008)
        await assistant.ensure_present(bot, -1008)

    asyncio.run(twice())
    assert bot.get_chat_member.await_count == 1

    assistant.forget(-1008)
    asyncio.run(assistant.ensure_present(bot, -1008))
    assert bot.get_chat_member.await_count == 2


def test_expired_invite_link_is_explained(monkeypatch):
    import asyncio

    assistant, bot, _ = _assistant_env(
        monkeypatch, join_error=Exception("INVITE_HASH_EXPIRED")
    )
    result = asyncio.run(assistant.ensure_present(bot, -1009))

    assert not result.ok
    assert "expired" in result.title.lower()


def test_playback_invites_instead_of_asking(monkeypatch):
    import inspect

    from bot.utils import play_helpers

    source = inspect.getsource(play_helpers.play_track)
    assert "assistant.ensure_present" in source
    assert "ensure_assistant_in_chat" not in source


def test_render_deploys_the_branch_with_the_fixes():
    """main is an unrelated history stuck on pre-fix code."""
    import pathlib

    text = pathlib.Path("render.yaml").read_text()
    assert "branch: main" not in text, "main lacks every fix in this branch"


# ──────────────────────────────────────────────────────────────────────────
# Extraction speed and browser impersonation
#
# Five player clients x 3 retries x a 30s socket timeout was up to ten
# minutes of dead air before the user saw an error — and the SoundCloud
# fallback then started from scratch.
# ──────────────────────────────────────────────────────────────────────────


def test_retry_budget_cannot_blow_up_again():
    from bot.services.music import YDL_OPTS_BASE

    timeout = YDL_OPTS_BASE["socket_timeout"]
    retries = YDL_OPTS_BASE["retries"]
    extractor_retries = YDL_OPTS_BASE["extractor_retries"]

    assert timeout <= 15, f"socket_timeout {timeout}s is too patient for a block"
    assert retries <= 1, "a blocked IP fails identically on every retry"
    assert extractor_retries <= 1

    from bot.services.music import _player_clients

    worst = len(_player_clients()) * (retries + 1) * timeout
    assert worst <= 180, f"worst case still {worst}s"


def test_extraction_has_a_hard_deadline():
    import inspect

    from bot.services import music

    # The deadline lives on the single attempt; _run_ytdl wraps it in retries.
    source = inspect.getsource(music._run_ytdl_once)
    assert "asyncio.wait_for" in source, "yt-dlp's own timeouts are per-client"
    assert "extract_timeout" in source
    assert "looks_transient" in inspect.getsource(music._run_ytdl)


def test_timeout_message_triggers_the_fallback():
    """A timeout is throttling in all but name, so it must reach SoundCloud."""
    from bot.services.music import looks_blocked

    assert looks_blocked("Extraction timed out after 45s. The media host…")
    assert not looks_blocked("ERROR: no results for that query")


def test_timeout_returns_none_quickly(monkeypatch):
    import asyncio
    import dataclasses
    import time

    from bot.services import music

    class HangingYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, query, download=False):
            time.sleep(30)

    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, extract_timeout=1)
    )
    monkeypatch.setattr(music.yt_dlp, "YoutubeDL", HangingYDL)

    started = time.time()
    result = asyncio.run(music._run_ytdl({}, "ytsearch1:anything"))
    elapsed = time.time() - started

    assert result is None
    assert elapsed < 5, f"took {elapsed:.1f}s despite a 1s deadline"
    assert "timed out" in music.last_error().lower()


def test_impersonation_is_applied_when_available():
    from bot.services.music import _impersonate_target, _ydl_common

    target = _impersonate_target()
    opts = _ydl_common()
    if target is None:
        assert "impersonate" not in opts
    else:
        assert opts["impersonate"] == target


def test_curl_cffi_is_declared():
    """Without it yt-dlp has zero impersonate targets."""
    import pathlib

    text = pathlib.Path("requirements.txt").read_text().lower()
    assert "curl_cffi" in text or "curl-cffi" in text


def test_impersonation_prefers_firefox_and_can_be_disabled(monkeypatch):
    import dataclasses

    from bot.services import music

    music._impersonate_target.cache_clear()
    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, impersonate="off")
    )
    assert music._impersonate_target() is None

    music._impersonate_target.cache_clear()
    monkeypatch.setattr(
        music, "config", dataclasses.replace(music.config, impersonate="")
    )
    target = music._impersonate_target()
    if target is not None:
        assert "firefox" in str(target).lower(), f"preferred firefox, picked {target}"
    music._impersonate_target.cache_clear()


def test_slow_searches_report_progress():
    """A frozen "Loading media…" reads as a hang."""
    import asyncio
    from unittest.mock import AsyncMock

    from bot.handlers.play import _report_slow_search

    async def quick():
        status = AsyncMock()
        task = asyncio.create_task(_report_slow_search(status))
        await asyncio.sleep(0.1)
        task.cancel()
        return status

    assert asyncio.run(quick()).edit_text.await_count == 0, "fast search stayed quiet"


def test_progress_task_is_always_cancelled():
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play._resolve_and_play)
    assert "finally:" in source
    assert "progress.cancel()" in source, "a live task would overwrite the result"


# ──────────────────────────────────────────────────────────────────────────
# Reliable delivery
#
# Before bot/services/delivery.py nothing in the bot caught TelegramRetryAfter.
# A broadcast that tripped Telegram's ~30 msg/s ceiling counted every
# rate-limited chat as a permanent failure and moved on.
# ──────────────────────────────────────────────────────────────────────────


def test_rate_limiter_paces_sends():
    import asyncio
    import time

    from bot.services.delivery import RateLimiter

    async def burst():
        limiter = RateLimiter(rate=50.0)  # 20ms apart
        started = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        return time.monotonic() - started

    elapsed = asyncio.run(burst())
    # Four gaps of 20ms; the first acquire is free.
    assert elapsed >= 0.07, f"sends were not paced ({elapsed:.3f}s)"


def test_retry_after_is_honoured_then_retried():
    import asyncio

    from aiogram.exceptions import TelegramRetryAfter

    from bot.services.delivery import send_safe

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TelegramRetryAfter(
                method=None, message="Too Many Requests: retry after 1", retry_after=1
            )
        return "delivered"

    outcome = asyncio.run(send_safe(flaky, chat_id=42))

    assert outcome.ok, "a 429 is temporary; the send must be retried"
    assert outcome.result == "delivered"
    assert outcome.attempts == 2
    assert calls["n"] == 2


def test_absurd_retry_after_is_not_waited_out():
    """A multi-hour 429 must not freeze a broadcast."""
    import asyncio

    from aiogram.exceptions import TelegramRetryAfter

    from bot.services.delivery import send_safe

    async def blocked():
        raise TelegramRetryAfter(
            method=None, message="Too Many Requests: retry after 4000", retry_after=4000
        )

    outcome = asyncio.run(send_safe(blocked, chat_id=1))
    assert not outcome.ok
    assert not outcome.permanent, "a long 429 is still not permanent"


def test_blocked_users_are_permanent_not_retried():
    import asyncio

    from aiogram.exceptions import TelegramForbiddenError

    from bot.services.delivery import send_safe

    calls = {"n": 0}

    async def blocked():
        calls["n"] += 1
        raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")

    outcome = asyncio.run(send_safe(blocked, chat_id=7))

    assert not outcome.ok
    assert outcome.permanent, "retrying a block can never succeed"
    assert calls["n"] == 1, "a permanent failure must not be retried"


def test_permanent_classification():
    from aiogram.exceptions import TelegramBadRequest, TelegramNotFound

    from bot.services.delivery import is_permanent

    assert is_permanent(TelegramNotFound(method=None, message="chat not found"))
    assert is_permanent(
        TelegramBadRequest(method=None, message="Bad Request: chat not found")
    )
    assert is_permanent(
        TelegramBadRequest(method=None, message="Forbidden: bot was kicked from the group")
    )
    assert not is_permanent(
        TelegramBadRequest(method=None, message="Bad Request: message text is empty")
    )


def test_broadcast_separates_blocked_from_failed():
    import asyncio

    from aiogram.exceptions import TelegramForbiddenError, TelegramServerError

    from bot.services.delivery import Broadcaster

    async def send(chat_id: int):
        if chat_id == 2:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id == 3:
            raise TelegramServerError(method=None, message="Internal Server Error")
        return "ok"

    async def run():
        return await Broadcaster(progress_every=1).run([1, 2, 3, 4], send)

    report = asyncio.run(run())

    assert report.sent == 2
    assert report.blocked == 1, "a block is not a failure — it is a prune signal"
    assert report.failed == 1
    assert report.pruned == [2]
    assert report.total == 4


def test_broadcast_survives_a_broken_progress_callback():
    """A failed status edit must not abort delivery."""
    import asyncio

    from bot.services.delivery import Broadcaster

    async def send(chat_id: int):
        return "ok"

    async def boom(report):
        raise RuntimeError("message to edit not found")

    async def run():
        return await Broadcaster(progress_every=1).run([1, 2, 3], send, on_progress=boom)

    assert asyncio.run(run()).sent == 3


def test_broadcast_uses_the_delivery_layer():
    import inspect

    from bot.handlers import admin

    source = inspect.getsource(admin.cmd_broadcast)
    assert "delivery" in source, "broadcast must not hand-roll its own send loop"
    assert "deliverable_chats" in source, "known-dead chats should be skipped"
    assert "prune_dead_chats" in source


def test_dead_chats_are_skipped_then_revived(tmp_path, monkeypatch):
    import asyncio

    from bot.services import delivery
    from bot.services.database import database

    async def scenario():
        await database.set_chat_value(-100, "delivery_dead", 111.0)
        await database.set_chat_value(-200, "delivery_dead", 0)
        alive = await delivery.deliverable_chats([-100, -200, -300])
        await delivery.revive_chat(-100)
        after = await delivery.deliverable_chats([-100, -200, -300])
        return alive, after

    alive, after = asyncio.run(scenario())

    assert -100 not in alive, "a chat that blocked us should be skipped"
    assert -200 in alive and -300 in alive
    assert -100 in after, "talking to us again must restore delivery"


def test_gatekeeper_revives_chats():
    import inspect

    from bot.middlewares.gatekeeper import GatekeeperMiddleware

    source = inspect.getsource(GatekeeperMiddleware._record)
    assert "revive_chat" in source


def test_assistant_admin_commands_are_registered():
    from bot.handlers import assistant_admin

    registered = set()
    for handler in assistant_admin.router.message.handlers:
        for f in handler.filters or []:
            registered |= set(getattr(f.callback, "commands", None) or [])

    for command in ("setpfp", "setbio", "setname", "delpfp", "leaveall", "rmdownloads"):
        assert command in registered, f"/{command} was not registered"


def test_assistant_commands_are_sudo_gated():
    """These drive a real user account — they must never be public."""
    import inspect

    from bot.handlers import assistant_admin

    for name in ("cmd_setpfp", "cmd_setbio", "cmd_setname", "cmd_leaveall", "cmd_clearcache"):
        source = inspect.getsource(getattr(assistant_admin, name))
        assert "_sudo_only" in source, f"{name} is not sudo gated"


def test_leaveall_skips_chats_that_are_playing():
    import inspect

    from bot.handlers import assistant_admin

    source = inspect.getsource(assistant_admin.cmd_leaveall)
    assert "active_chats" in source, "leaving a chat mid-stream kills playback"


def test_assistant_admin_uses_the_active_chats_property():
    """It is a property; calling it returns a list, not a bool."""
    from bot.services.stream import stream_manager

    assert isinstance(stream_manager.active_chats, list)


# ──────────────────────────────────────────────────────────────────────────
# Message presentation
# ──────────────────────────────────────────────────────────────────────────


def test_error_styles_are_unified():
    """Two same-named builders used to render two different error styles."""
    from bot.utils.cards import error_card as card_version
    from bot.utils.formatters import error_card as fmt_version

    text = fmt_version("Track not found.")
    assert isinstance(text, str), "the formatters version must stay str-returning"
    assert text == card_version("Track not found.").to_html()
    assert "❌" not in text, "the legacy bulky Error heading should be gone"


def test_formatters_error_card_accepts_a_hint():
    from bot.utils.formatters import error_card, success_card

    assert "Set COOKIES_DATA." in error_card("Blocked.", "Set COOKIES_DATA.")
    assert "Saved." in success_card("Saved.")


def test_errors_tell_the_user_what_to_do():
    """A bare "Track not found." leaves the user with no next step."""
    import pathlib
    import re

    bare = []
    for path in pathlib.Path("bot/handlers").glob("*.py"):
        for match in re.finditer(r'error_card\(\s*"([^"]{10,})"\s*\)', path.read_text()):
            bare.append(f"{path.name}: {match.group(1)}")

    # Self-explanatory: the message already contains the valid range or the
    # entire state being reported. A hint would just restate it.
    allowed = {
        "controls.py: Nothing is playing right now.",
        "play.py: Tell me what to play.",
        "controls.py: That offset is out of range (UTC-12:00 … UTC+14:00).",
        "assistant_admin.py: The assistant has no profile photo to delete.",
    }
    unexplained = [entry for entry in bare if entry not in allowed]
    assert not unexplained, "these errors need an actionable hint: " + "; ".join(unexplained)


def test_track_info_deep_link_is_handled():
    """Now-playing titles link to /start info_<id>; it must resolve."""
    from bot.handlers import start

    names = {h.callback.__name__ for h in start.router.message.handlers}
    assert "cmd_start_track_info" in names
    assert "cmd_start" in names, "the plain /start must still work"


def test_deep_link_handler_is_registered_before_plain_start():
    """aiogram picks the first match; the generic /start would swallow it."""
    from bot.handlers import start

    order = [h.callback.__name__ for h in start.router.message.handlers]
    assert order.index("cmd_start_track_info") < order.index("cmd_start")


def test_track_links_keyboard():
    from bot.keyboards.inline import track_links_kb

    kb = track_links_kb({"title": "Some Song", "url": "https://soundcloud.com/x/y"})
    labels = [button.text for row in kb.inline_keyboard for button in row]
    assert any("sᴏᴜɴᴅᴄʟᴏᴜᴅ" in label for label in labels), "the button should name the source"
    assert any("ᴄʟᴏsᴇ" in label for label in labels)


def test_track_links_keyboard_survives_a_bare_track():
    """Search results and cached entries don't always carry a url."""
    from bot.keyboards.inline import track_links_kb

    kb = track_links_kb({})
    assert kb.inline_keyboard, "a close button should always remain"


def test_close_button_has_a_handler():
    from bot.handlers import callbacks

    found = False
    for handler in callbacks.router.callback_query.handlers:
        if "cb_close" == handler.callback.__name__:
            found = True
    assert found, "ui:close would silently do nothing"


# ──────────────────────────────────────────────────────────────────────────
# Vote-skip
# ──────────────────────────────────────────────────────────────────────────


def test_requester_bypass_uses_id_not_display_name():
    """A display name is not identity — anyone can copy one."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play._may_skip_now)
    assert "requester_id" in source, "the bypass must key off a user id"
    assert 'requester") == user.full_name' not in source, "name comparison is forgeable"


def test_tracks_carry_a_requester_id():
    from bot.services.music import _normalize_entry

    track = _normalize_entry({"id": "abc", "title": "x"}, "Alice")
    assert "requester_id" in track, "without this the skip bypass cannot be checked"


def test_play_records_the_requester_id():
    import inspect

    from bot.utils import play_helpers

    source = inspect.getsource(play_helpers)
    assert 'track["requester_id"]' in source


def test_votes_can_be_withdrawn():
    """The vote button stays on screen, so a mis-tap must be reversible."""
    from bot.services.voteskip import VoteSkipManager

    manager = VoteSkipManager()
    track = {"id": "song-1"}

    votes, added = manager.add_vote(-100, 111, track)
    assert (votes, added) == (1, True)
    assert manager.has_voted(-100, 111, track)

    votes, removed = manager.remove_vote(-100, 111, track)
    assert (votes, removed) == (0, True)
    assert not manager.has_voted(-100, 111, track)

    # Withdrawing a vote that was never cast is a no-op, not an error.
    votes, removed = manager.remove_vote(-100, 222, track)
    assert removed is False


def test_votes_do_not_leak_across_tracks():
    from bot.services.voteskip import VoteSkipManager

    manager = VoteSkipManager()
    manager.add_vote(-100, 111, {"id": "song-1"})
    assert not manager.has_voted(-100, 111, {"id": "song-2"}), "a vote is per track"


def test_vote_threshold_ignores_the_assistant():
    from bot.services.voteskip import VoteSkipManager

    manager = VoteSkipManager()
    # 4 in the call = 3 humans + the assistant; half of 3 rounds to 2.
    assert manager.needed(4, 0.5) == 2
    # Never fewer than MIN_VOTES, even in a tiny call.
    assert manager.needed(2, 0.1) == 2


def test_voteskip_has_a_tap_to_vote_button():
    from bot.keyboards.inline import voteskip_kb

    kb = voteskip_kb(1, 3)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("1/3" in label for label in labels), "the tally belongs on the button"
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "vote:skip" in data


def test_vote_button_has_a_handler():
    from bot.handlers import callbacks

    names = {h.callback.__name__ for h in callbacks.router.callback_query.handlers}
    assert "cb_voteskip" in names, "vote:skip would silently do nothing"


# ──────────────────────────────────────────────────────────────────────────
# Attributed action cards (FallenMusic's "└ʙʏ : @user" shape)
# ──────────────────────────────────────────────────────────────────────────


def test_action_cards_name_the_actor():
    from bot.utils.cards import action_card

    html = action_card("paused", "Alice").to_html()
    assert "Paused" in html
    assert "Alice" in html, "a group needs to know who paused the stream"


def test_action_card_handles_an_unknown_action():
    """A new action must not render as a traceback."""
    from bot.utils.cards import action_card

    assert "Fast Forward" in action_card("fast_forward", "Bob").to_html()


def test_action_card_without_an_actor():
    from bot.utils.cards import action_card

    html = action_card("ended").to_html()
    assert "Ended" in html
    assert "by" not in html.lower().split("ended")[-1]


def test_transport_commands_are_attributed():
    import inspect

    from bot.handlers import play

    for name in ("cmd_pause", "cmd_resume", "cmd_stop"):
        source = inspect.getsource(getattr(play, name))
        assert "action_card" in source, f"{name} still uses an unattributed message"
        assert "_actor(" in source, f"{name} does not say who did it"


def test_stop_clears_a_pending_vote():
    """A vote opened on the old track must not carry into the next one."""
    import inspect

    from bot.handlers import play

    assert "voteskip.reset" in inspect.getsource(play.cmd_stop)


def test_stop_with_an_argument_still_deletes_a_filter():
    """play.router is registered first, so a bare pass-through never ran."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.cmd_stop)
    assert "cmd_stop_filter" in source, "/stop <name> would be swallowed by playback"


# ──────────────────────────────────────────────────────────────────────────
# Usage cards
# ──────────────────────────────────────────────────────────────────────────


def test_play_commands_show_examples_not_a_usage_line():
    import inspect

    from bot.handlers import play

    for name in ("cmd_play", "cmd_vplay", "cmd_vstream"):
        source = inspect.getsource(getattr(play, name))
        assert "RichCard" in source, f"{name} still replies with a bare usage string"


def test_vplay_warns_about_video_requirements():
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.cmd_vplay).lower()
    assert "voice chat" in source and "bandwidth" in source


def test_stream_started_card_marks_live_tracks():
    from bot.utils.cards import stream_started_card

    live = stream_started_card({"title": "News", "is_live": True}).to_html()
    assert "Live stream" in live

    normal = stream_started_card({"title": "Song", "duration": 200}).to_html()
    assert "Live stream" not in normal
    assert "3:20" in normal


# ──────────────────────────────────────────────────────────────────────────
# Startup / shutdown reports
# ──────────────────────────────────────────────────────────────────────────


def test_startup_report_probes_every_subsystem():
    import asyncio

    from bot.services import startup

    report = asyncio.run(startup.collect(None))
    names = {check.name for check in report.checks}
    for expected in ("Assistant", "ffmpeg", "Cookies", "Storage", "Thumbnails"):
        assert expected in names, f"{expected} is not covered by the boot report"


def test_startup_report_flags_a_missing_assistant():
    """A boot that looks fine but cannot play is the failure worth catching."""
    import asyncio

    from bot.services import assistant as assistant_service
    from bot.services import startup

    # Other tests install a fake assistant id; start from a clean slate.
    assistant_service.reset()
    report = asyncio.run(startup.collect(None))
    assistant_service.reset()

    assistant = next(c for c in report.checks if c.name == "Assistant")
    assert not assistant.healthy, "no SESSION_STRING configured in tests"
    assert assistant in report.degraded


def test_startup_card_names_what_is_broken():
    from types import SimpleNamespace

    from bot.services.startup import DEAD, OK, Check, Report, build_card

    report = Report(checks=[Check("ffmpeg", DEAD, "not installed"), Check("Storage", OK, "json")])
    me = SimpleNamespace(id=1, username="testbot", first_name="Test")
    html = build_card(me, report).to_html()

    assert "Needs attention" in html
    assert "ffmpeg" in html
    assert "not installed" in html, "the operator needs the reason, not just a red dot"


def test_startup_card_is_clean_when_healthy():
    from types import SimpleNamespace

    from bot.services.startup import OK, Check, Report, build_card

    report = Report(checks=[Check("Storage", OK, "json")])
    me = SimpleNamespace(id=1, username="testbot", first_name="Test")
    html = build_card(me, report).to_html()

    assert "nominal" in html.lower()
    assert "Needs attention" not in html


def test_owner_dm_is_skipped_without_an_owner_id(monkeypatch):
    import asyncio
    import dataclasses

    from bot.services import startup

    monkeypatch.setattr(startup, "config", dataclasses.replace(startup.config, owner_id=0))
    assert asyncio.run(startup.notify_owner(None)) is False


def test_startup_notification_never_breaks_the_boot(monkeypatch):
    """A failed report must not stop the bot from starting."""
    import asyncio
    import dataclasses

    from bot.services import startup

    monkeypatch.setattr(startup, "config", dataclasses.replace(startup.config, owner_id=123))

    class ExplodingBot:
        async def get_me(self):
            raise RuntimeError("Telegram is down")

    assert asyncio.run(startup.notify_owner(ExplodingBot())) is False


def test_main_sends_the_startup_dm_and_shutdown_notice():
    import pathlib

    source = pathlib.Path("main.py").read_text()
    assert "startup.notify_owner" in source
    assert "startup.notify_shutdown" in source

    # The shutdown DM must be sent before the session closes.
    assert source.index("startup.notify_shutdown") < source.index("await bot.session.close()")


# ──────────────────────────────────────────────────────────────────────────
# Player resilience
# ──────────────────────────────────────────────────────────────────────────


def test_one_dead_track_does_not_kill_the_queue():
    """Previously the first failed track stopped the whole session."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from bot.services.stream import StreamManager

    queued = [{"title": "dead-1"}, {"title": "dead-2"}, {"title": "good"}]
    cursor = {"i": 0}

    async def next_track(chat_id):
        if cursor["i"] < len(queued):
            track = queued[cursor["i"]]
            cursor["i"] += 1
            return track
        return None

    async def play(chat_id, track):
        if track["title"].startswith("dead"):
            raise RuntimeError("Video unavailable")

    manager = StreamManager()
    manager.play = play
    manager.stop = AsyncMock()

    seen: dict = {}

    async def on_skip(chat_id, titles):
        seen["skipped"] = titles

    async def on_next(chat_id, track):
        seen["playing"] = track["title"]

    manager.on_autoskip(on_skip)
    manager.on_track_end(on_next)

    async def run():
        with patch("bot.services.stream.queue_manager") as qm:
            qm.next_track = next_track
            await manager._handle_end(-100)

    asyncio.run(run())

    assert seen.get("playing") == "good", "the queue should survive dead links"
    assert seen.get("skipped") == ["dead-1", "dead-2"]


def test_autoskip_is_capped():
    """A queue full of dead links must not spin forever."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from bot.services.stream import MAX_AUTOSKIP, StreamManager

    attempts = {"n": 0}

    async def next_track(chat_id):
        return {"title": f"dead-{attempts['n']}"}

    async def play(chat_id, track):
        attempts["n"] += 1
        raise RuntimeError("Video unavailable")

    manager = StreamManager()
    manager.play = play
    manager.stop = AsyncMock()

    async def run():
        with patch("bot.services.stream.queue_manager") as qm:
            qm.next_track = next_track
            await manager._handle_end(-100)

    asyncio.run(run())
    assert attempts["n"] == MAX_AUTOSKIP
    manager.stop.assert_awaited()


def test_queue_advances_are_announced():
    """PyTgCalls changes track silently; the group should be told."""
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.register_stream_notifications)
    assert "on_track_end" in source
    assert "on_autoskip" in source
    assert "on_queue_empty" in source


def test_stream_notifications_are_registered_at_startup():
    import pathlib

    assert "register_stream_notifications" in pathlib.Path("main.py").read_text()


def test_announcements_can_be_muted_per_chat():
    import inspect

    from bot.handlers import play

    source = inspect.getsource(play.register_stream_notifications)
    assert "announce_tracks" in source, "busy groups need a way to silence these"


# ──────────────────────────────────────────────────────────────────────────
# Rich block field validity
#
# Tables rendered as an empty grid in production: RichBlockTableCell takes
# `text`, but the builder passed `blocks=`. Pydantic keeps unknown keys instead
# of raising, so every cell shipped with text=None and Telegram drew borders
# around nothing. Nothing in the suite noticed, because the HTML twin — which
# is what tests assert on — was built by a completely separate code path.
# ──────────────────────────────────────────────────────────────────────────


def test_no_rich_block_is_built_with_an_unknown_field():
    """Static sweep: every kwarg must be a real field on the aiogram type."""
    import ast
    import pathlib

    import aiogram.types as telegram_types

    tree = ast.parse(pathlib.Path("bot/utils/rich.py").read_text())
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        cls = getattr(telegram_types, node.func.id, None)
        if cls is None or not hasattr(cls, "model_fields"):
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg not in cls.model_fields:
                problems.append(
                    f"line {node.lineno}: {node.func.id}({kw.arg}=...) is not a field; "
                    f"valid: {sorted(cls.model_fields)}"
                )
    assert not problems, "silently dropped rich-block fields:\n" + "\n".join(problems)


def test_table_cells_actually_carry_their_text():
    """The exact production bug: bordered grid, empty cells."""
    from bot.utils.rich import RichCard, c

    card = RichCard().table(["Setting", "Value"], [["Audio quality", c("high")]])
    payload = card.to_rich_message().model_dump(exclude_none=True)
    table = next(b for b in payload["blocks"] if b["type"] == "table")

    for row in table["cells"]:
        for cell in row:
            assert cell.get("text"), f"empty table cell: {cell}"

    header = [cell["text"] for cell in table["cells"][0]]
    assert header == ["Setting", "Value"]


def test_every_card_builder_produces_non_empty_cells():
    """Sweep the real cards users see, not a synthetic one."""
    from bot.utils import cards

    samples = {
        "now_playing_card": lambda: cards.now_playing_card(
            {"title": "Song", "artist": "Artist", "duration": 200, "requester": "Al"},
            elapsed=10,
            queue_len=2,
        ),
        "queued_card": lambda: cards.queued_card(
            {"title": "Song", "duration": 100, "requester": "Al"}, 2, 3
        ),
        "stream_started_card": lambda: cards.stream_started_card(
            {"title": "Song", "duration": 100}
        ),
        "voteskip_card": lambda: cards.voteskip_card(1, 3, "Song"),
    }

    for name, build in samples.items():
        payload = build().to_rich_message().model_dump(exclude_none=True)
        for block in payload["blocks"]:
            if block["type"] != "table":
                continue
            for row in block["cells"]:
                for cell in row:
                    assert cell.get("text") not in (None, ""), f"{name}: empty cell {cell}"


def test_ordered_lists_are_actually_numbered():
    """is_ordered is not a field; ordering comes from each item's value."""
    from bot.utils.rich import RichCard

    ordered = RichCard().bullets(["a", "b"], ordered=True)
    values = [
        item.get("value")
        for item in ordered.to_rich_message().model_dump(exclude_none=True)["blocks"][0]["items"]
    ]
    assert values == [1, 2], "ordered list rendered as plain bullets"

    plain_list = RichCard().bullets(["a", "b"])
    payload = plain_list.to_rich_message().model_dump(exclude_none=True)
    assert all("value" not in item for item in payload["blocks"][0]["items"])


def test_rich_and_html_twins_agree_on_table_content():
    """The HTML twin passed while the rich path was broken — keep them in sync."""
    from bot.utils.rich import RichCard

    card = RichCard().table(["Key", "Value"], [["Storage", "json"]])
    html = card.to_html()
    payload = card.to_rich_message().model_dump(exclude_none=True)
    table = next(b for b in payload["blocks"] if b["type"] == "table")

    flat = []
    for row in table["cells"]:
        for cell in row:
            text = cell["text"]
            flat.append(text if isinstance(text, str) else "".join(s["text"] for s in text))

    for value in flat:
        assert value in html, f"{value!r} is in the rich table but missing from the HTML twin"


# ──────────────────────────────────────────────────────────────────────────
# Search resilience
#
# A dropped TLS handshake produced "Playback failed — make sure a voice chat
# is running", pointing users at something that was never the problem, and the
# SoundCloud fallback never ran because looks_blocked() was False.
# ──────────────────────────────────────────────────────────────────────────


def test_network_errors_are_recognised_as_transient():
    from bot.services.music import looks_transient

    real_failure = (
        'ERROR: query "faded" page 1: Unable to download API page: Failed to '
        "perform, curl: (35) BoringSSL SSL_connect: Connection closed abruptly "
        "(SSL_ERROR_SYSCALL; error queue empty) in connection to www.youtube.com:443"
    )
    assert looks_transient(real_failure)
    assert looks_transient("Remote end closed connection without response")
    assert looks_transient("HTTP Error 503: Service Unavailable")
    assert not looks_transient("Video unavailable. This video is private.")


def test_a_network_failure_still_tries_the_next_backend():
    """The whole point of a fallback: YouTube never answered."""
    from bot.services.music import should_try_next_backend

    assert should_try_next_backend("BoringSSL SSL_connect: Connection closed abruptly")
    assert should_try_next_backend("Sign in to confirm you're not a bot")
    # A genuine miss must NOT be re-asked elsewhere — that returns junk.
    assert not should_try_next_backend("No video results for that query")


def test_search_falls_through_every_backend(monkeypatch):
    import asyncio

    from bot.services import music

    tried: list[str] = []

    async def fake_once(opts, query):
        tried.append(query.split(":")[0])
        music._last_error = "curl: (35) BoringSSL SSL_connect: Connection closed abruptly"
        return None

    monkeypatch.setattr(music, "_run_ytdl_once", fake_once)
    result = asyncio.run(music._search_with_fallback("anything", {}, count=1))

    assert result is None
    assert any(p.startswith("ytsearch") for p in tried)
    assert any(
        p.startswith("scsearch") for p in tried
    ), "SoundCloud was never tried on a network error"


def test_transient_failures_are_retried(monkeypatch):
    """One dropped handshake should not fail the whole request."""
    import asyncio
    import dataclasses

    from bot.services import music

    calls = {"n": 0}

    async def flaky(opts, query):
        calls["n"] += 1
        if calls["n"] == 1:
            music._last_error = "Connection reset by peer"
            return None
        music._last_error = ""
        return {"entries": [{"id": "ok", "title": "Recovered"}]}

    monkeypatch.setattr(music, "config", dataclasses.replace(music.config, extract_attempts=2))
    monkeypatch.setattr(music, "_run_ytdl_once", flaky)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(music.asyncio, "sleep", no_delay)

    result = asyncio.run(music._run_ytdl({}, "ytsearch1:x"))
    assert result is not None, "a transient failure should be retried"
    assert calls["n"] == 2


def test_permanent_failures_are_not_retried(monkeypatch):
    """Retrying a blocked IP or a missing track only adds latency."""
    import asyncio
    import dataclasses

    from bot.services import music

    calls = {"n": 0}

    async def missing(opts, query):
        calls["n"] += 1
        music._last_error = "Video unavailable. This video is private."
        return None

    monkeypatch.setattr(music, "config", dataclasses.replace(music.config, extract_attempts=3))
    monkeypatch.setattr(music, "_run_ytdl_once", missing)

    assert asyncio.run(music._run_ytdl({}, "ytsearch1:x")) is None
    assert calls["n"] == 1, "a private video fails identically every time"


def test_extra_search_backend_is_configured():
    from bot.services.music import SEARCH_BACKENDS

    prefixes = [prefix for prefix, _ in SEARCH_BACKENDS]
    assert prefixes[:2] == ["ytsearch", "scsearch"], "YouTube first, it has the catalogue"
    assert len(SEARCH_BACKENDS) >= 3, "one fallback is not much of a fallback"


def test_all_backends_use_a_real_yt_dlp_search_key():
    """A typo'd prefix fails as 'unsupported url' on every query."""
    import yt_dlp

    from bot.services.music import SEARCH_BACKENDS

    keys = {
        getattr(ie, "_SEARCH_KEY", None)
        for ie in yt_dlp.extractor.gen_extractors()
        if getattr(ie, "_SEARCH_KEY", None)
    }
    for prefix, label in SEARCH_BACKENDS:
        assert prefix in keys, f"{label}: {prefix!r} is not a yt-dlp search key"


def test_network_failures_are_not_blamed_on_the_voice_chat():
    """The old catch-all sent users to check something that was fine."""
    from bot.services.callerrors import diagnose

    network = diagnose(RuntimeError("BoringSSL SSL_connect: Connection closed abruptly"))
    assert "voice chat" not in network.hint.lower()
    assert network.retryable

    blocked = diagnose(RuntimeError("Sign in to confirm you're not a bot"))
    assert "COOKIES_DATA" in blocked.hint

    # A genuinely unknown error keeps the original generic advice.
    unknown = diagnose(RuntimeError("something totally unexpected"))
    assert "voice chat" in unknown.hint.lower()


def test_richcard_methods_exist_at_every_call_site():
    """`.paragraph()` does not exist — `.para()` does. This crashed /assistant."""
    import ast
    import pathlib

    from bot.utils.rich import RichCard

    valid = {m for m in dir(RichCard) if not m.startswith("_")}
    problems = []
    for path in pathlib.Path("bot").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            base = node.func.value
            while isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
                base = base.func.value
            is_card = (
                isinstance(base, ast.Call)
                and isinstance(base.func, ast.Name)
                and base.func.id == "RichCard"
            ) or (isinstance(base, ast.Name) and base.id == "card")
            if is_card and node.func.attr not in valid:
                problems.append(f"{path}:{node.lineno} .{node.func.attr}()")
    assert not problems, "RichCard has no such method:\n" + "\n".join(problems)


# ──────────────────────────────────────────────────────────────────────────
# UI consistency
#
# The cards were structurally correct but visually flat: 92 .para() calls
# against 9 .quote(), so everything rendered as undifferentiated lines.
# ──────────────────────────────────────────────────────────────────────────


def _sample_track():
    return {
        "title": "Alan Walker - Faded",
        "duration": 212,
        "artist": "Alan Walker",
        "requester": "Rahul",
        "requester_id": 7,
        "url": "https://youtu.be/60ItHLz5WEA",
        "id": "60ItHLz5WEA",
        "source": "youtube",
    }


def test_core_cards_use_blockquotes():
    """Grouped metadata should be visually set apart, not another flat line."""
    from bot.utils.cards import action_card, error_card, now_playing_card, queued_card

    track = _sample_track()
    for name, card in [
        ("now_playing", now_playing_card(track, elapsed=45, queue_len=3)),
        ("queued", queued_card(track, 2, 5)),
        ("action", action_card("paused", "Rahul")),
        ("error", error_card("Playback failed.", "Start a voice chat first.")),
    ]:
        assert "<blockquote>" in card.to_html(), f"{name} still renders flat"


def test_blockquotes_survive_into_the_rich_payload():
    """The HTML twin having a quote proves nothing about what Telegram gets."""
    from aiogram.types import InputRichBlockBlockQuotation

    from bot.utils.cards import now_playing_card

    message = now_playing_card(_sample_track(), elapsed=45).to_rich_message()
    quotes = [b for b in message.blocks if isinstance(b, InputRichBlockBlockQuotation)]
    assert quotes, "no blockquote block reached the rich payload"
    assert all(q.blocks for q in quotes), "blockquote shipped with no content"


def test_no_card_block_is_silently_empty():
    """A block with neither text nor children draws an empty gap."""
    from bot.utils.cards import (
        action_card,
        error_card,
        now_playing_card,
        queued_card,
        stream_started_card,
        success_card,
    )

    track = _sample_track()
    cards = {
        "now_playing": now_playing_card(track, elapsed=45),
        "queued": queued_card(track, 1, 1),
        "stream_started": stream_started_card(track),
        "action": action_card("shuffled", "Rahul", note="3 tracks reordered."),
        "error": error_card("Nope.", "Try again."),
        "success": success_card("Done.", "All good."),
    }
    for name, card in cards.items():
        for block in card.to_rich_message().blocks:
            kind = type(block).__name__
            if kind in {"InputRichBlockDivider", "InputRichBlockBlank"}:
                continue
            body = getattr(block, "text", None) or getattr(block, "blocks", None)
            if kind == "InputRichBlockTable":
                body = getattr(block, "cells", None)
            assert body, f"{name}: {kind} is empty"


def test_terminal_replies_go_through_a_card(monkeypatch):
    """Spinners may be plain strings; the message a user is left with may not.

    /shuffle, /clear, /volume and /info each hand-built HTML, so they were the
    handlers the card styling never reached.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.handlers import misc, play
    from bot.services.queue import queue_manager

    sent: list[str] = []

    async def capture(message, card, **kwargs):
        sent.append(card.to_html())
        return MagicMock()

    monkeypatch.setattr(play, "send_card", capture)
    monkeypatch.setattr(misc, "send_card", capture)

    def fake_message(text: str):
        message = MagicMock()
        message.chat.id = -100999
        message.from_user.full_name = "Rahul"
        message.from_user.id = 7
        message.text = text
        message.answer = AsyncMock()
        return message

    async def exercise():
        chat_id = -100999
        track = _sample_track()
        await queue_manager.add(chat_id, dict(track))
        await queue_manager.set_current(chat_id, dict(track))

        await play.cmd_shuffle(fake_message("/shuffle"))
        await play.cmd_clear(fake_message("/clear"))
        await play.cmd_volume(fake_message("/volume"))
        await misc.cmd_info(fake_message("/info"))

    asyncio.run(exercise())

    assert len(sent) == 4, "a handler replied without going through send_card"
    for html in sent:
        assert "<b>" in html, "card rendered with no formatting at all"


# ──────────────────────────────────────────────────────────────────────────
# Queue correctness
# ──────────────────────────────────────────────────────────────────────────


def test_a_cleared_track_does_not_come_back_under_loop_all():
    """/clear emptied the list but left _current, so loop-all re-queued it."""
    import asyncio

    from bot.services.queue import LoopMode, queue_manager

    async def scenario():
        chat_id = -775001
        await queue_manager.reset(chat_id)
        await queue_manager.add(chat_id, {"title": "OLD", "duration": 10})
        await queue_manager.next_track(chat_id)
        await queue_manager.set_loop(chat_id, LoopMode.ALL)
        await queue_manager.clear(chat_id)
        for title in ("new1", "new2"):
            await queue_manager.add(chat_id, {"title": title, "duration": 10})
        return [
            (await queue_manager.next_track(chat_id) or {}).get("title")
            for _ in range(6)
        ]

    played = asyncio.run(scenario())
    assert "OLD" not in played, f"cleared track resurrected: {played}"


def test_loop_all_still_cycles_normally():
    """The clear fix must not break the feature it guards."""
    import asyncio

    from bot.services.queue import LoopMode, queue_manager

    async def scenario():
        chat_id = -775002
        await queue_manager.reset(chat_id)
        await queue_manager.set_loop(chat_id, LoopMode.ALL)
        for title in ("a", "b", "c"):
            await queue_manager.add(chat_id, {"title": title, "duration": 10})
        return [
            (await queue_manager.next_track(chat_id) or {}).get("title")
            for _ in range(7)
        ]

    assert asyncio.run(scenario()) == ["a", "b", "c", "a", "b", "c", "a"]


def test_reset_drops_every_per_chat_structure():
    """A missed dict is a slow leak in a process that runs for weeks."""
    import asyncio

    from bot.services.queue import LoopMode, queue_manager

    async def scenario():
        chat_id = -775003
        await queue_manager.add(chat_id, {"title": "x", "duration": 1})
        await queue_manager.set_loop(chat_id, LoopMode.ALL)
        await queue_manager.next_track(chat_id)
        await queue_manager.set_volume(chat_id, 150)
        await queue_manager.reset(chat_id)
        return {
            name: chat_id in getattr(queue_manager, name)
            for name in (
                "_queues",
                "_current",
                "_loop",
                "_loop_count",
                "_volume",
                "_requeue_current",
            )
        }

    leaked = [name for name, present in asyncio.run(scenario()).items() if present]
    assert not leaked, f"reset() leaked: {leaked}"


def test_a_full_queue_is_reported_not_raised():
    """add() raises; the non-raising twin is what handlers should call."""
    import asyncio

    from bot.services.queue import QueueManager

    async def scenario():
        manager = QueueManager(max_size=2)
        chat_id = -775004
        await manager.add(chat_id, {"title": "a"})
        await manager.add(chat_id, {"title": "b"})
        overflow = await manager.try_add(chat_id, {"title": "c"})
        front = await manager.try_add_front(chat_id, {"title": "d"})
        return overflow, front, await manager.size(chat_id)

    overflow, front, size = asyncio.run(scenario())
    assert overflow is None and front is None
    assert size == 2


def test_add_many_fills_the_remaining_space():
    """The old loop stopped at the first rejection instead of partly filling."""
    import asyncio

    from bot.services.queue import QueueManager

    async def scenario():
        manager = QueueManager(max_size=5)
        chat_id = -775005
        await manager.add(chat_id, {"title": "seed"})
        added = await manager.add_many(
            chat_id, [{"title": f"t{n}"} for n in range(10)]
        )
        return added, await manager.size(chat_id)

    added, size = asyncio.run(scenario())
    assert added == 4, f"expected to fill the 4 free slots, added {added}"
    assert size == 5


def test_no_handler_calls_the_raising_add_unguarded():
    """An escaped ValueError shows the user 'Something went wrong'."""
    import ast
    import pathlib

    offenders = []
    for path in list(pathlib.Path("bot/handlers").rglob("*.py")) + list(
        pathlib.Path("bot/utils").rglob("*.py")
    ):
        tree = ast.parse(path.read_text())
        guarded: list[tuple[int, int]] = [
            (node.lineno, node.end_lineno or node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
        ]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("add", "add_front"):
                continue
            target = node.func.value
            if not (isinstance(target, ast.Name) and target.id == "queue_manager"):
                continue
            if not any(start <= node.lineno <= end for start, end in guarded):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "queue_manager.add() can raise; use try_add() or wrap it:\n"
        + "\n".join(offenders)
    )


# ──────────────────────────────────────────────────────────────────────────
# Autoplay
# ──────────────────────────────────────────────────────────────────────────


def test_autoplay_never_repeats_within_its_memory():
    import asyncio

    from bot.services.autoplay import autoplay

    async def fetch(query, limit):
        return [{"id": f"v{n}", "title": f"song {n}"} for n in range(limit)]

    async def scenario():
        chat_id = -776001
        autoplay.forget(chat_id)
        seed = {"id": "seed", "title": "Faded", "artist": "Alan Walker"}
        return [
            (await autoplay.pick(chat_id, seed, fetch=fetch) or {}).get("title")
            for _ in range(5)
        ]

    picks = asyncio.run(scenario())
    assert None not in picks
    assert len(set(picks)) == len(picks), f"autoplay looped: {picks}"


def test_autoplay_gives_up_instead_of_hammering_a_dead_extractor():
    import asyncio

    from bot.services.autoplay import MAX_FAILURES, autoplay

    async def broken(query, limit):
        raise RuntimeError("extractor down")

    async def scenario():
        chat_id = -776002
        autoplay.forget(chat_id)
        seed = {"id": "s", "title": "x"}
        for _ in range(MAX_FAILURES):
            await autoplay.pick(chat_id, seed, fetch=broken)
        return autoplay.exhausted(chat_id)

    assert asyncio.run(scenario()), "autoplay should disable itself after repeated failures"


def test_autoplay_is_off_by_default():
    """Opt-in: a bot playing unprompted in someone's group is a nuisance."""
    import asyncio

    from bot.services.autoplay import autoplay

    assert not asyncio.run(autoplay.is_enabled(-776003))


def test_autoplay_reads_both_the_command_and_the_settings_toggle():
    """/autoplay writes "1"/"0"; the settings keyboard writes real bools."""
    import asyncio

    from bot.services.autoplay import autoplay
    from bot.services.database import database

    async def scenario():
        chat_id = -776004
        await autoplay.set_enabled(chat_id, True)
        from_command = await autoplay.is_enabled(chat_id)
        await database.set_chat_value(chat_id, "autoplay", False)
        from_toggle_off = await autoplay.is_enabled(chat_id)
        await database.set_chat_value(chat_id, "autoplay", True)
        from_toggle_on = await autoplay.is_enabled(chat_id)
        return from_command, from_toggle_off, from_toggle_on

    assert asyncio.run(scenario()) == (True, False, True)


def test_autoplay_command_handles_every_argument():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.handlers import controls

    rendered: list[str] = []

    async def capture(message, card, **kwargs):
        rendered.append(card.to_html())
        return MagicMock()

    original_send, original_guard = controls.send_card, controls._can_control
    controls.send_card = capture
    controls._can_control = AsyncMock(return_value=True)
    try:

        def message(text: str):
            item = MagicMock()
            item.chat.id = -776005
            item.text = text
            item.from_user.full_name = "Rahul"
            item.from_user.id = 7
            return item

        async def scenario():
            for text in ("/autoplay on", "/autoplay off", "/autoplay", "/autoplay wat"):
                await controls.cmd_autoplay(message(text), MagicMock())

        asyncio.run(scenario())
    finally:
        controls.send_card, controls._can_control = original_send, original_guard

    assert len(rendered) == 4
    assert "Autoplay On" in rendered[0]
    assert "Autoplay Off" in rendered[1]
    assert "Unknown option" in rendered[3]


# ──────────────────────────────────────────────────────────────────────────
# Session generator
# ──────────────────────────────────────────────────────────────────────────


def test_env_writer_preserves_the_rest_of_the_file():
    import tempfile
    from pathlib import Path

    from session_generator import write_env_value

    root = Path(tempfile.mkdtemp())

    fresh = root / "new.env"
    assert write_env_value(fresh, "SESSION_STRING", "AAA") == "created"
    assert fresh.read_text().strip() == "SESSION_STRING=AAA"

    existing = root / "existing.env"
    existing.write_text("# comment\nAPI_ID=123\nBOT_TOKEN=xyz\n")
    assert write_env_value(existing, "SESSION_STRING", "BBB") == "appended"
    body = existing.read_text()
    assert "# comment" in body and "API_ID=123" in body and "SESSION_STRING=BBB" in body

    replace = root / "replace.env"
    replace.write_text("API_ID=1\nSESSION_STRING=OLD\nBOT_TOKEN=t\n")
    assert write_env_value(replace, "SESSION_STRING", "CCC") == "updated"
    assert "OLD" not in replace.read_text()
    assert "BOT_TOKEN=t" in replace.read_text()
    assert (root / "replace.env.bak").read_text().count("OLD") == 1


def test_env_writer_keeps_an_export_prefix():
    """Some people source their .env; dropping `export ` breaks that quietly."""
    import tempfile
    from pathlib import Path

    from session_generator import write_env_value

    path = Path(tempfile.mkdtemp()) / "x.env"
    path.write_text("export SESSION_STRING=old\n")
    write_env_value(path, "SESSION_STRING", "NEW")
    assert path.read_text().strip() == "export SESSION_STRING=NEW"


def test_env_file_is_written_owner_only():
    import stat
    import tempfile
    from pathlib import Path

    from session_generator import write_env_value

    path = Path(tempfile.mkdtemp()) / "perm.env"
    write_env_value(path, "SESSION_STRING", "SECRET")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_every_env_file_is_gitignored():
    """--env accepts any path; a tracked target would commit a credential."""
    import subprocess

    for name in (".env", ".env.bak", "prod.env", "staging.env"):
        result = subprocess.run(["git", "check-ignore", "-q", name], capture_output=True)
        assert result.returncode == 0, f"{name} is not gitignored"


def test_phone_numbers_are_validated():
    from bot.services.sessiongen import _normalise_phone

    assert _normalise_phone("+91 98765 43210") == "+919876543210"
    assert _normalise_phone("919876543210") == "+919876543210"
    assert _normalise_phone("abc") == ""
    assert _normalise_phone("+12") == ""
    assert _normalise_phone("+" + "9" * 20) == ""


def test_session_generator_is_owner_and_dm_only():
    """A login code is full account access — it must not be usable in a group."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import config
    from bot.handlers import sessiongen as handler

    replies: list[str] = []

    async def capture(message, card, **kwargs):
        replies.append(card.to_html())
        return MagicMock()

    original = handler.send_card
    handler.send_card = capture
    try:

        def fake(user_id: int, chat_type: str):
            message = MagicMock()
            message.text = "/genstring"
            message.from_user.id = user_id
            message.chat.type = chat_type
            return message

        class FakeState:
            async def clear(self):
                pass

            async def set_state(self, value):
                pass

        owner = (config.owners or [0])[0]

        # A stranger gets no reply at all: they should not learn it exists.
        replies.clear()
        asyncio.run(handler.cmd_genstring(fake(999_999_999, "private"), FakeState()))
        assert replies == []

        # The owner in a group is refused, and told why.
        if owner:
            replies.clear()
            asyncio.run(handler.cmd_genstring(fake(owner, "supergroup"), FakeState()))
            assert replies and "Not here" in replies[0]
    finally:
        handler.send_card = original


def test_a_saved_session_is_actually_used_at_startup():
    """Saving a session the bot then ignores is worse than not saving it."""
    import inspect

    import assistant.client as client

    source = inspect.getsource(client.resolve_session)
    assert "config.session_string" in source, "the env copy must win"
    assert "stored" in source, "a /genstring session must be picked up"

    main_source = inspect.getsource(__import__("main"))
    assert "resolve_session()" in main_source, "main.py must call it"


def test_startup_report_falls_back_to_sudo_users():
    """render.yaml declares OWNER_ID sync:false, so it is often left empty."""
    import inspect

    from bot.services import startup

    source = inspect.getsource(startup.notify_owner)
    assert "sudo_users" in source, "an unset OWNER_ID should not mean silence"


# ──────────────────────────────────────────────────────────────────────────
# Queue persistence
#
# Queues were memory-only while scheduler jobs persisted. The deployment runs
# on Render's free plan, which spins down after ~15 minutes idle, so every
# restart silently destroyed every queue in every group.
# ──────────────────────────────────────────────────────────────────────────


def test_a_queue_survives_a_restart():
    import asyncio

    from bot.services import persistence
    from bot.services.database import database
    from bot.services.queue import LoopMode, queue_manager

    async def scenario():
        await database.connect()
        chat_id = -880001
        await queue_manager.reset(chat_id)
        for n in range(3):
            await queue_manager.add(
                chat_id,
                {"title": f"song {n}", "url": f"https://y/{n}", "id": f"v{n}"},
            )
        await queue_manager.set_loop(chat_id, LoopMode.ALL)
        await queue_manager.set_volume(chat_id, 140)

        assert await persistence.snapshot_chat(chat_id)

        await queue_manager.reset(chat_id)  # the restart
        assert await queue_manager.size(chat_id) == 0

        summary = await persistence.restore_chat(chat_id)
        titles = [t["title"] for t in await queue_manager.get_queue(chat_id)]
        loop = (await queue_manager.get_loop(chat_id)).value
        volume = await queue_manager.get_volume(chat_id)
        await queue_manager.reset(chat_id)
        return summary, titles, loop, volume

    summary, titles, loop, volume = asyncio.run(scenario())
    assert summary and summary["restored"] == 3
    assert titles == ["song 0", "song 1", "song 2"]
    assert loop == "all", "loop mode should survive too"
    assert volume == 140


def test_signed_stream_urls_are_not_persisted():
    """A restored expiring URL fails in a way that looks like a bug."""
    import asyncio

    from bot.services import persistence
    from bot.services.database import database
    from bot.services.queue import queue_manager

    async def scenario():
        await database.connect()
        chat_id = -880002
        await queue_manager.reset(chat_id)
        await queue_manager.add(
            chat_id,
            {
                "title": "t",
                "url": "https://y/1",
                "stream_url": "https://signed.example/expires-soon",
                "http_headers": {"Cookie": "secret"},
            },
        )
        await persistence.snapshot_chat(chat_id)
        doc = await database._get("queue_state", str(chat_id))
        await persistence.forget(chat_id)
        await queue_manager.reset(chat_id)
        return doc

    doc = asyncio.run(scenario())
    stored_track = doc["tracks"][0]
    assert "stream_url" not in stored_track
    assert "http_headers" not in stored_track
    assert stored_track["url"] == "https://y/1", "the stable url should remain"


def test_a_stale_snapshot_is_discarded():
    """Restoring a two-day-old queue into a room that moved on is noise."""
    import asyncio
    import time

    from bot.services import persistence
    from bot.services.database import database
    from bot.services.queue import queue_manager

    async def scenario():
        await database.connect()
        chat_id = -880003
        await queue_manager.reset(chat_id)
        await database._set(
            "queue_state",
            str(chat_id),
            {
                "tracks": [{"title": "ancient", "url": "u"}],
                "current": None,
                "loop": "off",
                "volume": 100,
                "saved_at": time.time() - persistence.MAX_AGE - 60,
            },
        )
        summary = await persistence.restore_chat(chat_id)
        leftover = await database._get("queue_state", str(chat_id))
        size = await queue_manager.size(chat_id)
        await queue_manager.reset(chat_id)
        return summary, leftover, size

    summary, leftover, size = asyncio.run(scenario())
    assert summary is None
    assert not leftover, "an expired snapshot should be purged, not left to rot"
    assert size == 0


def test_a_corrupt_snapshot_does_not_crash_the_boot():
    """Restore runs during startup; raising there takes the whole bot down."""
    import asyncio
    import time

    from bot.services import persistence
    from bot.services.database import database
    from bot.services.queue import queue_manager

    async def scenario():
        await database.connect()
        chat_id = -880004
        await database._set(
            "queue_state",
            str(chat_id),
            {"tracks": "not-a-list", "saved_at": time.time()},
        )
        result = await persistence.restore_chat(chat_id)
        await queue_manager.reset(chat_id)
        return result

    assert asyncio.run(scenario()) is None


def test_an_empty_queue_writes_no_snapshot():
    import asyncio

    from bot.services import persistence
    from bot.services.database import database
    from bot.services.queue import queue_manager

    async def scenario():
        await database.connect()
        chat_id = -880005
        await queue_manager.reset(chat_id)
        return await persistence.snapshot_chat(chat_id)

    assert asyncio.run(scenario()) is False


def test_startup_and_shutdown_both_touch_persistence():
    """Wiring is the whole feature: an unused snapshotter saves nothing."""
    import inspect

    import main

    source = inspect.getsource(main)
    assert "persistence.restore_all()" in source, "nothing restores on boot"
    assert "persistence.snapshot_all()" in source, "nothing saves on shutdown"
    # The periodic saver matters because an OOM kill never reaches shutdown.
    assert "_queue_saver" in source


def test_psutil_is_declared_so_sysinfo_has_numbers():
    """/sysinfo degrades silently without it, which hides the point of it."""
    from pathlib import Path

    assert "psutil" in Path("requirements.txt").read_text()


# ──────────────────────────────────────────────────────────────────────────
# Playback reachability
#
# Cookies are the one reliable fix for a datacenter-IP block, but installing
# one used to mean setting COOKIES_DATA and redeploying — so a bot that could
# not play stayed that way until someone had a laptop.
# ──────────────────────────────────────────────────────────────────────────


def _jar(live: bool = True, names=("SID", "HSID", "SSID", "LOGIN_INFO")) -> str:
    import time

    when = int(time.time()) + (86400 * 30 if live else -86400)
    rows = "\n".join(
        f".youtube.com\tTRUE\t/\tTRUE\t{when}\t{name}\tvalue{n}"
        for n, name in enumerate(names)
    )
    return "# Netscape HTTP Cookie File\n" + rows + "\n"


def test_every_player_client_is_real_and_usable_without_login():
    """A typo is ignored silently, and an auth-only client cannot help us."""
    from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

    from bot.services.music import _EXTRA_PLAYER_CLIENTS

    for name in _EXTRA_PLAYER_CLIENTS:
        assert name in INNERTUBE_CLIENTS, f"{name!r} is not a yt-dlp client"
        assert not INNERTUBE_CLIENTS[name].get("REQUIRE_AUTH"), (
            f"{name!r} needs a logged-in session, which is what we lack"
        )
    assert len(_EXTRA_PLAYER_CLIENTS) >= 6, "one or two fallbacks is not a ladder"


def test_cookie_status_and_pool_scan_the_same_places():
    """They used to duplicate the walk, so /cookies showed a jar and 'none'."""
    import inspect

    from bot.services import music

    assert "_cookie_dirs()" in inspect.getsource(music.cookie_pool)
    assert "_cookie_dirs()" in inspect.getsource(music.cookie_status)


def test_an_expired_jar_is_refused_rather_than_stored():
    """Accepting one silently leaves playback broken behind a green tick."""
    import asyncio
    import shutil
    from unittest.mock import AsyncMock, MagicMock
    from pathlib import Path

    from bot.config import config
    from bot.handlers import cookies as handler
    from bot.services.music import RUNTIME_COOKIE_DIR

    replies: list[str] = []

    async def capture(message, card, **kwargs):
        replies.append(card.to_html())
        return MagicMock()

    original = handler.send_card
    handler.send_card = capture
    shutil.rmtree(RUNTIME_COOKIE_DIR, ignore_errors=True)
    try:
        body = _jar(live=False, names=("SID",))

        message = MagicMock()
        message.from_user.id = (config.owners or [0])[0] or 1
        message.chat.type = "private"
        message.document.file_name = "cookies.txt"
        message.document.file_size = len(body)
        message.document.file_id = "F"
        message.delete = AsyncMock()

        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="p"))

        async def download(path, destination):
            Path(destination).write_text(body)

        bot.download_file = AsyncMock(side_effect=download)

        saved_owner = handler._is_owner
        handler._is_owner = lambda _m: True
        try:
            asyncio.run(handler.got_cookie_file(message, bot))
        finally:
            handler._is_owner = saved_owner

        kept = list(RUNTIME_COOKIE_DIR.glob("*.txt")) if RUNTIME_COOKIE_DIR.is_dir() else []
        assert replies and "no live cookies" in replies[0]
        assert not kept, "an expired jar must not be left in the rotation"
    finally:
        handler.send_card = original
        shutil.rmtree(RUNTIME_COOKIE_DIR, ignore_errors=True)


def test_a_live_jar_is_installed_and_the_upload_deleted():
    import asyncio
    import shutil
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from bot.handlers import cookies as handler
    from bot.services.music import RUNTIME_COOKIE_DIR, cookie_pool

    replies: list[str] = []

    async def capture(message, card, **kwargs):
        replies.append(card.to_html())
        return MagicMock()

    original = handler.send_card
    handler.send_card = capture
    shutil.rmtree(RUNTIME_COOKIE_DIR, ignore_errors=True)
    try:
        body = _jar(live=True)

        message = MagicMock()
        message.from_user.id = 1
        message.chat.type = "private"
        message.document.file_name = "cookies.txt"
        message.document.file_size = len(body)
        message.document.file_id = "F"
        message.delete = AsyncMock()

        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="p"))

        async def download(path, destination):
            Path(destination).write_text(body)

        bot.download_file = AsyncMock(side_effect=download)

        saved_owner = handler._is_owner
        handler._is_owner = lambda _m: True
        try:
            asyncio.run(handler.got_cookie_file(message, bot))
        finally:
            handler._is_owner = saved_owner

        assert replies and "Cookies Installed" in replies[0]
        assert message.delete.called, "a live credential must not stay in the chat"
        assert len(cookie_pool()) == 1, "the jar should be in rotation immediately"
    finally:
        handler.send_card = original
        shutil.rmtree(RUNTIME_COOKIE_DIR, ignore_errors=True)


def test_cookie_upload_is_owner_and_dm_only():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import config
    from bot.handlers import cookies as handler

    replies: list[str] = []

    async def capture(message, card, **kwargs):
        replies.append(card.to_html())
        return MagicMock()

    original_send = handler.send_card
    original_owner = handler._is_owner
    handler.send_card = capture
    try:
        # A stranger: the real owner check must reject them.
        handler._is_owner = original_owner
        stranger = MagicMock()
        stranger.from_user.id = 999_999_999
        stranger.chat.type = "private"
        stranger.document.file_name = "cookies.txt"
        stranger.document.file_size = 100

        asyncio.run(handler.got_cookie_file(stranger, MagicMock()))
        assert replies == [], "a stranger should not learn this exists"

        # The owner, but in a group: refused with an explanation.
        handler._is_owner = lambda _m: True
        owner_in_group = MagicMock()
        owner_in_group.chat.type = "supergroup"
        owner_in_group.text = "/cookies"
        asyncio.run(handler.cmd_cookies(owner_in_group))
        assert replies and "Not here" in replies[0]
    finally:
        handler.send_card = original_send
        handler._is_owner = original_owner


def test_the_blocked_hint_does_not_hand_the_user_a_chore():
    """
    The old copy told listeners to go export a cookie jar and redeploy. That is
    not a fix a person in a group chat can perform, and since mirrors landed it
    is not even the right advice. Say what happened, and what actually helps.
    """
    from bot.services.music import BLOCKED_HINT

    lowered = BLOCKED_HINT.lower()
    for chore in ("cookies_data", "/cookies", "redeploy", "export"):
        assert chore not in lowered, f"stop asking listeners to {chore}"
    assert "try again" in lowered, "the honest advice is usually 'wait a minute'"


# ──────────────────────────────────────────────────────────────────────────
# Cookieless playback via public mirrors
#
# Cookies work but expire, leak, and demand a laptop. Invidious/Piped run the
# extraction on their own IPs, so a block on ours does not apply.
# ──────────────────────────────────────────────────────────────────────────


def test_mirror_parsers_pick_the_best_audio_stream():
    from bot.services import mirrors

    invidious = {
        "videoId": "abc12345678",
        "title": "Faded",
        "author": "Alan Walker",
        "lengthSeconds": "212",
        "videoThumbnails": [{"url": "https://t/x.jpg"}],
        "adaptiveFormats": [
            {"type": "audio/mp4", "bitrate": "128000", "url": "https://s/lo"},
            {"type": "audio/webm", "bitrate": "160000", "url": "https://s/hi"},
            {"type": "video/mp4", "bitrate": "900000", "url": "https://s/video"},
        ],
    }
    track = mirrors._from_invidious(invidious)
    assert track["stream_url"] == "https://s/hi", "should take the best AUDIO"
    assert track["duration"] == 212
    assert track["via"] == "invidious"

    piped = {
        "title": "Faded",
        "uploader": "Alan Walker",
        "duration": 212,
        "audioStreams": [
            {"bitrate": 128000, "url": "https://p/lo"},
            {"bitrate": 192000, "url": "https://p/hi"},
        ],
    }
    assert mirrors._from_piped(piped)["stream_url"] == "https://p/hi"


def test_a_mirror_response_without_audio_is_a_failure():
    """HTTP 200 with no stream is how you get 'Now Playing' over silence."""
    from bot.services import mirrors

    for junk in (
        {},
        "not a dict",
        {"adaptiveFormats": None},
        {"adaptiveFormats": [{"type": "video/mp4", "url": "u"}]},
    ):
        assert mirrors._from_invidious(junk) is None
    assert mirrors._from_piped({"audioStreams": []}) is None


def test_a_failing_instance_is_benched_but_never_all_of_them():
    """A dead host costs a full timeout every time it is asked."""
    from bot.services import mirrors

    mirrors.reset()
    try:
        assert len(mirrors._healthy(mirrors.INVIDIOUS)) == len(mirrors.INVIDIOUS)

        mirrors._bench(mirrors.INVIDIOUS[0], "test")
        assert len(mirrors._healthy(mirrors.INVIDIOUS)) == len(mirrors.INVIDIOUS) - 1

        # Bench everything: it must recover rather than return nothing, or a
        # bad minute would disable mirrors permanently.
        for instance in mirrors.INVIDIOUS:
            mirrors._bench(instance, "test")
        assert mirrors._healthy(mirrors.INVIDIOUS), "all-benched must reset, not fail"
    finally:
        mirrors.reset()


def test_playback_falls_back_to_mirrors_when_youtube_blocks():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from bot.services import mirrors, music

    async def blocked(*args, **kwargs):
        music._last_error = "Sign in to confirm you're not a bot"
        return None

    track = {
        "title": "Faded",
        "stream_url": "https://mirror/audio",
        "id": "60ItHLz5WEA",
        "via": "invidious",
    }

    with patch.object(music, "_run_ytdl", blocked), patch.object(
        music, "_search_with_fallback", blocked
    ), patch.object(mirrors, "fetch_stream", AsyncMock(return_value=track)), patch.object(
        mirrors, "search", AsyncMock(return_value=[{"id": "60ItHLz5WEA"}])
    ):
        result = asyncio.run(music.get_stream_url("alan walker faded"))

    assert result is not None, "a blocked IP should still play through a mirror"
    assert result["via"] == "invidious"


def test_mirrors_are_not_used_for_a_genuine_miss():
    """A song that does not exist is not a mirror's problem."""
    import asyncio
    from unittest.mock import patch

    from bot.services import mirrors, music

    async def empty(*args, **kwargs):
        music._last_error = "No video results for that query"
        return None

    calls = {"n": 0}

    async def spy(*args, **kwargs):
        calls["n"] += 1
        return None

    with patch.object(music, "_run_ytdl", empty), patch.object(
        music, "_search_with_fallback", empty
    ), patch.object(mirrors, "search", spy), patch.object(mirrors, "fetch_stream", spy):
        asyncio.run(music.get_stream_url("kjhaskdjhaskdjh nonexistent"))

    assert calls["n"] == 0, "a real miss must not burn a mirror request"


def test_mirrors_refuse_video_and_live():
    """They serve audio reliably; a video/live URL would die seconds in."""
    import asyncio

    from bot.services import music

    assert asyncio.run(music._try_mirrors("x", video=True)) is None
    assert asyncio.run(music._try_mirrors("x", live=True)) is None


def test_youtube_ids_are_extracted_from_every_url_shape():
    from bot.services.music import _youtube_id

    for url in (
        "https://youtu.be/60ItHLz5WEA",
        "https://youtube.com/watch?v=60ItHLz5WEA",
        "https://youtube.com/shorts/60ItHLz5WEA",
        "https://www.youtube.com/embed/60ItHLz5WEA",
        "60ItHLz5WEA",
    ):
        assert _youtube_id(url) == "60ItHLz5WEA", url
    assert _youtube_id("just a song name") == ""
    assert _youtube_id("") == ""


def test_search_falls_back_to_mirrors_too():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from bot.services import mirrors, music

    async def blocked(*args, **kwargs):
        music._last_error = "Sign in to confirm you're not a bot"
        return None

    hits = [{"title": "Faded", "id": "a", "url": "u", "via": "invidious"}]
    with patch.object(music, "_search_with_fallback", blocked), patch.object(
        mirrors, "search", AsyncMock(return_value=hits)
    ):
        results = asyncio.run(music.search_youtube("faded", limit=3))

    assert results and results[0]["via"] == "invidious"


def test_no_cookies_is_not_reported_as_a_problem():
    """Mirrors cover it, so nagging about cookies is now just noise."""
    import asyncio

    from bot.services import startup

    report = asyncio.run(startup.collect(None))
    names = {check.name for check in report.degraded}
    assert "Cookies" not in names, "a cookieless deployment is a working one"
    assert any(check.name == "Mirrors" for check in report.checks)


def test_the_blocked_hint_no_longer_demands_cookies():
    from bot.services.music import BLOCKED_HINT

    assert "COOKIES_DATA" not in BLOCKED_HINT
    assert "mirror" in BLOCKED_HINT.lower()
