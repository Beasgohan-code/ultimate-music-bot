"""Turn unhandled handler exceptions into something actionable.

Without this, a bug in any of the ~195 registered handlers produces silence:
aiogram logs a traceback on the server and the user sees nothing at all. They
retry, it fails again, and nobody finds out unless someone reads the logs.

What happens instead:

* the user gets a short apology carrying a short error id
* the full traceback goes to the log channel, once per distinct bug
* repeats are counted rather than reposted, so one broken handler in a busy
  group cannot flood the channel

The id is a hash of the traceback's *shape*, not its message, so the same bug
keeps the same id across restarts and different chats. Quoting that id in a
report leads straight to the right traceback.
"""

from __future__ import annotations

import hashlib
import html
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Don't repost the same bug more often than this (seconds).
REPORT_COOLDOWN = 900

#: Cap the stored registry so a pathological loop cannot grow it forever.
MAX_TRACKED = 256


@dataclass
class ErrorRecord:
    """Bookkeeping for one distinct failure."""

    error_id: str
    kind: str
    message: str
    first_seen: float
    last_seen: float
    count: int = 1
    last_reported: float = 0.0
    contexts: list[str] = field(default_factory=list)


_REGISTRY: dict[str, ErrorRecord] = {}


def _fingerprint(exc: BaseException) -> str:
    """Stable short id for a bug, based on where it happened.

    Uses the exception type plus the file/line of each frame — deliberately
    not the message, since messages embed chat ids and song titles that would
    otherwise split one bug into thousands.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    shape = "|".join(f"{f.filename}:{f.lineno}:{f.name}" for f in frames)
    digest = hashlib.sha256(f"{type(exc).__name__}|{shape}".encode()).hexdigest()
    return digest[:8]


def record(exc: BaseException, context: str = "") -> ErrorRecord:
    """Register an exception and return its record."""
    error_id = _fingerprint(exc)
    now = time.time()
    rec = _REGISTRY.get(error_id)
    if rec is None:
        if len(_REGISTRY) >= MAX_TRACKED:
            # Drop the least recently seen entry rather than growing forever.
            oldest = min(_REGISTRY.values(), key=lambda r: r.last_seen)
            _REGISTRY.pop(oldest.error_id, None)
        rec = ErrorRecord(
            error_id=error_id,
            kind=type(exc).__name__,
            message=str(exc)[:300],
            first_seen=now,
            last_seen=now,
        )
        _REGISTRY[error_id] = rec
    else:
        rec.count += 1
        rec.last_seen = now
        rec.message = str(exc)[:300]

    if context and context not in rec.contexts:
        rec.contexts.append(context)
        del rec.contexts[:-5]
    return rec


def should_report(rec: ErrorRecord) -> bool:
    """First sighting reports immediately; repeats respect a cooldown."""
    if rec.last_reported == 0.0:
        return True
    return (time.time() - rec.last_reported) >= REPORT_COOLDOWN


def format_report(rec: ErrorRecord, exc: BaseException) -> str:
    """HTML crash report for the log channel."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    # Telegram caps messages at 4096; keep the tail, where the cause lives.
    if len(tb) > 2800:
        tb = "…\n" + tb[-2800:]

    lines = [
        f"🐞 <b>Unhandled error</b> <code>{rec.error_id}</code>",
        f"<b>{html.escape(rec.kind)}</b>: {html.escape(rec.message)}",
    ]
    if rec.count > 1:
        since = int(time.time() - rec.first_seen)
        lines.append(f"Seen <b>{rec.count}×</b> in the last {since // 60}m")
    if rec.contexts:
        lines.append("Where: " + ", ".join(html.escape(c) for c in rec.contexts))
    lines.append(f"<pre>{html.escape(tb)}</pre>")
    return "\n".join(lines)


def user_message(rec: ErrorRecord) -> str:
    """Short, non-technical apology. No stack traces at the user."""
    return (
        "⚠️ <b>Something went wrong on my side.</b>\n"
        "This has been reported — nothing you did caused it.\n"
        f"<i>Reference:</i> <code>{rec.error_id}</code>"
    )


def snapshot() -> list[dict[str, Any]]:
    """Recent distinct errors, worst first — powers /errors for the owner."""
    ordered = sorted(_REGISTRY.values(), key=lambda r: (r.count, r.last_seen), reverse=True)
    return [
        {
            "id": r.error_id,
            "kind": r.kind,
            "message": r.message,
            "count": r.count,
            "age": int(time.time() - r.last_seen),
            "contexts": list(r.contexts),
        }
        for r in ordered
    ]


def clear() -> int:
    """Forget every tracked error. Returns how many were dropped."""
    n = len(_REGISTRY)
    _REGISTRY.clear()
    return n
