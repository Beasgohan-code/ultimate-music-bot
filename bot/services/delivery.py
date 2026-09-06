"""Reliable message delivery: rate limits, retries, and dead-chat pruning.

Telegram enforces two separate limits that a naive ``send_message`` loop walks
straight into:

* **~30 messages/second globally** across all chats.
* **~20 messages/minute per group.**

Exceeding either returns ``429`` with a ``retry_after``.  aiogram surfaces that
as :class:`TelegramRetryAfter`.  Before this module the bot caught nothing —
``except Exception: failed += 1`` — so a broadcast to a few hundred chats
quietly lost most of its messages and reported them as failures, while the
underlying send would have succeeded a second later.

Three things live here:

``send_safe``
    One send with retry-on-429 and classification of permanent failures.

``Broadcaster``
    Paced fan-out over many chats with live progress and a result breakdown.

``prune_dead_chats``
    Chats that reply "bot was blocked"/"chat not found" are gone for good.
    Recording that keeps the next broadcast from paying for them again.

The pacing is deliberately conservative (``GLOBAL_RATE`` below Telegram's
ceiling) because a 429 costs far more than the delay it saves: the retry_after
Telegram hands back grows with how hard you push it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger(__name__)

#: Messages per second across every chat. Telegram's own ceiling is ~30; the
#: headroom absorbs bursts from normal command traffic happening concurrently.
GLOBAL_RATE = 20.0

#: A 429 asking us to wait longer than this is not worth blocking a broadcast
#: for — the chat is skipped and counted as deferred.
MAX_RETRY_AFTER = 120

#: Attempts per chat before giving up (only for retryable failures).
MAX_ATTEMPTS = 3

# Substrings that mean "this chat will never accept a message again".
_PERMANENT = (
    "bot was blocked",
    "user is deactivated",
    "chat not found",
    "peer_id_invalid",
    "bot was kicked",
    "group chat was deactivated",
    "chat_write_forbidden",
    "not enough rights to send text messages",
    "have no rights to send a message",
    "topic_closed",
    "user_is_blocked",
)


def is_permanent(exc: BaseException) -> bool:
    """True when retrying ``exc`` can never succeed.

    Distinguishing this from a transient failure is the difference between a
    broadcast that self-heals and one that hammers dead chats forever.
    """
    if isinstance(exc, (TelegramForbiddenError, TelegramNotFound)):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT)


class RateLimiter:
    """Token-bucket pacer shared by everything that sends in bulk.

    Uses a monotonic clock so a system clock change (NTP step, DST on a badly
    configured host) can't wedge it into waiting for hours.
    """

    def __init__(self, rate: float = GLOBAL_RATE) -> None:
        self.rate = rate
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_slot = max(now, self._next_slot) + self._interval

    async def pause(self, seconds: float) -> None:
        """Push every pending slot back — used after a 429."""
        async with self._lock:
            self._next_slot = max(self._next_slot, time.monotonic() + seconds)


#: Process-wide pacer.
limiter = RateLimiter()


@dataclass(slots=True)
class SendOutcome:
    """What happened to one delivery attempt."""

    ok: bool
    chat_id: int
    result: Any = None
    error: str = ""
    permanent: bool = False
    attempts: int = 1
    waited: float = 0.0


async def send_safe(
    action: Callable[[], Awaitable[Any]],
    *,
    chat_id: int = 0,
    max_attempts: int = MAX_ATTEMPTS,
    pace: bool = True,
) -> SendOutcome:
    """Run ``action`` with rate limiting and retry-on-429.

    ``action`` is a zero-arg coroutine factory (``lambda: bot.send_message(...)``)
    rather than a coroutine, because a coroutine can only be awaited once and
    retrying needs a fresh one each time.
    """
    waited = 0.0
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        if pace:
            await limiter.acquire()
        try:
            result = await action()
            return SendOutcome(True, chat_id, result=result, attempts=attempt, waited=waited)

        except TelegramRetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 5) or 5)
            last_error = f"rate limited ({delay:.0f}s)"
            if delay > MAX_RETRY_AFTER or attempt == max_attempts:
                reason = (
                    f"it asked for {delay:.0f}s"
                    if delay > MAX_RETRY_AFTER
                    else f"still limited after {attempt} attempts"
                )
                logger.warning("Skipping %s: %s", chat_id, reason)
                return SendOutcome(
                    False, chat_id, error=last_error, attempts=attempt, waited=waited
                )
            # Hold the shared pacer too: a 429 in one chat means we are pushing
            # the whole bot too hard, not just that chat.
            await limiter.pause(delay)
            await asyncio.sleep(delay)
            waited += delay

        except (TelegramForbiddenError, TelegramNotFound) as exc:
            return SendOutcome(
                False, chat_id, error=str(exc), permanent=True, attempts=attempt, waited=waited
            )

        except (TelegramServerError, TelegramNetworkError) as exc:
            last_error = str(exc)
            if attempt == max_attempts:
                break
            backoff = min(2.0 * attempt, 10.0)
            await asyncio.sleep(backoff)
            waited += backoff

        except TelegramBadRequest as exc:
            # Bad requests are deterministic: the same payload fails identically.
            return SendOutcome(
                False,
                chat_id,
                error=str(exc),
                permanent=is_permanent(exc),
                attempts=attempt,
                waited=waited,
            )

        except Exception as exc:  # noqa: BLE001 - never let one chat kill a run
            last_error = str(exc)
            if attempt == max_attempts:
                break
            await asyncio.sleep(1.0)
            waited += 1.0

    return SendOutcome(
        False, chat_id, error=last_error, permanent=False, attempts=max_attempts, waited=waited
    )


@dataclass(slots=True)
class BroadcastReport:
    """Aggregate result of a fan-out, in the shape the summary card needs."""

    total: int = 0
    sent: int = 0
    failed: int = 0
    blocked: int = 0
    pruned: list[int] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def rate(self) -> float:
        seconds = self.elapsed
        return self.sent / seconds if seconds > 0.5 else 0.0

    def as_rows(self) -> list[list[str]]:
        rows = [
            ["Delivered", str(self.sent)],
            ["Blocked / gone", str(self.blocked)],
            ["Failed", str(self.failed)],
            ["Total", str(self.total)],
        ]
        if self.elapsed >= 1:
            rows.append(["Time", f"{self.elapsed:.0f}s ({self.rate:.1f}/s)"])
        return rows


class Broadcaster:
    """Paced fan-out with progress callbacks and dead-chat collection."""

    def __init__(self, *, progress_every: int = 25) -> None:
        self.progress_every = progress_every

    async def run(
        self,
        targets: Sequence[int],
        send: Callable[[int], Awaitable[Any]],
        *,
        on_progress: Callable[[BroadcastReport], Awaitable[None]] | None = None,
    ) -> BroadcastReport:
        report = BroadcastReport(total=len(targets))

        for index, chat_id in enumerate(targets, 1):
            outcome = await send_safe(lambda cid=chat_id: send(cid), chat_id=chat_id)

            if outcome.ok:
                report.sent += 1
            elif outcome.permanent:
                report.blocked += 1
                report.pruned.append(chat_id)
            else:
                report.failed += 1
                logger.debug("Broadcast to %s failed: %s", chat_id, outcome.error)

            if on_progress and index % self.progress_every == 0:
                try:
                    await on_progress(report)
                except Exception:  # a broken status message must not abort the run
                    logger.debug("Broadcast progress update failed", exc_info=True)

        return report


async def prune_dead_chats(chat_ids: Iterable[int]) -> int:
    """Mark chats that can never receive a message again.

    They stay in the database (history and stats still reference them) but are
    flagged so :func:`deliverable_chats` skips them next time.
    """
    from bot.services.database import database

    count = 0
    for chat_id in chat_ids:
        try:
            await database.set_chat_value(chat_id, "delivery_dead", time.time())
            count += 1
        except Exception:
            logger.debug("Could not flag chat %s as dead", chat_id, exc_info=True)
    if count:
        logger.info("Flagged %d unreachable chats", count)
    return count


async def deliverable_chats(chat_ids: Iterable[int]) -> list[int]:
    """Filter out chats already known to be unreachable."""
    from bot.services.database import database

    alive: list[int] = []
    for chat_id in chat_ids:
        try:
            if await database.get_chat_value(chat_id, "delivery_dead"):
                continue
        except Exception:
            pass
        alive.append(chat_id)
    return alive


async def revive_chat(chat_id: int) -> None:
    """Clear the dead flag — called when a chat talks to us again.

    Without this a group that removed and re-added the bot would stay
    permanently excluded from broadcasts.
    """
    from bot.services.database import database

    try:
        if await database.get_chat_value(chat_id, "delivery_dead"):
            await database.set_chat_value(chat_id, "delivery_dead", 0)
    except Exception:
        logger.debug("Could not revive chat %s", chat_id, exc_info=True)
