"""Ultimate Music Bot — main entry point."""

from __future__ import annotations

import asyncio
import logging
import time
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, BotCommandScopeAllChatAdministrators, BotCommandScopeAllPrivateChats

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
    assistant_admin,
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
from bot.services.autoleave import auto_leave
from bot.services import cleanup, startup
from bot.services import errors
from bot.services.scheduler import scheduler  # noqa: E402
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
    BotCommand(command="song", description="Download a track as MP3"),
    BotCommand(command="lyrics", description="Fetch song lyrics"),
    BotCommand(command="radio", description="Internet radio stations"),
    BotCommand(command="playlists", description="Your saved playlists"),
    BotCommand(command="top", description="Most played tracks"),
    BotCommand(command="schedule", description="Schedule playback for later"),
    BotCommand(command="settings", description="Configure this chat"),
    BotCommand(command="help", description="All commands"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="ban", description="Ban a user"),
    BotCommand(command="mute", description="Mute a user"),
    BotCommand(command="warn", description="Warn a user"),
    BotCommand(command="purge", description="Bulk delete messages"),
    BotCommand(command="voteskip", description="Configure vote-to-skip"),
    BotCommand(command="unschedule", description="Cancel a scheduled play"),
    BotCommand(command="lock", description="Lock a message type"),
    BotCommand(command="setwelcome", description="Set the welcome message"),
    BotCommand(command="filters", description="Manage auto-reply filters"),
]


def build_id() -> str:
    """Identify the running code, so a stale deploy is obvious at a glance.

    Render (and most PaaS) expose the deployed commit as an env var. Falling
    back to the local git checkout keeps this useful in development.
    """
    for var in ("RENDER_GIT_COMMIT", "SOURCE_VERSION", "GIT_COMMIT", "HEROKU_SLUG_COMMIT"):
        sha = os.getenv(var)
        if sha:
            return sha[:7]
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _banner() -> None:
    logger.info("=" * 62)
    logger.info("  %s", config.bot_name)
    logger.info("  Music streaming + group management for Telegram")
    logger.info("  build %s | python %d.%d", build_id(), *sys.version_info[:2])
    logger.info("=" * 62)




