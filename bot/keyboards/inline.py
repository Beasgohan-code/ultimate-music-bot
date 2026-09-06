"""Premium inline keyboards with styled buttons."""

from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    SwitchInlineQueryChosenChat,
)

from bot.config import config
from bot.utils.formatters import format_duration



def start_kb(bot_username: str = "") -> InlineKeyboardMarkup:
    """The /start keyboard.

    Uses the newer Bot API button features: coloured styles, a chat-picker
    "add me" button, an inline-query launcher, and a copy-to-clipboard button.
    Rows adapt to whichever support links are actually configured.
    """
    uname = bot_username or config.bot_username
    rows: list[list[InlineKeyboardButton]] = []

    # ── Primary call to action: let the user pick a group to add me to.
    if uname:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ",
                    url=f"https://t.me/{uname}?startgroup=true&admin="
                    "delete_messages+restrict_members+pin_messages+invite_users+manage_video_chats",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        )

    # ── Try it right now, without leaving the chat.
    rows.append(
        [
            InlineKeyboardButton(
                text="🔎 sᴇᴀʀᴄʜ ɪɴʟɪɴᴇ",
                switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
                    query="",
                    allow_user_chats=True,
                    allow_group_chats=True,
                    allow_channel_chats=True,
                ),
            ),
            InlineKeyboardButton(
                text="🎧 ᴛʀʏ ᴀ sᴏɴɢ",
                switch_inline_query_current_chat="",
                style=ButtonStyle.SUCCESS,
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(text="📖 ʜᴇʟᴘ", callback_data="menu:help"),
            InlineKeyboardButton(text="✨ ꜰᴇᴀᴛᴜʀᴇs", callback_data="menu:features"),
            InlineKeyboardButton(text="⚙️ sᴇᴛᴛɪɴɢs", callback_data="menu:settings"),
        ]
    )

    # ── Owner / support / updates, only when configured.
    third: list[InlineKeyboardButton] = []
    if config.owner_username:
        third.append(
            InlineKeyboardButton(text="👑 ᴏᴡɴᴇʀ", url=f"https://t.me/{config.owner_username}")
        )
    if config.support_chat:
        third.append(
            InlineKeyboardButton(
                text="💬 sᴜᴘᴘᴏʀᴛ", url=_tg_link(config.support_chat), style=ButtonStyle.LINK
            )
        )
    if config.support_channel:
        third.append(
            InlineKeyboardButton(
                text="📢 ᴜᴘᴅᴀᴛᴇs", url=_tg_link(config.support_channel), style=ButtonStyle.LINK
            )
        )
    if third:
        rows.append(third)

    # ── Share link: copy_text puts it on the clipboard with no extra screen.
    if uname:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔗 sʜᴀʀᴇ ᴍᴇ",
                    copy_text=CopyTextButton(text=f"https://t.me/{uname}"),
                ),
                InlineKeyboardButton(text="📊 sᴛᴀᴛs", callback_data="menu:stats"),
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def features_kb() -> InlineKeyboardMarkup:
    """Sub-menu shown behind the ✨ Features button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 ᴍᴜsɪᴄ", callback_data="feat:music"),
                InlineKeyboardButton(text="🛡 ɢʀᴏᴜᴘ", callback_data="feat:group"),
            ],
            [
                InlineKeyboardButton(text="⚡ ᴘᴏᴡᴇʀ ᴜsᴇʀ", callback_data="feat:power"),
                InlineKeyboardButton(text="📖 ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅs", callback_data="menu:help"),
            ],
            [InlineKeyboardButton(text="◂ ʙᴀᴄᴋ", callback_data="menu:back")],
        ]
    )


def _tg_link(handle: str) -> str:
    """Accept @name, name, or a full URL and always return a valid https link."""
    handle = handle.strip()
    if handle.startswith("http://") or handle.startswith("https://"):
        return handle
    return f"https://t.me/{handle.lstrip('@')}"


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎵 Play Song", callback_data="menu:play", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="🔍 Search", callback_data="menu:search", style=ButtonStyle.PRIMARY),
            ],
            [
                InlineKeyboardButton(text="📻 Radio", callback_data="menu:radio"),
                InlineKeyboardButton(text="🎭 Mood", callback_data="menu:mood"),
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
                InlineKeyboardButton(text="🖥 OS Dashboard", callback_data="menu:os", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="⭐ Favorites", callback_data="menu:favs"),
            ],
            [
                InlineKeyboardButton(text="📖 Help", callback_data="menu:help"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
            ],
        ]
    )


def _progress_label(elapsed: int, duration: int) -> str:
    """A one-line "0:42 ──◉──── 3:15" label for the player's top row.

    Button text has no monospace, so this stays short and uses the knob
    position alone to convey progress.
    """
    from bot.utils.cards import fmt_duration, progress_bar

    bar = progress_bar(elapsed, duration, width=10)
    # fmt_duration treats 0 as "unknown" and renders an em dash, which is
    # wrong for a track that has simply just started.
    left = "0:00" if not elapsed else fmt_duration(elapsed)
    return f"{left} {bar} {fmt_duration(duration)}"


def player_panel_kb(
    is_playing: bool = False,
    is_paused: bool = False,
    *,
    elapsed: int | None = None,
    duration: int | None = None,
) -> InlineKeyboardMarkup:
    """Transport controls for the now-playing card.

    Fourteen buttons in five rows read as a wall of text on a phone. The
    controls people press constantly (skip, pause, volume) get the top rows as
    bare glyphs — universally understood and easy to hit — and the rest moves
    behind a single "More" row.

    When elapsed/duration are known a progress row is added on top, the way
    DAXXMUSIC does it: the bar sits in the markup rather than the text, so
    refreshing it only edits the keyboard and never re-sends the card.
    """
    play_pause = "▶️" if is_paused else "⏸"
    play_data = "ctrl:resume" if is_paused else "ctrl:pause"

    rows: list[list[InlineKeyboardButton]] = []
    if duration and duration > 0 and elapsed is not None:
        # Inert label — a callback that does nothing would just confuse.
        rows.append(
            [
                InlineKeyboardButton(
                    text=_progress_label(elapsed, duration),
                    callback_data="ctrl:noop",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows + [
            # Transport, in the order a physical player has them.
            [
                InlineKeyboardButton(text="⏮", callback_data="ctrl:replay"),
                InlineKeyboardButton(
                    text=play_pause, callback_data=play_data, style=ButtonStyle.PRIMARY
                ),
                InlineKeyboardButton(
                    text="⏭", callback_data="ctrl:skip", style=ButtonStyle.PRIMARY
                ),
                InlineKeyboardButton(
                    text="⏹", callback_data="ctrl:stop", style=ButtonStyle.DANGER
                ),
            ],
            [
                InlineKeyboardButton(text="🔉", callback_data="ctrl:vol_down"),
                InlineKeyboardButton(text="🔊", callback_data="ctrl:vol_up"),
                InlineKeyboardButton(text="🔁", callback_data="ctrl:loop"),
                InlineKeyboardButton(text="🔀", callback_data="ctrl:shuffle"),
            ],
            [
                InlineKeyboardButton(text="📋 ǫᴜᴇᴜᴇ", callback_data="ctrl:queue"),
                InlineKeyboardButton(text="📝 ʟʏʀɪᴄs", callback_data="ctrl:lyrics"),
                InlineKeyboardButton(text="⋯ ᴍᴏʀᴇ", callback_data="ctrl:more"),
            ],
        ]
    )


def player_more_kb() -> InlineKeyboardMarkup:
    """Secondary player actions, behind the ⋯ More button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💡 sᴜɢɢᴇsᴛ", callback_data="ctrl:suggest"),
                InlineKeyboardButton(text="⭐ ꜰᴀᴠᴏᴜʀɪᴛᴇ", callback_data="ctrl:fav"),
            ],
            [
                InlineKeyboardButton(text="🎬 ᴠɪᴅᴇᴏ", callback_data="ctrl:video"),
                InlineKeyboardButton(text="📡 ʟɪᴠᴇ", callback_data="ctrl:live"),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 ᴄʟᴇᴀʀ ǫᴜᴇᴜᴇ",
                    callback_data="ctrl:clear",
                    style=ButtonStyle.DANGER,
                ),
            ],
            [InlineKeyboardButton(text="◂ ʙᴀᴄᴋ", callback_data="ctrl:back")],
        ]
    )


