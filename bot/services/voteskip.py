"""Vote-skip: stop one person hijacking the queue in a busy group.

A non-admin `/skip` opens a vote instead of skipping outright. Admins, the
person who requested the current track, and sudo users always skip instantly.

The threshold is a fraction of the *listeners actually in the voice chat*, not
the group's member count — a 5000-member group with three people listening
should need two votes, not 2500.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bot.config import config
from bot.services.database import database

logger = logging.getLogger(__name__)

DEFAULT_RATIO = 0.5
MIN_VOTES = 2
VOTE_TTL = 300  # a vote dies with the track, or after 5 minutes


@dataclass
class Vote:
    track_key: str
    voters: set[int] = field(default_factory=set)
    started: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.started > VOTE_TTL


class VoteSkipManager:
    def __init__(self) -> None:
        self._votes: dict[int, Vote] = {}

    @staticmethod
    def track_key(track: dict[str, Any] | None) -> str:
        if not track:
            return ""
        return str(track.get("id") or track.get("url") or track.get("title", ""))

    async def enabled(self, chat_id: int) -> bool:
        return bool(await database.get_chat_value(chat_id, "voteskip_enabled", True))

    async def set_enabled(self, chat_id: int, value: bool) -> None:
        await database.set_chat_value(chat_id, "voteskip_enabled", bool(value))

    async def ratio(self, chat_id: int) -> float:
        raw = await database.get_chat_value(
            chat_id, "voteskip_ratio", config.voteskip_ratio or DEFAULT_RATIO
        )
        try:
            return min(1.0, max(0.1, float(raw)))
        except (TypeError, ValueError):
            return DEFAULT_RATIO

    async def set_ratio(self, chat_id: int, value: float) -> float:
        value = min(1.0, max(0.1, float(value)))
        await database.set_chat_value(chat_id, "voteskip_ratio", value)
        return value

    def needed(self, listeners: int, ratio: float) -> int:
        """How many votes to skip, given the number of people listening."""
        # Exclude the assistant itself from the headcount.
        humans = max(1, listeners - 1)
        return max(MIN_VOTES, min(humans, round(humans * ratio)))

    def reset(self, chat_id: int) -> None:
        self._votes.pop(chat_id, None)

    def current(self, chat_id: int, track: dict[str, Any] | None) -> Vote | None:
        vote = self._votes.get(chat_id)
        if not vote:
            return None
        # A vote only applies to the track it was opened against.
        if vote.expired or vote.track_key != self.track_key(track):
            self._votes.pop(chat_id, None)
            return None
        return vote

    def add_vote(
        self, chat_id: int, user_id: int, track: dict[str, Any] | None
    ) -> tuple[int, bool]:
        """Register a vote. Returns (total_votes, was_new)."""
        vote = self.current(chat_id, track)
        if vote is None:
            vote = Vote(track_key=self.track_key(track))
            self._votes[chat_id] = vote
        if user_id in vote.voters:
            return len(vote.voters), False
        vote.voters.add(user_id)
        return len(vote.voters), True


voteskip = VoteSkipManager()


async def count_listeners(bot, chat_id: int) -> int:
    """Best-effort count of people in the voice chat.

    PyTgCalls exposes participants; if that fails we fall back to the chat's
    member count so the feature still works, just with a coarser denominator.
    """
    try:
        from bot.services.stream import stream_manager

        # .calls raises if setup() has not run yet — treat that like any other
        # lookup failure and fall back to the member count.
        participants = await stream_manager.calls.get_participants(chat_id)
        if participants:
            return len(participants)
    except Exception as exc:
        logger.debug("Participant lookup failed for %s: %s", chat_id, exc)

    try:
        return max(1, await bot.get_chat_member_count(chat_id) - 1)
    except Exception:
        return 1