def _make_schedule_runner(bot):
    """Build the callback the scheduler uses to start playback for a job."""

    async def run(job) -> None:
        from bot.services.music import get_stream_url
        from bot.services.queue import queue_manager
        from bot.services.stream import stream_manager
        from bot.utils.cards import error_card, now_playing_card
        from bot.utils.rich import RichCard, b, plain

        logger.info("Firing schedule %s in %s: %s", job.id, job.chat_id, job.query)

        async def tell(card) -> None:
            try:
                await bot.send_message(job.chat_id, card.to_html(), parse_mode="HTML")
            except Exception as exc:
                logger.debug("Could not notify %s: %s", job.chat_id, exc)

        track = await get_stream_url(job.query)
        if not track:
            await tell(
                error_card(
                    f"Scheduled play failed — I could not find “{job.query}”.",
                    "Update it with /schedule, or remove it with /unschedule.",
                )
            )
            return

        track["requester"] = "Scheduler"
        try:
            await stream_manager.play(job.chat_id, track)
        except Exception as exc:
            await tell(
                error_card(
                    f"Scheduled play failed: {exc}",
                    "Make sure the voice chat is open when the schedule fires.",
                )
            )
            return

        await tell(
            RichCard()
            .heading([plain("⏰ "), b("Scheduled Playback")], size=1)
            .para([plain("Starting "), b(track.get("title", job.query))])
            .footer("Set up with /schedule  •  cancel with /unschedule")
        )
        try:
            await bot.send_message(
                job.chat_id,
                now_playing_card(
                    track,
                    elapsed=0,
                    queue_len=await queue_manager.size(job.chat_id),
                    volume=await queue_manager.get_volume(job.chat_id),
                    loop_mode=(await queue_manager.get_loop(job.chat_id)).value,
                ).to_html(),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return run




def _ensure_ffmpeg() -> bool:
    """Make sure an `ffmpeg` binary is reachable on PATH.

    FFmpeg is required for both streaming and the /song transcode. Managed
    Python hosts (Render, Railway on the native runtime) give you no apt, so
    fall back to the static binary shipped by the imageio-ffmpeg wheel and
    expose it under a stable name that subprocesses can find.
    """
    if shutil.which("ffmpeg"):
        return True

    try:
        import imageio_ffmpeg
    except ImportError:
        logger.error(
            "FFmpeg is not installed and imageio-ffmpeg is unavailable. "
            "Streaming and /song will fail. Install FFmpeg (apt install ffmpeg) "
            "or add imageio-ffmpeg to requirements.txt."
        )
        return False

    try:
        binary = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if not binary.is_file():
            raise FileNotFoundError(binary)
    except Exception as exc:
        logger.error("Could not locate a bundled FFmpeg: %s", exc)
        return False

    bin_dir = DATA_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "ffmpeg"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(binary)
    except OSError:
        # Some filesystems disallow symlinks; copying is a fine fallback.
        try:
            shutil.copy2(binary, link)
            link.chmod(0o755)
        except OSError as exc:
            logger.error("Could not install a bundled FFmpeg: %s", exc)
            return False

    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    logger.info("Using bundled FFmpeg from imageio-ffmpeg (%s)", binary.name)
    return True


def _ensure_node() -> bool:
    """Make sure a JavaScript runtime is reachable on PATH.

    yt-dlp needs one to solve YouTube's player challenges. Without it, it
    falls back to a single player client, which datacenter IPs almost always
    fail — surfacing to users as "I could not find or extract that media" for
    every query, a baffling symptom for a missing system package.

    Same constraint as FFmpeg: no apt on managed Python hosts, so fall back to
    the Node binary shipped by the nodejs-wheel-binaries wheel.
    """
    for runtime in ("node", "deno", "bun", "quickjs"):
        if shutil.which(runtime):
            return True

    try:
        from nodejs_wheel.executable import ROOT_DIR
    except ImportError:
        return False

    binary = Path(ROOT_DIR) / "bin" / "node"
    if not binary.is_file():
        return False

    bin_dir = DATA_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "node"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(binary)
    except OSError:
        try:
            shutil.copy2(binary, link)
            link.chmod(0o755)
        except OSError as exc:
            logger.error("Could not install a bundled Node: %s", exc)
            return False

    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    logger.info("Using bundled Node from nodejs-wheel-binaries")
    return True


def _check_js_runtime() -> bool:
    """Report which JS runtime yt-dlp will use, installing one if needed."""
    _ensure_node()

    from bot.services.music import _js_runtimes, _player_clients, cookie_status

    # State the anti-block configuration outright. When extraction fails, the
    # first question is always "were cookies actually loaded?" — answer it in
    # the log rather than making someone reason about it.
    status = cookie_status()
    if status == "none":
        logger.info(
            "YouTube cookies: none (set COOKIES_FILE or COOKIES_DATA if "
            "extraction gets blocked)"
        )
    elif status.startswith("PRESENT BUT UNUSABLE"):
        # Loud, because an expired jar fails exactly like no jar at all and
        # that ambiguity is expensive to debug.
        logger.warning("YouTube cookies: %s", status)
        logger.warning(
            "Re-export cookies from a browser where you are signed in to "
            "YouTube; the current file will not get past an IP block."
        )
    else:
        logger.info("YouTube cookies: %s", status)
    logger.info("YouTube player clients: %s", ", ".join(_player_clients()))

    runtimes = _js_runtimes()
    if runtimes:
        logger.info("yt-dlp JS runtime: %s", ", ".join(runtimes))
        return True

    logger.warning(
        "No JavaScript runtime found (node/deno/bun/quickjs). YouTube "
        "extraction will use a reduced client set and will likely fail on "
        "this host. Install Node, or set COOKIES_FILE / YTDLP_PROXY."
    )
    return False


def _preflight() -> None:
    """Fail loudly and legibly on known-bad dependency combinations.

    The most common deploy failure is installing official `pyrogram` instead of
    a maintained fork: py-tgcalls imports `GroupcallForbidden` at module load,
    official pyrogram has not shipped since 2023 and does not define it, and the
    result is a bare ImportError deep inside pytgcalls that tells the operator
    nothing about how to fix it.
    """
    try:
        import pyrogram
    except ImportError:
        logger.error(
            "No MTProto client installed. Run: pip install -r requirements.txt"
        )
        sys.exit(1)

    try:
        from pyrogram.errors import GroupcallForbidden  # noqa: F401  (probe)
    except ImportError:
        version = getattr(pyrogram, "__version__", "unknown")
        logger.error(
            "=" * 62
            + "\n  INCOMPATIBLE PYROGRAM INSTALL"
            + "\n" + "=" * 62
            + f"\n  Installed pyrogram {version} does not provide GroupcallForbidden,"
            + "\n  which py-tgcalls needs. This is official pyrogram, which has not"
            + "\n  been released since 2023 and cannot drive voice calls."
            + "\n"
            + "\n  Fix:"
            + "\n    pip uninstall -y pyrogram pyrofork"
            + "\n    pip install -U kurigram"
            + "\n"
            + "\n  Your existing SESSION_STRING keeps working — the session format"
            + "\n  is identical, so there is no need to log in again."
            + "\n" + "=" * 62
        )
        sys.exit(1)

    if not _ensure_ffmpeg():
        logger.warning(
            "Continuing without FFmpeg — playback and downloads will not work."
        )

    _check_js_runtime()

    if sys.version_info >= (3, 14):
        logger.warning(
            "Running on Python %d.%d. Some native deps (ntgcalls, TgCrypto) may "
            "lack wheels here; 3.11-3.12 is the tested range.",
            *sys.version_info[:2],
        )


async def _janitor() -> None:
    """Periodically clear out stale downloads and rendered thumbnails."""
    from bot.services.downloads import prune_downloads
    from bot.services.thumbnails import prune_thumbnails

    while True:
        try:
            await asyncio.sleep(3600)
            await prune_downloads()
            await prune_thumbnails()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Janitor pass failed: %s", exc)


def _register_error_handler(dp: Dispatcher, bot: Bot) -> None:
    """Catch anything a handler throws.

    Most of the bot's handlers have no try/except of their own — which is
    fine, and arguably cleaner, *provided* something catches what they throw.
    Until now nothing did, so a bug meant a server-side traceback and total
    silence for the user.
    """

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        update = event.update

        chat_id: int | None = None
        context = ""
        message = None
        if getattr(update, "message", None):
            message = update.message
            chat_id = message.chat.id
            context = (message.text or "")[:32]
        elif getattr(update, "callback_query", None):
            query = update.callback_query
            message = query.message
            chat_id = message.chat.id if message else None
            context = f"callback {query.data}"[:32]

        rec = errors.record(exc, context)
        logger.exception("Unhandled error %s in %s", rec.error_id, context or "update")

        # Tell the user something, but never a traceback.
        if chat_id is not None:
            try:
                await bot.send_message(
                    chat_id, errors.user_message(rec), parse_mode="HTML"
                )
            except Exception as send_exc:
                logger.debug("Could not deliver the error notice: %s", send_exc)

        # Report to the log channel, deduplicated.
        if config.log_group_id and errors.should_report(rec):
            rec.last_reported = time.time()
            try:
                await bot.send_message(
                    config.log_group_id,
                    errors.format_report(rec, exc),
                    parse_mode="HTML",
                )
            except Exception as log_exc:
                logger.debug("Could not post the crash report: %s", log_exc)

        # True == handled, so polling continues instead of unwinding.
        return True


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

    _preflight()

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
    _register_error_handler(dp, bot)

    for router in (
        start.router,
        settings_handlers.router,
        moderation_handlers.router,
        admin.router,
        assistant_admin.router,
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

    # Announce queue advances: PyTgCalls changes track on its own, and until
    # now the group was told nothing when it did.
    play.register_stream_notifications(bot)

    scheduler.set_runner(_make_schedule_runner(bot))
    await scheduler.start()

    janitor = asyncio.create_task(_janitor())

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

    # DM the owner a readiness report. Deliberately after the log-group post
    # and never fatal: a PaaS restarts silently, and reading a log stream to
    # confirm the bot came back — and came back *healthy* — is friction.
    await startup.notify_owner(bot, backend=backend)

    web_runner = None
    if config.web_enabled:
        try:
            from bot.web import start_web_server

            web_runner = await start_web_server(bot)
        except Exception as exc:
            logger.warning("Web dashboard failed to start: %s", exc)

    # A redeploy overlaps the old instance with the new one for a few seconds,
    # and any leftover webhook competes with polling outright. Both surface as
    # TelegramConflictError; clearing the webhook and dropping the backlog
    # keeps the new instance from fighting the old one's queued updates.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:
        logger.warning("Could not clear webhook before polling: %s", exc)

    logger.info("%s is live — polling for updates.", config.bot_name)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        janitor.cancel()
        logger.info("Shutting down…")
        # Must run while the bot session is still open, and before we start
        # tearing down streams — otherwise there is nothing left to send with.
        await startup.notify_shutdown(bot)
        await scheduler.stop()
        await auto_leave.stop()
        await cleanup.stop()
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
