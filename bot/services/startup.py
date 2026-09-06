"""Startup and shutdown reports delivered to the owner's DM.

FallenMusic DM'd its support chat a short identity card on boot
(``__main__.py``: id / name / username for both the bot and the assistant).
The idea is sound — a PaaS restarts a bot silently, and reading a log stream to
confirm it came back is friction — but the original told you almost nothing:
it could not tell a healthy boot from one where the assistant was dead, cookies
had expired, or ffmpeg was missing.

This sends a full readiness report instead, and follows FallenMusic in noting
whether delivery failed rather than crashing the boot over it.  A boot report
must never be able to prevent the boot: every call here is best-effort.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import time
from dataclasses import dataclass, field

from bot.config import config
from bot.utils.rich import RichCard, b, c, i, plain

logger = logging.getLogger(__name__)

#: Process start, used for the uptime line in the shutdown report.
STARTED_AT = time.time()

#: Emitted when a subsystem is fine / degraded / unavailable.
OK, WARN, DEAD = "🟢", "🟡", "🔴"


@dataclass(slots=True)
class Check:
    """One line in the readiness table."""

    name: str
    state: str
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.state == OK


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, state: str, detail: str = "") -> None:
        self.checks.append(Check(name, state, detail))

    @property
    def degraded(self) -> list[Check]:
        return [check for check in self.checks if not check.healthy]


async def collect(bot) -> Report:
    """Probe every subsystem the bot depends on.

    Ordered by how badly a failure hurts: playback first, then media
    resolution, then niceties.
    """
    report = Report()

    # ── Assistant: without it the bot cannot join a voice chat at all.
    try:
        from bot.services import assistant

        uid = await assistant.user_id()
        if uid:
            report.add("Assistant", OK, f"{assistant.label()} ({uid})")
        else:
            report.add("Assistant", DEAD, "no SESSION_STRING — playback disabled")
    except Exception as exc:
        report.add("Assistant", DEAD, str(exc)[:60])

    # ── ffmpeg: yt-dlp can fetch, but nothing can be transcoded or streamed.
    # imageio-ffmpeg ships a binary that is not on PATH, so check it too rather
    # than reporting a false alarm on hosts that rely on it.
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = ""
    report.add(
        "ffmpeg",
        OK if ffmpeg else DEAD,
        ffmpeg or "not installed — streaming and /song will fail",
    )

    # ── Cookies decide whether YouTube answers us at all.
    try:
        from bot.services import music

        status = music.cookie_status()
        pool = len(music.cookie_pool())
        if pool:
            report.add("Cookies", OK, f"{pool} usable jar(s)")
        else:
            report.add("Cookies", WARN, status or "none — YouTube may block this IP")
    except Exception as exc:
        report.add("Cookies", WARN, str(exc)[:60])

    # ── Browser impersonation lowers the odds of being flagged.
    try:
        from bot.services.music import impersonate_status

        target = impersonate_status()
        report.add(
            "Impersonation",
            OK if "off" not in target.lower() else WARN,
            target,
        )
    except Exception:
        report.add("Impersonation", WARN, "unavailable")

    # ── Storage.
    try:
        from bot.services.database import database

        report.add("Storage", OK, database.backend)
    except Exception as exc:
        report.add("Storage", DEAD, str(exc)[:60])

    # ── Thumbnails degrade silently without Pillow, so say so out loud.
    try:
        import PIL  # noqa: F401  (probe only)

        report.add("Thumbnails", OK, "Pillow available")
    except ImportError:
        report.add("Thumbnails", WARN, "Pillow missing — text cards only")

    return report


def build_card(bot_user, report: Report, *, backend: str = "") -> RichCard:
    """The boot report card. FallenMusic's identity block, with a health table."""
    name = config.bot_name or bot_user.first_name
    card = (
        RichCard()
        .heading([plain("🚀 "), b(f"{name} is online")], size=1)
        .table(
            ["Field", "Value"],
            [
                ["Bot", c(f"@{bot_user.username}" if bot_user.username else name)],
                ["ID", c(str(bot_user.id))],
                ["Python", c(platform.python_version())],
                ["Host", c(platform.node()[:32] or "unknown")],
            ],
        )
        .divider()
        .heading([plain("🩺 "), b("Readiness")], size=2)
        .table(
            ["Subsystem", "", "Detail"],
            [[check.name, check.state, check.detail[:48]] for check in report.checks],
        )
    )

    broken = report.degraded
    if broken:
        # Lead with the consequence, not the label — an operator skimming a
        # phone notification should see what is actually broken.
        card.para(
            [i("Needs attention: " + ", ".join(f"{c.name} ({c.detail[:40]})" for c in broken))]
        )
    else:
        card.para([i("All subsystems nominal.")])
    return card


