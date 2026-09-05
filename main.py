"""Ultimate Music Bot — main entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllPrivateChats

# Pyrogram still calls the deprecated asyncio.get_event_loop() at import time on
# Python 3.12+, which raises when no loop is running. Patch before importing it.
_original_get_event_loop = asyncio.get_event_loop


def _patched_get_event_loop():
    try:
        return _original_get_event_loop()
    except RuntimeError as ex:
        if "no current event loop" in str(ex).lower():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop
        raise


asyncio.get_event_loop = _patched_get_event_loop

from assistant.client import create_assistant  # noqa: E402
from bot.config import DATA_DIR, config  # noqa: E402
from bot.handlers import (  # noqa: E402
    admin,
    advanced,
    callbacks,
    controls,
    dashboard,
    extras,
    grouptools,
    inline_mode,
    misc,
    moderation as moderation_handlers,
    play,
    settings as settings_handlers,
    start,
)
from bot.middlewares.enforcement import EnforcementMiddleware  # noqa: E402
from bot.middlewares.gatekeeper import GatekeeperMiddleware  # noqa: E402
from bot.services.autoleave import auto_leave  # noqa: E402
from bot.services.database import database  # noqa: E402
from bot.services.i18n import translator  # noqa: E402
from bot.services.stream import stream_manager  # noqa: E402


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(
            RotatingFileHandler(
                DATA_DIR / "bot.log", maxBytes=4_000_000, backupCount=2, encoding="utf-8"
            )
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy in ("pyrogram", "pytgcalls", "aiogram.event", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger("ultimate")

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Start the bot"),
    BotCommand(command="play", description="Play a song in the voice chat"),
    BotCommand(command="vplay", description="Play a video in the voice chat"),
    BotCommand(command="search", description="Search and pick a track"),
    BotCommand(command="queue", description="Show the queue"),
    BotCommand(command="skip", description="Skip the current track"),
    BotCommand(command="pause", description="Pause playback"),
    BotCommand(command="resume", description="Resume playback"),
    BotCommand(command="stop", description="Stop and clear the queue"),
    BotCommand(command="seek", description="Seek within the track"),
    BotCommand(command="loop", description="Loop the track or queue"),
    BotCommand(command="volume", description="Set volume (1-200)"),
    BotCommand(command="lyrics", description="Fetch song lyrics"),
    BotCommand(command="radio", description="Internet radio stations"),
    BotCommand(command="playlists", description="Your saved playlists"),
    BotCommand(command="top", description="Most played tracks"),
    BotCommand(command="settings", description="Configure this chat"),
    BotCommand(command="help", description="All commands"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="ban", description="Ban a user"),
    BotCommand(command="mute", description="Mute a user"),
    BotCommand(command="warn", description="Warn a user"),
    BotCommand(command="purge", description="Bulk delete messages"),
    BotCommand(command="lock", description="Lock a message type"),
    BotCommand(command="setwelcome", description="Set the welcome message"),
    BotCommand(command="filters", description="Manage auto-reply filters"),
]


def _banner() -> None:
    logger.info("=" * 62)
    logger.info("  %s", config.bot_name)
    logger.info("  Music streaming + group management for Telegram")
    logger.info("=" * 62)


async def main() -> None:
    setup_logging()
    _banner()
    config.ensure_dirs()

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)
    for warn in config.warnings():
        logger.warning("%s", warn)

    backend = await database.connect()
    logger.info("Languages loaded: %s", ", ".join(translator.languages))

    assistant = create_assistant()
    calls = stream_manager.setup(assistant)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Order matters: gatekeeper (access) → enforcement (locks/flood/filters).
    dp.update.middleware(GatekeeperMiddleware())
    dp.message.middleware(EnforcementMiddleware())

    for router in (
        start.router,
        settings_handlers.router,
        moderation_handlers.router,
        admin.router,
        controls.router,
        play.router,
        advanced.router,
        misc.router,
        extras.router,
        dashboard.router,
        inline_mode.router,
        callbacks.router,
        # grouptools last: it owns broad catch-alls (#notes, AFK watcher).
        grouptools.router,
    ):
        dp.include_router(router)

    logger.info("Starting assistant userbot…")
    await assistant.start()
    await calls.start()
    await auto_leave.start()

    try:
        me = await bot.get_me()
        logger.info("Bot online as @%s (%s)", me.username, me.id)
        await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeAllChatAdministrators())
    except Exception as exc:
        logger.warning("Could not register bot commands: %s", exc)

    if config.log_group_id:
        try:
            await bot.send_message(
                config.log_group_id,
                f"✅ <b>{config.bot_name} started</b>\n"
                f"Storage: <code>{backend}</code>  •  "
                f"Languages: <code>{len(translator.languages)}</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not post to the log group: %s", exc)

    web_runner = None
    if config.web_enabled:
        try:
            from bot.web import start_web_server

            web_runner = await start_web_server(bot)
        except Exception as exc:
            logger.warning("Web dashboard failed to start: %s", exc)

    logger.info("%s is live — polling for updates.", config.bot_name)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down…")
        await auto_leave.stop()
        for chat_id in list(stream_manager.active_chats):
            try:
                await stream_manager.stop(chat_id)
            except Exception:
                pass
        if web_runner is not None:
            try:
                await web_runner.cleanup()
            except Exception:
                pass
        try:
            await calls.stop()
        except Exception:
            pass
        # Pyrogram raises ConnectionError if the client never fully connected.
        try:
            if assistant.is_connected:
                await assistant.stop()
        except Exception as exc:
            logger.debug("Assistant shutdown: %s", exc)
        await database.close()
        await bot.session.close()
        logger.info("Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
