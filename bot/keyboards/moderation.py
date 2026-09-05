"""Inline keyboards for moderation and group settings."""

from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.moderation import LOCK_TYPES


def warn_actions_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Remove Warning",
                    callback_data=f"warn:remove:{user_id}",
                    style=ButtonStyle.SUCCESS,
                ),
            ]
        ]
    )


def settings_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 Music", callback_data="gs:music", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="🌐 Language", callback_data="gs:lang"),
            ],
            [
                InlineKeyboardButton(text="🔒 Locks", callback_data="gs:locks"),
                InlineKeyboardButton(text="⚠️ Warns", callback_data="gs:warns"),
            ],
            [
                InlineKeyboardButton(text="👋 Greetings", callback_data="gs:welcome"),
                InlineKeyboardButton(text="🌊 Anti-flood", callback_data="gs:flood"),
            ],
            [
                InlineKeyboardButton(text="🚫 Blacklist", callback_data="gs:blacklist"),
                InlineKeyboardButton(text="🧹 Clean Mode", callback_data="gs:clean"),
            ],
            [
                InlineKeyboardButton(text="✖️ Close", callback_data="gs:close", style=ButtonStyle.DANGER),
            ],
        ]
    )


def settings_back_kb(extra: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append(
        [InlineKeyboardButton(text="◀️ Back", callback_data="gs:root", style=ButtonStyle.PRIMARY)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def locks_kb(locks: dict[str, bool], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    keys = [k for k in LOCK_TYPES if k != "all"]
    total_pages = max(1, (len(keys) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = keys[page * per_page : (page + 1) * per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(chunk), 2):
        row = []
        for key in chunk[idx : idx + 2]:
            on = locks.get(key, False)
            row.append(
                InlineKeyboardButton(
                    text=f"{'🔒' if on else '🔓'} {key}",
                    callback_data=f"lock:toggle:{key}:{page}",
                    style=ButtonStyle.DANGER if on else None,
                )
            )
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"lock:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"lock:page:{page + 1}"))
    rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text="◀️ Back", callback_data="gs:root", style=ButtonStyle.PRIMARY)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_kb(languages: list[tuple[str, str]], current: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(languages), 2):
        row = [
            InlineKeyboardButton(
                text=("✅ " if code == current else "") + name,
                callback_data=f"lang:set:{code}",
                style=ButtonStyle.SUCCESS if code == current else None,
            )
            for code, name in languages[idx : idx + 2]
        ]
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="◀️ Back", callback_data="gs:root", style=ButtonStyle.PRIMARY)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def toggle_kb(items: list[tuple[str, str, bool]], back: str = "gs:root") -> InlineKeyboardMarkup:
    """Generic on/off toggle list — ``(label, callback, state)``."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if state else '❌'} {label}",
                callback_data=cb,
                style=ButtonStyle.SUCCESS if state else None,
            )
        ]
        for label, cb, state in items
    ]
    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data=back, style=ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_menu_kb(categories: list[tuple[str, str]], page: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(categories), 2):
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"help:{key}")
                for key, label in categories[idx : idx + 2]
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="✖️ Close", callback_data="help:close", style=ButtonStyle.DANGER)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️ All Categories", callback_data="help:root", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="✖️ Close", callback_data="help:close", style=ButtonStyle.DANGER),
            ]
        ]
    )