def search_results_kb(results: list[dict], prefix: str = "play") -> InlineKeyboardMarkup:
    """Numbered picker for search results.

    One full-width button per result pushed the list off screen and truncated
    the titles anyway — the card above already shows them. Numbers in rows of
    four keep the whole result set visible and thumb-reachable.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, r in enumerate(results[:8]):
        row.append(
            InlineKeyboardButton(
                text=f"{i + 1}",
                callback_data=f"{prefix}:{r.get('id', i)}",
                style=ButtonStyle.PRIMARY if i == 0 else None,
            )
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
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


def radio_kb() -> InlineKeyboardMarkup:
    from bot.services.radio import list_stations

    buttons = []
    row = []
    for key, s in list_stations():
        row.append(InlineKeyboardButton(
            text=f"{s['emoji']} {s['genre']}",
            callback_data=f"radio:{key}",
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:back", style=ButtonStyle.PRIMARY),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def mood_kb() -> InlineKeyboardMarkup:
    from bot.services.music import MOOD_QUERIES

    buttons = []
    row = []
    moods = list(MOOD_QUERIES.keys())
    emojis = {"chill": "🌿", "party": "🎉", "workout": "💪", "sad": "😢", "happy": "😊",
              "focus": "🎯", "sleep": "😴", "romantic": "❤️", "gaming": "🎮", "retro": "📼"}
    for mood in moods:
        row.append(InlineKeyboardButton(
            text=f"{emojis.get(mood, '🎵')} {mood.title()}",
            callback_data=f"mood:{mood}",
            style=ButtonStyle.PRIMARY if mood == "chill" else None,
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:back"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def favorites_kb(favs: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for i, f in enumerate(favs[:8]):
        title = f.get("title", "?")[:35]
        buttons.append([
            InlineKeyboardButton(
                text=f"▶️ {title}",
                callback_data=f"favplay:{i}",
                style=ButtonStyle.SUCCESS if i == 0 else None,
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:back", style=ButtonStyle.PRIMARY),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def os_dashboard_kb(is_playing: bool = False, is_paused: bool = False) -> InlineKeyboardMarkup:
    play_pause = "▶️ Resume" if is_paused else ("⏸ Pause" if is_playing else "▶️ Play")
    play_data = "ctrl:resume" if is_paused else "ctrl:pause"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=play_pause, callback_data=play_data, style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="⏭ Skip", callback_data="ctrl:skip", style=ButtonStyle.PRIMARY),
                InlineKeyboardButton(text="⏹ Stop", callback_data="ctrl:stop", style=ButtonStyle.DANGER),
            ],
            [
                InlineKeyboardButton(text="📻 Radio", callback_data="menu:radio"),
                InlineKeyboardButton(text="🎭 Mood", callback_data="menu:mood"),
                InlineKeyboardButton(text="⭐ Favs", callback_data="menu:favs"),
            ],
            [
                InlineKeyboardButton(text="📋 Queue", callback_data="ctrl:queue"),
                InlineKeyboardButton(text="🎛 Panel", callback_data="ctrl:panel", style=ButtonStyle.SUCCESS),
                InlineKeyboardButton(text="📊 Stats", callback_data="menu:stats"),
            ],
        ]
    )


def track_links_kb(track: dict) -> InlineKeyboardMarkup:
    """Buttons under a track-info card.

    Shown when someone opens a ``/start info_<id>`` deep link, so the actions
    are "listen elsewhere" and "play it here" rather than player transport
    controls — there is nothing playing in a PM to control.
    """
    rows: list[list[InlineKeyboardButton]] = []

    url = track.get("url") or ""
    if url:
        source = "ʏᴏᴜᴛᴜʙᴇ"
        lowered = url.lower()
        if "soundcloud" in lowered:
            source = "sᴏᴜɴᴅᴄʟᴏᴜᴅ"
        elif "spotify" in lowered:
            source = "sᴘᴏᴛɪғʏ"
        rows.append([InlineKeyboardButton(text=f"▶ ᴏᴘᴇɴ ᴏɴ {source}", url=url)])

    # Prefill the query so the user can drop this track into any group.
    title = (track.get("title") or "").strip()
    if title:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎧 ᴘʟᴀʏ ɪɴ ᴀ ɢʀᴏᴜᴘ",
                    switch_inline_query=title[:64],
                )
            ]
        )

    support = _tg_link(config.support_chat) if getattr(config, "support_chat", "") else ""
    last: list[InlineKeyboardButton] = []
    if support:
        last.append(InlineKeyboardButton(text="✨ sᴜᴘᴘᴏʀᴛ", url=support))
    last.append(InlineKeyboardButton(text="✖ ᴄʟᴏsᴇ", callback_data="ui:close"))
    rows.append(last)

    return InlineKeyboardMarkup(inline_keyboard=rows)
