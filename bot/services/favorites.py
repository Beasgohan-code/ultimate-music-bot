"""Per-user favorites storage (JSON file)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FAV_FILE = DATA_DIR / "favorites.json"


class FavoritesStore:
    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if FAV_FILE.exists():
                self._data = json.loads(FAV_FILE.read_text())
        except Exception as exc:
            logger.warning("Could not load favorites: %s", exc)
            self._data = {}

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            FAV_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            logger.warning("Could not save favorites: %s", exc)

    async def add(self, user_id: int, track: dict[str, Any]) -> bool:
        key = str(user_id)
        async with self._lock:
            favs = self._data.setdefault(key, [])
            tid = track.get("id") or track.get("url", "")
            if any(f.get("id") == tid for f in favs):
                return False
            favs.append({
                "id": tid,
                "title": track.get("title", "Unknown"),
                "artist": track.get("artist", ""),
                "url": track.get("url", ""),
                "duration": track.get("duration"),
            })
            if len(favs) > 100:
                favs.pop(0)
            self._save()
            return True

    async def remove(self, user_id: int, index: int) -> dict[str, Any] | None:
        key = str(user_id)
        async with self._lock:
            favs = self._data.get(key, [])
            if 0 <= index < len(favs):
                removed = favs.pop(index)
                self._save()
                return removed
            return None

    async def list(self, user_id: int) -> list[dict[str, Any]]:
        return list(self._data.get(str(user_id), []))

    async def count(self, user_id: int) -> int:
        return len(self._data.get(str(user_id), []))


favorites_store = FavoritesStore()
