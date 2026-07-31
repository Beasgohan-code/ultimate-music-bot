"""Start, help, and settings handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import main_menu_kb, settings_kb
from bot.utils.formatters import help_card, welcome_message

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = (await message.bot.get_me()).first_name
    await message.answer(
        welcome_message(name),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_card(), parse_mode="HTML", reply_markup=main_menu_kb())


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    from bot.handlers.callbacks import send_player_panel

    await send_player_panel(message)


@router.callback_query(F.data == "menu:help")
async def cb_help(query: CallbackQuery) -> None:
    await query.message.edit_text(help_card(), parse_mode="HTML", reply_markup=main_menu_kb())
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
    name = (await query.bot.get_me()).first_name
    await query.message.edit_text(
        welcome_message(name),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
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
