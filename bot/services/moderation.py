"""Group management state: warns, notes, filters, locks, rules, welcome, AFK.

Modelled on Miss Rose / Marie semantics but stored through the unified
:mod:`bot.services.database` layer so everything works with or without Mongo.
"""

from __future__ import annotations

import re
import time
from typing import Any

from bot.services.database import database

# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_WARN_LIMIT = 3
DEFAULT_WARN_ACTION = "mute"  # mute | kick | ban
WARN_ACTIONS = ("mute", "kick", "ban")

#: Lockable message categories (Rose's /lock types).
LOCK_TYPES: dict[str, str] = {
    "all": "Every message type below",
    "text": "Plain text messages",
    "media": "Photos, videos and animations",
    "photo": "Photos",
    "video": "Videos",
    "audio": "Audio tracks",
    "voice": "Voice notes",
    "document": "Files and documents",
    "sticker": "Stickers",
    "gif": "GIFs / animations",
    "poll": "Polls",
    "game": "Games",
    "location": "Locations and venues",
    "contact": "Shared contacts",
    "url": "Messages containing links",
    "forward": "Forwarded messages",
    "mention": "Messages that @mention users",
    "inline": "Messages sent via inline bots",
    "emojigame": "Dice / darts / slot animations",
    "bots": "Non-admins adding new bots",
}

#: Commands that /disable can switch off per chat.
DISABLEABLE = {
    "ping", "id", "info", "stats", "lyrics", "suggest", "search", "song",
    "afk", "rules", "notes", "filters", "source", "mood", "radio", "history",
    "fav", "favs", "download", "top", "active", "speed",
}


def _now() -> float:
    return time.time()


