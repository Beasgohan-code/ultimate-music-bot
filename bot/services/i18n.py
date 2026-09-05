"""Lightweight multilingual string catalogue.

Locales live in ``bot/locales/<code>.json``.  English is the source of truth —
any key missing from a translation transparently falls back to English, so a
partial translation never breaks the bot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANG = "en"

#: Display metadata for every shipped language.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "🇬🇧 English",
    "es": "🇪🇸 Español",
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "pt": "🇧🇷 Português",
    "ru": "🇷🇺 Русский",
    "hi": "🇮🇳 हिन्दी",
    "bn": "🇧🇩 বাংলা",
    "ar": "🇸🇦 العربية",
    "tr": "🇹🇷 Türkçe",
    "id": "🇮🇩 Indonesia",
    "fa": "🇮🇷 فارسی",
}


class Translator:
    """Loads and serves locale catalogues."""

    def __init__(self, locales_dir: Path = LOCALES_DIR) -> None:
        self.locales_dir = locales_dir
        self._catalogues: dict[str, dict[str, str]] = {}
        self.reload()

    # ── loading ─────────────────────────────────────────────────────────
    def reload(self) -> None:
        self._catalogues.clear()
        if not self.locales_dir.is_dir():
            logger.warning("Locales directory %s missing — running English-only", self.locales_dir)
            self._catalogues[DEFAULT_LANG] = {}
            return
        for path in sorted(self.locales_dir.glob("*.json")):
            code = path.stem.lower()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - corrupt file guard
                logger.error("Could not parse locale %s: %s", path.name, exc)
                continue
            self._catalogues[code] = {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        self._catalogues.setdefault(DEFAULT_LANG, {})

    # ── lookup ──────────────────────────────────────────────────────────
    @property
    def languages(self) -> list[str]:
        """Available language codes, English always first."""
        codes = sorted(self._catalogues)
        if DEFAULT_LANG in codes:
            codes.remove(DEFAULT_LANG)
            codes.insert(0, DEFAULT_LANG)
        return codes

    def has(self, lang: str) -> bool:
        return lang.lower() in self._catalogues

    def display_name(self, lang: str) -> str:
        return LANGUAGE_NAMES.get(lang.lower(), lang.upper())

    def get(self, key: str, lang: str = DEFAULT_LANG, /, **kwargs: Any) -> str:
        """Resolve ``key`` for ``lang`` with ``{placeholder}`` formatting."""
        lang = (lang or DEFAULT_LANG).lower()
        catalogue = self._catalogues.get(lang) or {}
        template = catalogue.get(key)
        if template is None:
            template = self._catalogues.get(DEFAULT_LANG, {}).get(key)
        if template is None:
            # Surface the key itself so missing strings are obvious but harmless.
            return key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template

    def catalogue(self, lang: str) -> dict[str, str]:
        """Full merged catalogue for a language (English base + overrides)."""
        base = dict(self._catalogues.get(DEFAULT_LANG, {}))
        base.update(self._catalogues.get((lang or DEFAULT_LANG).lower(), {}))
        return base

    def coverage(self, lang: str) -> float:
        """Fraction of English keys translated for ``lang`` (0.0 – 1.0)."""
        english = self._catalogues.get(DEFAULT_LANG, {})
        if not english:
            return 1.0
        other = self._catalogues.get((lang or DEFAULT_LANG).lower(), {})
        if lang.lower() == DEFAULT_LANG:
            return 1.0
        translated = sum(1 for key in english if key in other)
        return translated / len(english)


translator = Translator()


class Lang:
    """Bound translator for a single language — ``lang("key", name="x")``."""

    __slots__ = ("code",)

    def __init__(self, code: str = DEFAULT_LANG) -> None:
        self.code = (code or DEFAULT_LANG).lower()

    def __call__(self, key: str, **kwargs: Any) -> str:
        return translator.get(key, self.code, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Lang({self.code!r})"


async def get_lang(chat_id: int) -> Lang:
    """Resolve the language configured for a chat (falls back to config default)."""
    from bot.config import config
    from bot.services.database import database

    code = await database.get_chat_value(chat_id, "language", config.default_language)
    if not translator.has(str(code)):
        code = config.default_language if translator.has(config.default_language) else DEFAULT_LANG
    return Lang(str(code))
