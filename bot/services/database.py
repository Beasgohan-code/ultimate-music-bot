"""Unified persistence layer.

Uses MongoDB when ``MONGO_URI`` is configured and transparently falls back to
atomic JSON files under ``data/`` otherwise, so the bot is fully functional
with zero external services.  Every public method is async and safe to call
before :meth:`Database.connect` has run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from bot.config import DATA_DIR, config

logger = logging.getLogger(__name__)

#: Collections mirrored to JSON when Mongo is unavailable.
_COLLECTIONS = (
    "chats",  # per-chat settings
    "users",  # per-user profile + language
    "playlists",  # saved user playlists
    "favorites",  # per-user favourite tracks
    "stats",  # global counters + top tracks
    "blacklist",  # blacklisted chats
    "banned",  # globally banned users
    "auth",  # per-chat authorised (non-admin) users
    "cache",  # resolved track cache (query -> track)
    "schedules",  # scheduled playback jobs
)


class _JsonBackend:
    """Tiny document store backed by one JSON file per collection."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._dirty: set[str] = set()

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def _lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def _load(self, name: str) -> dict[str, Any]:
        if name in self._cache:
            return self._cache[name]
        path = self._path(name)
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception as exc:
                logger.warning("Could not read %s: %s — starting fresh", path.name, exc)
                try:  # keep a copy of the damaged file for forensics
                    path.rename(path.with_suffix(".corrupt.json"))
                except OSError:
                    pass
        self._cache[name] = data
        return data

    def _flush(self, name: str) -> None:
        """Atomic write — temp file + replace so a crash never truncates data."""
        path = self._path(name)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self._cache.get(name, {}), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as exc:
            logger.error("Could not persist %s: %s", path.name, exc)
        finally:
            self._dirty.discard(name)

    async def get(self, collection: str, key: str) -> dict[str, Any]:
        async with self._lock(collection):
            return dict(self._load(collection).get(key, {}))

    async def set(self, collection: str, key: str, document: dict[str, Any]) -> None:
        async with self._lock(collection):
            self._load(collection)[key] = document
            self._flush(collection)

    async def update(self, collection: str, key: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._lock(collection):
            data = self._load(collection)
            doc = dict(data.get(key, {}))
            doc.update(patch)
            data[key] = doc
            self._flush(collection)
            return doc

    async def delete(self, collection: str, key: str) -> bool:
        async with self._lock(collection):
            data = self._load(collection)
            if key in data:
                del data[key]
                self._flush(collection)
                return True
            return False

    async def all(self, collection: str) -> dict[str, Any]:
        async with self._lock(collection):
            return dict(self._load(collection))

    async def keys(self, collection: str) -> list[str]:
        async with self._lock(collection):
            return list(self._load(collection))


class Database:
    """High level API used by the rest of the bot."""

    def __init__(self) -> None:
        self._json = _JsonBackend(DATA_DIR / "store")
        self._mongo: Any = None
        self._db: Any = None
        self.backend = "json"

    # ── lifecycle ───────────────────────────────────────────────────────
    async def connect(self) -> str:
        """Attempt a Mongo connection; silently stay on JSON when unavailable."""
        if not config.mongo_enabled:
            logger.info("Storage backend: local JSON (%s)", self._json.root)
            return self.backend
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._mongo = AsyncIOMotorClient(config.mongo_uri, serverSelectionTimeoutMS=6000)
            await self._mongo.admin.command("ping")
            self._db = self._mongo[config.mongo_db_name]
            self.backend = "mongo"
            logger.info("Storage backend: MongoDB (%s)", config.mongo_db_name)
        except Exception as exc:
            logger.warning("MongoDB unavailable (%s) — using local JSON storage", exc)
            self._mongo = None
            self._db = None
            self.backend = "json"
        return self.backend

    async def close(self) -> None:
        if self._mongo is not None:
            self._mongo.close()
            self._mongo = None
            self._db = None

    # ── generic document helpers ────────────────────────────────────────
    async def _get(self, collection: str, key: str) -> dict[str, Any]:
        if self._db is not None:
            try:
                doc = await self._db[collection].find_one({"_id": key})
                return {k: v for k, v in (doc or {}).items() if k != "_id"}
            except Exception as exc:
                logger.error("Mongo read failed (%s/%s): %s", collection, key, exc)
        return await self._json.get(collection, key)

    async def _set(self, collection: str, key: str, document: dict[str, Any]) -> None:
        if self._db is not None:
            try:
                await self._db[collection].update_one(
                    {"_id": key}, {"$set": document}, upsert=True
                )
                return
            except Exception as exc:
                logger.error("Mongo write failed (%s/%s): %s", collection, key, exc)
        await self._json.set(collection, key, document)

    async def _update(self, collection: str, key: str, patch: dict[str, Any]) -> dict[str, Any]:
        if self._db is not None:
            try:
                await self._db[collection].update_one({"_id": key}, {"$set": patch}, upsert=True)
                return await self._get(collection, key)
            except Exception as exc:
                logger.error("Mongo update failed (%s/%s): %s", collection, key, exc)
        return await self._json.update(collection, key, patch)

    async def _delete(self, collection: str, key: str) -> bool:
        if self._db is not None:
            try:
                result = await self._db[collection].delete_one({"_id": key})
                return bool(result.deleted_count)
            except Exception as exc:
                logger.error("Mongo delete failed (%s/%s): %s", collection, key, exc)
        return await self._json.delete(collection, key)

    async def _all(self, collection: str) -> dict[str, Any]:
        if self._db is not None:
            try:
                out: dict[str, Any] = {}
                async for doc in self._db[collection].find({}):
                    key = str(doc.pop("_id"))
                    out[key] = doc
                return out
            except Exception as exc:
                logger.error("Mongo scan failed (%s): %s", collection, exc)
        return await self._json.all(collection)

    # ── global settings ─────────────────────────────────────────────────
    #: Values that belong to the deployment rather than to a chat or user —
    #: the assistant session string being the one that matters. On a PaaS with
    #: an ephemeral filesystem this is the only place a runtime-generated
    #: credential can survive a redeploy.
    async def get_setting(self, key: str, default: Any = None) -> Any:
        doc = await self._get("settings", "global")
        value = doc.get(key)
        return default if value is None else value

    async def set_setting(self, key: str, value: Any) -> Any:
        await self._update("settings", "global", {key: value, "updated_at": time.time()})
        return value

    async def delete_setting(self, key: str) -> None:
        await self._update("settings", "global", {key: None})

    # ── chat settings ───────────────────────────────────────────────────
    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        return await self._get("chats", str(chat_id))

    async def get_chat_value(self, chat_id: int, key: str, default: Any = None) -> Any:
        doc = await self.get_chat(chat_id)
        value = doc.get(key)
        return default if value is None else value

    async def set_chat_value(self, chat_id: int, key: str, value: Any) -> Any:
        await self._update("chats", str(chat_id), {key: value, "updated_at": time.time()})
        return value

    async def known_chats(self) -> list[int]:
        keys = await self._all("chats")
        out: list[int] = []
        for key in keys:
            try:
                out.append(int(key))
            except ValueError:
                continue
        return out

    async def touch_chat(self, chat_id: int, title: str = "") -> None:
        patch: dict[str, Any] = {"last_seen": time.time()}
        if title:
            patch["title"] = title[:128]
        await self._update("chats", str(chat_id), patch)

    # ── users ───────────────────────────────────────────────────────────
    async def get_user(self, user_id: int) -> dict[str, Any]:
        return await self._get("users", str(user_id))

    async def touch_user(self, user_id: int, name: str = "") -> None:
        patch: dict[str, Any] = {"last_seen": time.time()}
        if name:
            patch["name"] = name[:128]
        await self._update("users", str(user_id), patch)

    async def set_user_value(self, user_id: int, key: str, value: Any) -> Any:
        await self._update("users", str(user_id), {key: value})
        return value

    async def get_user_value(self, user_id: int, key: str, default: Any = None) -> Any:
        doc = await self.get_user(user_id)
        value = doc.get(key)
        return default if value is None else value

    async def known_users(self) -> list[int]:
        keys = await self._all("users")
        out: list[int] = []
        for key in keys:
            try:
                out.append(int(key))
            except ValueError:
                continue
        return out

    # ── blacklist / gban / auth ─────────────────────────────────────────
    async def blacklist_chat(self, chat_id: int, reason: str = "") -> None:
        await self._set("blacklist", str(chat_id), {"reason": reason, "at": time.time()})

    async def whitelist_chat(self, chat_id: int) -> bool:
        return await self._delete("blacklist", str(chat_id))

    async def is_blacklisted(self, chat_id: int) -> bool:
        return bool(await self._get("blacklist", str(chat_id)))

    async def blacklisted_chats(self) -> list[int]:
        return [int(k) for k in (await self._all("blacklist")) if k.lstrip("-").isdigit()]

    async def ban_user(self, user_id: int, reason: str = "") -> None:
        await self._set("banned", str(user_id), {"reason": reason, "at": time.time()})

    async def unban_user(self, user_id: int) -> bool:
        return await self._delete("banned", str(user_id))

    async def is_banned(self, user_id: int) -> bool:
        return bool(await self._get("banned", str(user_id)))

    async def banned_users(self) -> list[int]:
        return [int(k) for k in (await self._all("banned")) if k.lstrip("-").isdigit()]

    async def add_auth_user(self, chat_id: int, user_id: int, name: str = "") -> bool:
        doc = await self._get("auth", str(chat_id))
        users = dict(doc.get("users", {}))
        if str(user_id) in users:
            return False
        users[str(user_id)] = {"name": name, "at": time.time()}
        await self._set("auth", str(chat_id), {"users": users})
        return True

    async def remove_auth_user(self, chat_id: int, user_id: int) -> bool:
        doc = await self._get("auth", str(chat_id))
        users = dict(doc.get("users", {}))
        if str(user_id) not in users:
            return False
        users.pop(str(user_id))
        await self._set("auth", str(chat_id), {"users": users})
        return True

    async def auth_users(self, chat_id: int) -> dict[str, Any]:
        doc = await self._get("auth", str(chat_id))
        return dict(doc.get("users", {}))

    async def is_auth_user(self, chat_id: int, user_id: int) -> bool:
        return str(user_id) in (await self.auth_users(chat_id))

    # ── favourites ──────────────────────────────────────────────────────
    async def get_favorites(self, user_id: int) -> list[dict[str, Any]]:
        doc = await self._get("favorites", str(user_id))
        items = doc.get("items", [])
        return list(items) if isinstance(items, list) else []

    async def add_favorite(self, user_id: int, track: dict[str, Any], limit: int = 200) -> bool:
        items = await self.get_favorites(user_id)
        tid = str(track.get("id") or track.get("url") or track.get("title", ""))
        if any(str(i.get("id")) == tid for i in items):
            return False
        items.append(
            {
                "id": tid,
                "title": track.get("title", "Unknown"),
                "artist": track.get("artist", ""),
                "url": track.get("url", ""),
                "duration": track.get("duration"),
                "added_at": time.time(),
            }
        )
        del items[:-limit]
        await self._set("favorites", str(user_id), {"items": items})
        return True

    async def remove_favorite(self, user_id: int, index: int) -> dict[str, Any] | None:
        items = await self.get_favorites(user_id)
        if not 0 <= index < len(items):
            return None
        removed = items.pop(index)
        await self._set("favorites", str(user_id), {"items": items})
        return removed

    # ── saved playlists ─────────────────────────────────────────────────
    async def get_playlists(self, user_id: int) -> dict[str, list[dict[str, Any]]]:
        doc = await self._get("playlists", str(user_id))
        lists = doc.get("lists", {})
        return lists if isinstance(lists, dict) else {}

    async def save_playlist(
        self, user_id: int, name: str, tracks: list[dict[str, Any]], limit: int = 100
    ) -> int:
        lists = await self.get_playlists(user_id)
        slim = [
            {
                "title": t.get("title", "Unknown"),
                "artist": t.get("artist", ""),
                "url": t.get("url", ""),
                "duration": t.get("duration"),
            }
            for t in tracks[:limit]
        ]
        lists[name[:48]] = slim
        await self._set("playlists", str(user_id), {"lists": lists})
        return len(slim)

    async def delete_playlist(self, user_id: int, name: str) -> bool:
        lists = await self.get_playlists(user_id)
        if name not in lists:
            return False
        lists.pop(name)
        await self._set("playlists", str(user_id), {"lists": lists})
        return True

    # ── statistics / top tracks ─────────────────────────────────────────
    async def record_play(self, chat_id: int, user_id: int, track: dict[str, Any]) -> None:
        """Increment global, per-chat, per-user and per-track counters."""
        title = str(track.get("title", "Unknown"))[:120]
        key = str(track.get("id") or track.get("url") or title)

        doc = await self._get("stats", "global")
        top_tracks: dict[str, Any] = dict(doc.get("top_tracks", {}))
        entry = dict(top_tracks.get(key, {"title": title, "count": 0}))
        entry["title"] = title
        entry["url"] = track.get("url", "")
        entry["count"] = int(entry.get("count", 0)) + 1
        top_tracks[key] = entry
        if len(top_tracks) > 500:  # keep the map bounded
            top_tracks = dict(
                sorted(top_tracks.items(), key=lambda kv: -int(kv[1].get("count", 0)))[:400]
            )
        await self._set(
            "stats",
            "global",
            {"top_tracks": top_tracks, "total_plays": int(doc.get("total_plays", 0)) + 1},
        )

        chat_doc = await self._get("stats", f"chat:{chat_id}")
        chat_top: dict[str, Any] = dict(chat_doc.get("top_tracks", {}))
        c_entry = dict(chat_top.get(key, {"title": title, "count": 0}))
        c_entry["title"] = title
        c_entry["url"] = track.get("url", "")
        c_entry["count"] = int(c_entry.get("count", 0)) + 1
        chat_top[key] = c_entry
        if len(chat_top) > 200:
            chat_top = dict(sorted(chat_top.items(), key=lambda kv: -int(kv[1].get("count", 0)))[:150])
        await self._set(
            "stats",
            f"chat:{chat_id}",
            {"top_tracks": chat_top, "plays": int(chat_doc.get("plays", 0)) + 1},
        )

        if user_id:
            user_doc = await self._get("stats", f"user:{user_id}")
            await self._set(
                "stats", f"user:{user_id}", {"plays": int(user_doc.get("plays", 0)) + 1}
            )

    async def top_tracks(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        key = f"chat:{chat_id}" if chat_id else "global"
        doc = await self._get("stats", key)
        tracks = doc.get("top_tracks", {})
        if not isinstance(tracks, dict):
            return []
        ranked = sorted(tracks.values(), key=lambda t: -int(t.get("count", 0)))
        return ranked[:limit]

    async def top_users(self, limit: int = 10) -> list[dict[str, Any]]:
        stats = await self._all("stats")
        users = []
        for key, doc in stats.items():
            if not key.startswith("user:"):
                continue
            uid = key.split(":", 1)[1]
            profile = await self.get_user(int(uid)) if uid.isdigit() else {}
            users.append(
                {"user_id": uid, "name": profile.get("name", uid), "plays": int(doc.get("plays", 0))}
            )
        return sorted(users, key=lambda u: -u["plays"])[:limit]

    async def top_chats(self, limit: int = 10) -> list[dict[str, Any]]:
        stats = await self._all("stats")
        chats = []
        for key, doc in stats.items():
            if not key.startswith("chat:"):
                continue
            cid = key.split(":", 1)[1]
            profile = await self.get_chat(int(cid)) if cid.lstrip("-").isdigit() else {}
            chats.append(
                {"chat_id": cid, "title": profile.get("title", cid), "plays": int(doc.get("plays", 0))}
            )
        return sorted(chats, key=lambda c: -c["plays"])[:limit]

    async def global_counters(self) -> dict[str, int]:
        doc = await self._get("stats", "global")
        return {
            "total_plays": int(doc.get("total_plays", 0)),
            "served_chats": len(await self.known_chats()),
            "served_users": len(await self.known_users()),
        }

    # ── resolved-track cache (spot-seek style reuse) ─────────────────────
    async def cache_track(self, query_key: str, track: dict[str, Any]) -> None:
        await self._set(
            "cache",
            query_key.lower()[:180],
            {
                "title": track.get("title"),
                "artist": track.get("artist"),
                "url": track.get("url"),
                "duration": track.get("duration"),
                "thumbnail": track.get("thumbnail"),
                "file_id": track.get("file_id", ""),
                "at": time.time(),
            },
        )

    async def cached_track(self, query_key: str, max_age: float = 86_400 * 14) -> dict[str, Any] | None:
        doc = await self._get("cache", query_key.lower()[:180])
        if not doc:
            return None
        if time.time() - float(doc.get("at", 0)) > max_age:
            return None
        return doc


database = Database()
