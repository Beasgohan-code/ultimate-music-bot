"""Scheduled playback — start music at a given time, optionally every day.

Jobs are persisted, so a restart does not lose them. A single background loop
ticks once a minute and fires anything due; that is plenty of resolution for
"play the morning playlist at 07:00" and costs nothing while idle.

Times are stored as a plain "HH:MM" string plus a UTC offset for the chat, so a
group in Kolkata and one in Berlin can both say "07:00" and mean their own
local morning.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from bot.services.database import database

logger = logging.getLogger(__name__)

# "07:00", "7:00", "0700", "7am", "7:30 pm"
_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$", re.IGNORECASE
)
_DELAY_RE = re.compile(r"^(?:in\s+)?(?P<n>\d+)\s*(?P<unit>m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$", re.IGNORECASE)

MAX_JOBS_PER_CHAT = 10
TICK_SECONDS = 30


@dataclass
class Job:
    id: str
    chat_id: int
    user_id: int
    query: str
    # Either an absolute epoch (one-shot) or a daily "HH:MM" in chat-local time.
    run_at: float = 0.0
    daily_at: str = ""
    tz_offset_min: int = 0
    label: str = ""
    created: float = field(default_factory=time.time)
    last_run: float = 0.0

    @property
    def is_daily(self) -> bool:
        return bool(self.daily_at)

    def describe(self) -> str:
        if self.is_daily:
            return f"every day at {self.daily_at}"
        remaining = self.run_at - time.time()
        if remaining <= 0:
            return "due now"
        mins = int(remaining // 60)
        if mins < 60:
            return f"in {max(1, mins)} min"
        hours, mins = divmod(mins, 60)
        if hours < 24:
            return f"in {hours}h {mins:02d}m"
        return f"in {hours // 24}d {hours % 24}h"

    def next_epoch(self) -> float:
        """When this job should next fire, as a UTC epoch."""
        if not self.is_daily:
            return self.run_at
        tz = timezone(timedelta(minutes=self.tz_offset_min))
        now = datetime.now(tz)
        hh, mm = (int(x) for x in self.daily_at.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()


def parse_when(text: str, tz_offset_min: int = 0) -> tuple[float, str] | None:
    """Parse a schedule spec.

    Returns (epoch, daily_at) — `daily_at` is "" for a one-shot job, and
    `epoch` is 0 for a daily one. Returns None if the text is not a time.
    """
    text = text.strip().lower()

    # Relative: "in 20m", "45 minutes", "2h"
    delay = _DELAY_RE.match(text)
    if delay:
        n = int(delay.group("n"))
        unit = delay.group("unit")[0]
        seconds = n * (3600 if unit == "h" else 60)
        if not 60 <= seconds <= 30 * 86400:
            return None
        return time.time() + seconds, ""

    daily = False
    if text.startswith("daily "):
        daily, text = True, text[6:].strip()
    elif text.endswith(" daily"):
        daily, text = True, text[:-6].strip()

    m = _TIME_RE.match(text.replace(".", ":"))
    if not m:
        return None

    hour = int(m.group("h"))
    minute = int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if daily:
        return 0.0, f"{hour:02d}:{minute:02d}"

    # One-shot: the next occurrence of that clock time in the chat's timezone.
    tz = timezone(timedelta(minutes=tz_offset_min))
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp(), ""


class Scheduler:
    """Persisted job store plus the tick loop that runs them."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._runner = None  # set by main.py: async (job) -> None

    def set_runner(self, runner) -> None:
        """Install the coroutine that actually starts playback for a job."""
        self._runner = runner

    # ── persistence ─────────────────────────────────────────────────────
    async def _load(self, chat_id: int) -> list[Job]:
        doc = await database._get("schedules", str(chat_id))
        return [Job(**j) for j in doc.get("jobs", []) if isinstance(j, dict)]

    async def _save(self, chat_id: int, jobs: list[Job]) -> None:
        if jobs:
            await database._set("schedules", str(chat_id), {"jobs": [asdict(j) for j in jobs]})
        else:
            await database._delete("schedules", str(chat_id))

    async def list_jobs(self, chat_id: int) -> list[Job]:
        """Soonest first, so the list reads like a timetable."""
        jobs = await self._load(chat_id)
        return sorted(jobs, key=lambda j: j.next_epoch())

    async def add(
        self,
        chat_id: int,
        user_id: int,
        query: str,
        when: str,
        tz_offset_min: int = 0,
    ) -> Job | None:
        parsed = parse_when(when, tz_offset_min)
        if not parsed:
            return None
        run_at, daily_at = parsed

        jobs = await self._load(chat_id)
        if len(jobs) >= MAX_JOBS_PER_CHAT:
            raise ValueError(f"This chat already has {MAX_JOBS_PER_CHAT} schedules.")

        job = Job(
            id=uuid.uuid4().hex[:8],
            chat_id=chat_id,
            user_id=user_id,
            query=query.strip()[:300],
            run_at=run_at,
            daily_at=daily_at,
            tz_offset_min=tz_offset_min,
        )
        jobs.append(job)
        await self._save(chat_id, jobs)
        return job

    async def remove(self, chat_id: int, job_id: str) -> bool:
        jobs = await self._load(chat_id)
        remaining = [j for j in jobs if j.id != job_id.lower()]
        if len(remaining) == len(jobs):
            return False
        await self._save(chat_id, remaining)
        return True

    async def clear(self, chat_id: int) -> int:
        jobs = await self._load(chat_id)
        await self._save(chat_id, [])
        return len(jobs)

    async def due_jobs(self) -> list[Job]:
        """Every job across every chat that should fire now."""
        now = time.time()
        out: list[Job] = []
        everything = await database._all("schedules")
        for raw in everything.values():
            for data in raw.get("jobs", []):
                try:
                    job = Job(**data)
                except TypeError:
                    continue
                # Don't double-fire inside one tick window.
                if now - job.last_run < 120:
                    continue
                if job.next_epoch() <= now:
                    out.append(job)
        return out

    async def _complete(self, job: Job) -> None:
        """Mark a job run: reschedule if daily, drop it if one-shot."""
        jobs = await self._load(job.chat_id)
        if job.is_daily:
            for existing in jobs:
                if existing.id == job.id:
                    existing.last_run = time.time()
            await self._save(job.chat_id, jobs)
        else:
            await self._save(job.chat_id, [j for j in jobs if j.id != job.id])

    # ── loop ────────────────────────────────────────────────────────────
    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                if not self._runner:
                    continue
                for job in await self.due_jobs():
                    await self._complete(job)
                    try:
                        await self._runner(job)
                    except Exception as exc:
                        logger.warning("Scheduled job %s failed: %s", job.id, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Scheduler tick failed: %s", exc)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("Scheduler started (tick %ss)", TICK_SECONDS)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


scheduler = Scheduler()
