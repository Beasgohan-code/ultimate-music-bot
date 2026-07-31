"""Configuration loader for Ultimate Music Bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _parse_int_list(value: str) -> list[int]:
    if not value.strip():
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]


@dataclass(frozen=True)
class Config:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    api_id: int = field(default_factory=lambda: int(os.getenv("API_ID", "0") or "0"))
    api_hash: str = field(default_factory=lambda: os.getenv("API_HASH", ""))
    session_string: str = field(default_factory=lambda: os.getenv("SESSION_STRING", ""))
    mongo_uri: str = field(default_factory=lambda: os.getenv("MONGO_URI", ""))
    sudo_users: list[int] = field(
        default_factory=lambda: _parse_int_list(os.getenv("SUDO_USERS", ""))
    )
    assistant_username: str = field(
        default_factory=lambda: os.getenv("ASSISTANT_USERNAME", "")
    )
    max_queue_size: int = field(
        default_factory=lambda: int(os.getenv("MAX_QUEUE_SIZE", "50"))
    )
    auto_leave_idle: int = field(
        default_factory=lambda: int(os.getenv("AUTO_LEAVE_IDLE", "300"))
    )
    default_volume: int = field(
        default_factory=lambda: int(os.getenv("DEFAULT_VOLUME", "100"))
    )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.bot_token:
            errors.append("BOT_TOKEN is required")
        if not self.api_id:
            errors.append("API_ID is required")
        if not self.api_hash:
            errors.append("API_HASH is required")
        if not self.session_string:
            errors.append("SESSION_STRING is required (run session_generator.py)")
        return errors


config = Config()
