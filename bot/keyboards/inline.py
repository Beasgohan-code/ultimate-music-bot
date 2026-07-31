"""Premium inline keyboards with styled buttons."""

from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.formatters import format_duration


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 Play Song", callback_data="menu:play", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="🔍 Search", callback_data="menu:search", style=ButtonStyle.PRIMARY),
            ],
            [
                InlineKeyboardButton(text="📝 Lyrics", callback_data="menu:lyrics"),
                InlineKeyboardButton(text="💡 Suggest", callback_data="menu:suggest"),
            ],
            [
                InlineKeyboardButton(text="📋 Queue", callback_data="ctrl:queue"),
                InlineKeyboardButton(text="🎛 Panel", callback_data="ctrl:panel", style=ButtonStyle.SUCCESS),
            ],
            [
                InlineKeyboardButton(text="📖 Help", callback_data="menu:help"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
            ],
        ]
    )


def player_panel_kb(is_playing: bool = False, is_paused: bool = False) -> InlineKeyboardMarkup:
    play_pause = "▶️ Resume" if is_paused else ("⏸ Pause" if is_playing else "▶️ Play")
    play_data = "ctrl:resume" if is_paused else "ctrl:pause"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏮ Replay", callback_data="ctrl:replay"),
                InlineKeyboardButton(text=play_pause, callback_data=play_data, style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="⏭ Skip", callback_data="ctrl:skip", style=ButtonStyle.PRIMARY),
            ],
            [
                InlineKeyboardButton(text="⏹ Stop", callback_data="ctrl:stop", style=ButtonStyle.DANGER),
                InlineKeyboardButton(text="🔁 Loop", callback_data="ctrl:loop"),
                InlineKeyboardButton(text="🔀 Shuffle", callback_data="ctrl:shuffle"),
            ],
            [
                InlineKeyboardButton(text="🔉 Vol -", callback_data="ctrl:vol_down"),
                InlineKeyboardButton(text="🔊 Vol +", callback_data="ctrl:vol_up"),
                InlineKeyboardButton(text="🗑 Clear", callback_data="ctrl:clear"),
            ],
            [
                InlineKeyboardButton(text="📋 Queue", callback_data="ctrl:queue"),
                InlineKeyboardButton(text="📝 Lyrics", callback_data="ctrl:lyrics"),
                InlineKeyboardButton(text="💡 Suggest", callback_data="ctrl:suggest"),
            ],
            [
                InlineKeyboardButton(text="🎬 Video Mode", callback_data="ctrl:video"),
                InlineKeyboardButton(text="📡 Live Stream", callback_data="ctrl:live"),
            ],
        ]
    )


def search_results_kb(results: list[dict], prefix: str = "play") -> InlineKeyboardMarkup:
    buttons = []
    for i, r in enumerate(results[:8]):
        title = r.get("title", "?")[:40]
        dur = format_duration(r.get("duration"))
        buttons.append([
            InlineKeyboardButton(
                text=f"{i + 1}. {title} ({dur})",
                callback_data=f"{prefix}:{r.get('id', i)}",
                style=ButtonStyle.PRIMARY if i == 0 else None,
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="❌ Cancel", callback_data="ctrl:cancel", style=ButtonStyle.DANGER),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def suggestions_kb(suggestions: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, s in enumerate(suggestions[:6]):
        title = s.get("title", "?")[:38]
        buttons.append([
            InlineKeyboardButton(
                text=f"💡 {title}",
                callback_data=f"suggest:{s.get('id', i)}",
                style=ButtonStyle.SUCCESS if i < 2 else None,
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="🔄 More", callback_data="ctrl:more_suggest"),
        InlineKeyboardButton(text="❌ Close", callback_data="ctrl:cancel", style=ButtonStyle.DANGER),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def queue_pagination_kb(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"queue:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"queue:page:{page + 1}"))

    rows = [nav] if nav else []
    rows.append([
        InlineKeyboardButton(text="🔀 Shuffle", callback_data="ctrl:shuffle"),
        InlineKeyboardButton(text="🗑 Clear", callback_data="ctrl:clear", style=ButtonStyle.DANGER),
    ])
    rows.append([
        InlineKeyboardButton(text="🎛 Panel", callback_data="ctrl:panel", style=ButtonStyle.PRIMARY),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes", callback_data=f"confirm:{action}:yes", style=ButtonStyle.SUCCESS),
                InlineKeyboardButton(text="❌ No", callback_data=f"confirm:{action}:no", style=ButtonStyle.DANGER),
            ]
        ]
    )


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔊 Volume", callback_data="settings:volume"),
                InlineKeyboardButton(text="🔁 Loop Mode", callback_data="settings:loop"),
            ],
            [
                InlineKeyboardButton(text="📡 Auto-leave", callback_data="settings:autoleave"),
                InlineKeyboardButton(text="🎬 Default Video", callback_data="settings:video"),
            ],
            [
                InlineKeyboardButton(text="◀️ Back", callback_data="menu:back", style=ButtonStyle.PRIMARY),
            ],
        ]
    )
