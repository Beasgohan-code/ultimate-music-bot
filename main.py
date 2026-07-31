"""Ultimate Music Bot — main entry point."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from assistant.client import create_assistant
from bot.config import config
from bot.handlers import admin, advanced, callbacks, dashboard, extras, inline_mode, misc, play, start
from bot.middlewares.stats import StatsMiddleware
from bot.services.autoleave import auto_leave
from bot.services.stream import stream_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Start the bot"),
    BotCommand(command="play", description="Play audio in voice chat"),
    BotCommand(command="song", description="Search & play a song"),
    BotCommand(command="cplay", description="Channel/group play"),
    BotCommand(command="vplay", description="Stream video (MKV/MP4)"),
    BotCommand(command="vstream", description="Live stream (m3u8)"),
    BotCommand(command="playlist", description="Load YouTube playlist"),
    BotCommand(command="radio", description="Internet radio stations"),
    BotCommand(command="mood", description="Mood-based playlists"),
    BotCommand(command="search", description="Interactive search"),
    BotCommand(command="lyrics", description="Get song lyrics"),
    BotCommand(command="suggest", description="Song suggestions"),
    BotCommand(command="fav", description="Save to favorites"),
    BotCommand(command="favs", description="View favorites"),
    BotCommand(command="download", description="Download as MP3"),
    BotCommand(command="join", description="How to add assistant"),
    BotCommand(command="info", description="Current track info"),
    BotCommand(command="source", description="Supported sources"),
    BotCommand(command="os", description="Premium OS dashboard"),
    BotCommand(command="panel", description="Control panel"),
    BotCommand(command="queue", description="View queue"),
    BotCommand(command="help", description="All commands"),
]


async def main() -> None:
    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    assistant = create_assistant()
    calls = stream_manager.setup(assistant)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(StatsMiddleware())

    dp.include_router(start.router)
    dp.include_router(play.router)
    dp.include_router(advanced.router)
    dp.include_router(misc.router)
    dp.include_router(extras.router)
    dp.include_router(dashboard.router)
    dp.include_router(admin.router)
    dp.include_router(inline_mode.router)
    dp.include_router(callbacks.router)

    logger.info("Starting assistant userbot…")
    await assistant.start()
    await calls.start()
    await auto_leave.start()
    await bot.set_my_commands(BOT_COMMANDS)

    logger.info("Ultimate Music Bot is live!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await auto_leave.stop()
        await calls.stop()
        await assistant.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
