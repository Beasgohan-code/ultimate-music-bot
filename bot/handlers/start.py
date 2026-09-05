"""Start, help, and settings handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.keyboards.inline import features_kb, main_menu_kb, settings_kb, start_kb
from bot.utils.cards import feature_card, welcome_card
from bot.utils.formatters import welcome_message
from bot.utils.rich import send_card

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    me = await message.bot.get_me()
    user = message.from_user
    card = welcome_card(
        config.bot_name or me.first_name,
        me.username or "",
        first_name=user.first_name if user else "there",
        user_username=(user.username or "") if user else "",
    )
    kb = start_kb(me.username or "")

    # A start image makes the card feel like a product, not a log line.
    if config.start_image_url:
        try:
            await message.answer_photo(
                config.start_image_url, caption=card.to_html(), parse_mode="HTML", reply_markup=kb
            )
            return
        except Exception:
            pass  # bad/expired URL — fall through to the text card
    await send_card(message, card, reply_markup=kb)


@router.callback_query(F.data == "menu:features")
async def cb_features(query: CallbackQuery) -> None:
    try:
        await query.message.edit_text(
            feature_card("overview").to_html(), parse_mode="HTML", reply_markup=features_kb()
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data.startswith("feat:"))
async def cb_feature_section(query: CallbackQuery) -> None:
    section = query.data.split(":", 1)[1]
    try:
        await query.message.edit_text(
            feature_card(section).to_html(), parse_mode="HTML", reply_markup=features_kb()
        )
    except Exception:
        pass
    await query.answer()


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    from bot.handlers.callbacks import send_player_panel

    await send_player_panel(message)


@router.callback_query(F.data == "menu:help")
async def cb_help(query: CallbackQuery) -> None:
    from bot.handlers.settings import _help_root_card, HELP_CATEGORIES
    from bot.keyboards.moderation import help_menu_kb

    cats = [(k, v[0]) for k, v in HELP_CATEGORIES.items()]
    try:
        await query.message.edit_text(
            _help_root_card().to_html(), parse_mode="HTML", reply_markup=help_menu_kb(cats)
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data == "menu:settings")
async def cb_settings(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold

    text = (
        f"⚙️ {bold('Settings')}\n\n"
        f"{bq('Configure playback preferences for this chat.')}"
    )
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=settings_kb())
    await query.answer()


@router.callback_query(F.data == "menu:back")
async def cb_back(query: CallbackQuery) -> None:
    me = await query.bot.get_me()
    # Keep the greeting personal on the way back; rebuilding it without the
    # user made the card silently downgrade to "Hey, there".
    user = query.from_user
    try:
        await query.message.edit_text(
            welcome_card(
                config.bot_name or me.first_name,
                me.username or "",
                first_name=user.first_name if user else "there",
                user_username=(user.username or "") if user else "",
            ).to_html(),
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data == "menu:play")
async def cb_menu_play(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold, italic

    await query.message.edit_text(
        f"🎵 {bold('Play a Song')}\n\n"
        f"{bq('Send a song name, YouTube URL, or reply to an audio/video file.')}\n\n"
        f"{italic('Example: /song never gonna give you up')}",
        parse_mode="HTML",
    )
    await query.answer("Use /song <query> or /play <query>")


@router.callback_query(F.data == "menu:search")
async def cb_menu_search(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold, italic

    await query.message.edit_text(
        f"🔍 {bold('Search')}\n\n"
        f"{bq('Use /search followed by a query to pick from results.')}\n\n"
        f"{italic('Example: /search lofi hip hop')}",
        parse_mode="HTML",
    )
    await query.answer("Use /search <query>")


@router.callback_query(F.data == "menu:lyrics")
async def cb_menu_lyrics(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold, italic

    await query.message.edit_text(
        f"📝 {bold('Lyrics')}\n\n"
        f"{bq('Get lyrics for any song.')}\n\n"
        f"{italic('Example: /lyrics artist - song title')}",
        parse_mode="HTML",
    )
    await query.answer("Use /lyrics <song>")


@router.callback_query(F.data == "menu:suggest")
async def cb_menu_suggest(query: CallbackQuery) -> None:
    from bot.utils.formatters import bq, bold, italic

    await query.message.edit_text(
        f"💡 {bold('Suggestions')}\n\n"
        f"{bq('Get song recommendations based on a track or mood.')}\n\n"
        f"{italic('Example: /suggest chill vibes')}",
        parse_mode="HTML",
    )
    await query.answer("Use /suggest <query>")


@router.callback_query(F.data == "menu:radio")
async def cb_menu_radio(query: CallbackQuery) -> None:
    from bot.keyboards.inline import radio_kb
    from bot.utils.formatters import radio_card

    await query.message.edit_text(radio_card(), parse_mode="HTML", reply_markup=radio_kb())
    await query.answer()


@router.callback_query(F.data == "menu:mood")
async def cb_menu_mood(query: CallbackQuery) -> None:
    from bot.keyboards.inline import mood_kb
    from bot.utils.formatters import mood_card

    await query.message.edit_text(mood_card(), parse_mode="HTML", reply_markup=mood_kb())
    await query.answer()


@router.callback_query(F.data == "menu:favs")
async def cb_menu_favs(query: CallbackQuery) -> None:
    from bot.keyboards.inline import favorites_kb
    from bot.services.favorites import favorites_store
    from bot.utils.formatters import favorites_card

    user_id = query.from_user.id if query.from_user else 0
    favs = await favorites_store.list(user_id)
    await query.message.edit_text(
        favorites_card(favs),
        parse_mode="HTML",
        reply_markup=favorites_kb(favs) if favs else None,
    )
    await query.answer()


@router.callback_query(F.data == "menu:os")
async def cb_menu_os(query: CallbackQuery) -> None:
    from bot.handlers.dashboard import cmd_os

    class _Msg:
        def __init__(self, q):
            self.chat = q.message.chat
            self.from_user = q.from_user
            self.answer = q.message.answer

    await cmd_os(_Msg(query))
    await query.answer()


@router.callback_query(F.data == "menu:stats")
async def cb_menu_stats(query: CallbackQuery) -> None:
    from bot.services.history import history_tracker
    from bot.services.stats import bot_stats
    from bot.utils.formatters import stats_card

    stats = await bot_stats.summary()
    recent = await history_tracker.get_global_history(5)
    await query.message.answer(stats_card(stats, recent), parse_mode="HTML")
    await query.answer()
