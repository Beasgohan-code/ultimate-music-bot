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
    assert progress_bar(0, None) == "🔴 LIVE"
    bar = progress_bar(50, 100, width=10)
    assert "🔘" in bar and len(bar.replace("🔘", "")) == 9


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
