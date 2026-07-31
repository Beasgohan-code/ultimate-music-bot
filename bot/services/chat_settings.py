"""Per-chat settings storage."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "chat_settings.json"

DEFAULTS: dict[str, Any] = {
    "default_video": False,
    "autoleave_enabled": True,
    "speed": 1.0,
}


class ChatSettings:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if SETTINGS_FILE.exists():
                self._data = json.loads(SETTINGS_FILE.read_text())
        except Exception as exc:
            logger.warning("Could not load chat settings: %s", exc)

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            logger.warning("Could not save chat settings: %s", exc)

    async def get(self, chat_id: int, key: str) -> Any:
        async with self._lock:
            chat = self._data.get(str(chat_id), {})
            return chat.get(key, DEFAULTS.get(key))

    async def set(self, chat_id: int, key: str, value: Any) -> Any:
        async with self._lock:
            key_str = str(chat_id)
            if key_str not in self._data:
                self._data[key_str] = dict(DEFAULTS)
            self._data[key_str][key] = value
            self._save()
            return value

    async def toggle(self, chat_id: int, key: str) -> Any:
        current = await self.get(chat_id, key)
        if isinstance(current, bool):
            return await self.set(chat_id, key, not current)
        return current

    async def summary(self, chat_id: int) -> dict[str, Any]:
        async with self._lock:
            return {**DEFAULTS, **self._data.get(str(chat_id), {})}


chat_settings = ChatSettings()
