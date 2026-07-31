"""Pyrogram userbot assistant for voice chat streaming."""

from __future__ import annotations

from pyrogram import Client

from bot.config import config


def create_assistant() -> Client:
    return Client(
        "ultimate-assistant",
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_string=config.session_string,
        in_memory=True,
    )