async def notify_owner(bot, *, backend: str = "") -> bool:
    """DM the owner a boot report. Returns True if it was delivered.

    Never raises: a boot report failing must not stop the bot from booting.
    """
    # OWNER_ID is the intended target, but render.yaml declares it sync:false,
    # so a deploy that never filled it in leaves it empty and the DM silently
    # never happens. Most such setups do have SUDO_USERS, and the first sudo
    # is the same person in practice — so fall back rather than stay quiet.
    target = config.owner_id
    via_fallback = False
    if not target:
        sudo = [uid for uid in config.sudo_users if uid]
        if sudo:
            target, via_fallback = sudo[0], True
        else:
            logger.warning(
                "No OWNER_ID or SUDO_USERS set — nobody to send the startup "
                "report to. Set OWNER_ID to your numeric Telegram id."
            )
            return False

    try:
        me = await bot.get_me()
        report = await collect(bot)
        card = build_card(me, report, backend=backend)

        from bot.services.delivery import send_safe

        outcome = await send_safe(
            lambda: bot.send_message(target, card.to_html(), parse_mode="HTML"),
            chat_id=target,
        )
        if outcome.ok:
            logger.info(
                "Startup report sent to %s%s (%d subsystem(s) degraded)",
                target,
                " (via SUDO_USERS — set OWNER_ID to silence this)" if via_fallback else "",
                len(report.degraded),
            )
            return True

        # FallenMusic logged this same failure mode; the usual cause is that
        # the owner has never opened a chat with the bot.
        logger.warning(
            "Could not DM the owner (%s): %s. "
            "Send /start to the bot from that account to enable startup reports.",
            target,
            outcome.error,
        )
    except Exception as exc:
        logger.warning("Startup report failed: %s", exc)
    return False


async def notify_shutdown(bot, reason: str = "") -> None:
    """Best-effort "going down" DM, with uptime.

    An unexplained restart loop is invisible otherwise: the boot message alone
    looks identical whether the bot ran for a month or thirty seconds.
    """
    if not config.owner_id:
        return

    uptime = time.time() - STARTED_AT
    hours, remainder = divmod(int(uptime), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        pretty = f"{hours}h {minutes}m"
    elif minutes:
        pretty = f"{minutes}m {seconds}s"
    else:
        pretty = f"{seconds}s"

    card = (
        RichCard()
        .heading([plain("🛑 "), b(f"{config.bot_name} shutting down")], size=1)
        .table(
            ["Field", "Value"],
            [["Uptime", c(pretty)], ["Reason", c(reason or "normal shutdown")]],
        )
    )
    if uptime < 60:
        card.para([i("That was a very short run — check the logs for a crash loop.")])

    try:
        # Shutdown is time-critical: the platform may SIGKILL us shortly after
        # SIGTERM, so don't let a slow send hold the process open.
        await asyncio.wait_for(
            bot.send_message(config.owner_id, card.to_html(), parse_mode="HTML"),
            timeout=5,
        )
    except Exception:
        logger.debug("Shutdown notice not delivered", exc_info=True)