class ModerationStore:
    """Async facade over per-chat moderation documents."""

    # ── warnings ────────────────────────────────────────────────────────
    async def warn_settings(self, chat_id: int) -> dict[str, Any]:
        doc = await database.get_chat(chat_id)
        return {
            "limit": int(doc.get("warn_limit", DEFAULT_WARN_LIMIT)),
            "action": str(doc.get("warn_action", DEFAULT_WARN_ACTION)),
        }

    async def set_warn_limit(self, chat_id: int, limit: int) -> int:
        limit = max(1, min(50, limit))
        await database.set_chat_value(chat_id, "warn_limit", limit)
        return limit

    async def set_warn_action(self, chat_id: int, action: str) -> str:
        action = action.lower()
        if action not in WARN_ACTIONS:
            action = DEFAULT_WARN_ACTION
        await database.set_chat_value(chat_id, "warn_action", action)
        return action

    async def _warns_doc(self, chat_id: int) -> dict[str, Any]:
        return await database._get("chats", f"warns:{chat_id}")  # noqa: SLF001

    async def add_warn(
        self, chat_id: int, user_id: int, reason: str = ""
    ) -> tuple[int, int, str]:
        """Record a warning. Returns ``(count, limit, action)``."""
        doc = await self._warns_doc(chat_id)
        users = dict(doc.get("users", {}))
        entry = dict(users.get(str(user_id), {"count": 0, "reasons": []}))
        entry["count"] = int(entry.get("count", 0)) + 1
        reasons = list(entry.get("reasons", []))
        reasons.append({"reason": reason or "No reason given", "at": _now()})
        entry["reasons"] = reasons[-20:]
        users[str(user_id)] = entry
        await database._set("chats", f"warns:{chat_id}", {"users": users})  # noqa: SLF001
        settings = await self.warn_settings(chat_id)
        return entry["count"], settings["limit"], settings["action"]

    async def get_warns(self, chat_id: int, user_id: int) -> dict[str, Any]:
        doc = await self._warns_doc(chat_id)
        users = doc.get("users", {})
        return dict(users.get(str(user_id), {"count": 0, "reasons": []}))

    async def reset_warns(self, chat_id: int, user_id: int) -> bool:
        doc = await self._warns_doc(chat_id)
        users = dict(doc.get("users", {}))
        if str(user_id) not in users:
            return False
        users.pop(str(user_id))
        await database._set("chats", f"warns:{chat_id}", {"users": users})  # noqa: SLF001
        return True

    async def remove_one_warn(self, chat_id: int, user_id: int) -> int:
        doc = await self._warns_doc(chat_id)
        users = dict(doc.get("users", {}))
        entry = dict(users.get(str(user_id), {"count": 0, "reasons": []}))
        count = max(0, int(entry.get("count", 0)) - 1)
        entry["count"] = count
        entry["reasons"] = list(entry.get("reasons", []))[:-1]
        if count == 0:
            users.pop(str(user_id), None)
        else:
            users[str(user_id)] = entry
        await database._set("chats", f"warns:{chat_id}", {"users": users})  # noqa: SLF001
        return count

    async def all_warned(self, chat_id: int) -> dict[str, Any]:
        doc = await self._warns_doc(chat_id)
        return dict(doc.get("users", {}))

    # ── notes ───────────────────────────────────────────────────────────
    async def save_note(self, chat_id: int, name: str, content: dict[str, Any]) -> None:
        doc = await database._get("chats", f"notes:{chat_id}")  # noqa: SLF001
        notes = dict(doc.get("notes", {}))
        notes[name.lower()[:64]] = {**content, "at": _now()}
        await database._set("chats", f"notes:{chat_id}", {"notes": notes})  # noqa: SLF001

    async def get_note(self, chat_id: int, name: str) -> dict[str, Any] | None:
        doc = await database._get("chats", f"notes:{chat_id}")  # noqa: SLF001
        return doc.get("notes", {}).get(name.lower()[:64])

    async def delete_note(self, chat_id: int, name: str) -> bool:
        doc = await database._get("chats", f"notes:{chat_id}")  # noqa: SLF001
        notes = dict(doc.get("notes", {}))
        if name.lower() not in notes:
            return False
        notes.pop(name.lower())
        await database._set("chats", f"notes:{chat_id}", {"notes": notes})  # noqa: SLF001
        return True

    async def list_notes(self, chat_id: int) -> list[str]:
        doc = await database._get("chats", f"notes:{chat_id}")  # noqa: SLF001
        return sorted(doc.get("notes", {}))

    # ── custom filters ──────────────────────────────────────────────────
    async def save_filter(self, chat_id: int, trigger: str, content: dict[str, Any]) -> None:
        doc = await database._get("chats", f"filters:{chat_id}")  # noqa: SLF001
        filters = dict(doc.get("filters", {}))
        filters[trigger.lower()[:64]] = {**content, "at": _now()}
        await database._set("chats", f"filters:{chat_id}", {"filters": filters})  # noqa: SLF001

    async def delete_filter(self, chat_id: int, trigger: str) -> bool:
        doc = await database._get("chats", f"filters:{chat_id}")  # noqa: SLF001
        filters = dict(doc.get("filters", {}))
        if trigger.lower() not in filters:
            return False
        filters.pop(trigger.lower())
        await database._set("chats", f"filters:{chat_id}", {"filters": filters})  # noqa: SLF001
        return True

    async def list_filters(self, chat_id: int) -> dict[str, Any]:
        doc = await database._get("chats", f"filters:{chat_id}")  # noqa: SLF001
        return dict(doc.get("filters", {}))

    async def match_filter(self, chat_id: int, text: str) -> dict[str, Any] | None:
        """Whole-word, case-insensitive trigger match (Rose behaviour)."""
        if not text:
            return None
        filters = await self.list_filters(chat_id)
        if not filters:
            return None
        lowered = text.lower()
        for trigger, content in filters.items():
            if " " in trigger:
                if trigger in lowered:
                    return content
            elif re.search(rf"(?<![\w]){re.escape(trigger)}(?![\w])", lowered):
                return content
        return None

    # ── word blacklist ──────────────────────────────────────────────────
    async def add_blacklist_word(self, chat_id: int, word: str) -> bool:
        doc = await database._get("chats", f"blwords:{chat_id}")  # noqa: SLF001
        words = set(doc.get("words", []))
        if word.lower() in words:
            return False
        words.add(word.lower())
        await database._set("chats", f"blwords:{chat_id}", {"words": sorted(words)})  # noqa: SLF001
        return True

    async def remove_blacklist_word(self, chat_id: int, word: str) -> bool:
        doc = await database._get("chats", f"blwords:{chat_id}")  # noqa: SLF001
        words = set(doc.get("words", []))
        if word.lower() not in words:
            return False
        words.discard(word.lower())
        await database._set("chats", f"blwords:{chat_id}", {"words": sorted(words)})  # noqa: SLF001
        return True

    async def blacklist_words(self, chat_id: int) -> list[str]:
        doc = await database._get("chats", f"blwords:{chat_id}")  # noqa: SLF001
        return list(doc.get("words", []))

    async def match_blacklist(self, chat_id: int, text: str) -> str | None:
        words = await self.blacklist_words(chat_id)
        if not words or not text:
            return None
        lowered = text.lower()
        for word in words:
            if "*" in word:  # simple glob support
                pattern = re.escape(word).replace(r"\*", r"\w*")
                if re.search(rf"(?<![\w]){pattern}(?![\w])", lowered):
                    return word
            elif re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", lowered):
                return word
        return None

    async def blacklist_mode(self, chat_id: int) -> str:
        return str(await database.get_chat_value(chat_id, "blacklist_mode", "delete"))

    async def set_blacklist_mode(self, chat_id: int, mode: str) -> str:
        mode = mode.lower()
        if mode not in ("delete", "warn", "mute", "kick", "ban"):
            mode = "delete"
        await database.set_chat_value(chat_id, "blacklist_mode", mode)
        return mode

    # ── locks ───────────────────────────────────────────────────────────
    async def locks(self, chat_id: int) -> dict[str, bool]:
        doc = await database._get("chats", f"locks:{chat_id}")  # noqa: SLF001
        return {k: bool(v) for k, v in doc.get("locks", {}).items()}

    async def set_lock(self, chat_id: int, lock_type: str, locked: bool) -> bool:
        current = await self.locks(chat_id)
        if lock_type == "all":
            for key in LOCK_TYPES:
                if key != "all":
                    current[key] = locked
        else:
            current[lock_type] = locked
        await database._set("chats", f"locks:{chat_id}", {"locks": current})  # noqa: SLF001
        return locked

    async def is_locked(self, chat_id: int, lock_type: str) -> bool:
        return bool((await self.locks(chat_id)).get(lock_type, False))

    # ── anti-flood ──────────────────────────────────────────────────────
    async def flood_limit(self, chat_id: int) -> int:
        return int(await database.get_chat_value(chat_id, "flood_limit", 0))

    async def set_flood_limit(self, chat_id: int, limit: int) -> int:
        limit = 0 if limit <= 0 else max(3, min(100, limit))
        await database.set_chat_value(chat_id, "flood_limit", limit)
        return limit

    async def flood_action(self, chat_id: int) -> str:
        return str(await database.get_chat_value(chat_id, "flood_action", "mute"))

    async def set_flood_action(self, chat_id: int, action: str) -> str:
        action = action.lower()
        if action not in ("mute", "kick", "ban"):
            action = "mute"
        await database.set_chat_value(chat_id, "flood_action", action)
        return action

    # ── rules ───────────────────────────────────────────────────────────
    async def get_rules(self, chat_id: int) -> str:
        return str(await database.get_chat_value(chat_id, "rules", "") or "")

    async def set_rules(self, chat_id: int, text: str) -> None:
        await database.set_chat_value(chat_id, "rules", text[:3500])

    async def clear_rules(self, chat_id: int) -> None:
        await database.set_chat_value(chat_id, "rules", "")

    # ── welcome / goodbye ───────────────────────────────────────────────
    async def welcome_settings(self, chat_id: int) -> dict[str, Any]:
        doc = await database.get_chat(chat_id)
        return {
            "enabled": bool(doc.get("welcome_enabled", True)),
            "text": str(doc.get("welcome_text", "") or ""),
            "goodbye_enabled": bool(doc.get("goodbye_enabled", False)),
            "goodbye_text": str(doc.get("goodbye_text", "") or ""),
            "clean": bool(doc.get("clean_welcome", True)),
            "last_id": int(doc.get("last_welcome_id", 0) or 0),
        }

    async def set_welcome(self, chat_id: int, text: str) -> None:
        await database.set_chat_value(chat_id, "welcome_text", text[:2000])

    async def set_goodbye(self, chat_id: int, text: str) -> None:
        await database.set_chat_value(chat_id, "goodbye_text", text[:2000])

    async def toggle_welcome(self, chat_id: int, enabled: bool) -> bool:
        await database.set_chat_value(chat_id, "welcome_enabled", enabled)
        return enabled

    async def toggle_goodbye(self, chat_id: int, enabled: bool) -> bool:
        await database.set_chat_value(chat_id, "goodbye_enabled", enabled)
        return enabled

    async def remember_welcome_msg(self, chat_id: int, message_id: int) -> None:
        await database.set_chat_value(chat_id, "last_welcome_id", message_id)

    # ── disabled commands ───────────────────────────────────────────────
    async def disabled_commands(self, chat_id: int) -> list[str]:
        doc = await database._get("chats", f"disabled:{chat_id}")  # noqa: SLF001
        return list(doc.get("commands", []))

    async def disable_command(self, chat_id: int, command: str) -> bool:
        command = command.lower().lstrip("/")
        if command not in DISABLEABLE:
            return False
        current = set(await self.disabled_commands(chat_id))
        current.add(command)
        await database._set("chats", f"disabled:{chat_id}", {"commands": sorted(current)})  # noqa: SLF001
        return True

    async def enable_command(self, chat_id: int, command: str) -> bool:
        command = command.lower().lstrip("/")
        current = set(await self.disabled_commands(chat_id))
        if command not in current:
            return False
        current.discard(command)
        await database._set("chats", f"disabled:{chat_id}", {"commands": sorted(current)})  # noqa: SLF001
        return True

    async def is_command_disabled(self, chat_id: int, command: str) -> bool:
        return command.lower().lstrip("/") in (await self.disabled_commands(chat_id))

    # ── AFK ─────────────────────────────────────────────────────────────
    async def set_afk(self, user_id: int, reason: str = "") -> None:
        await database._set(  # noqa: SLF001
            "users", f"afk:{user_id}", {"reason": reason[:200], "since": _now()}
        )

    async def clear_afk(self, user_id: int) -> dict[str, Any] | None:
        doc = await database._get("users", f"afk:{user_id}")  # noqa: SLF001
        if not doc:
            return None
        await database._delete("users", f"afk:{user_id}")  # noqa: SLF001
        return doc

    async def get_afk(self, user_id: int) -> dict[str, Any] | None:
        doc = await database._get("users", f"afk:{user_id}")  # noqa: SLF001
        return doc or None

    # ── reports ─────────────────────────────────────────────────────────
    async def reports_enabled(self, chat_id: int) -> bool:
        return bool(await database.get_chat_value(chat_id, "reports_enabled", True))

    async def set_reports(self, chat_id: int, enabled: bool) -> bool:
        await database.set_chat_value(chat_id, "reports_enabled", enabled)
        return enabled


moderation = ModerationStore()
