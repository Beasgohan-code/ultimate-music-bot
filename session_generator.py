#!/usr/bin/env python3
"""Generate a Pyrogram session string for the assistant userbot."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    from pyrogram import Client

    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "")

    if not api_id or not api_hash:
        print("Set API_ID and API_HASH in .env first (from https://my.telegram.org)")
        sys.exit(1)

    print("=" * 50)
    print("  Ultimate Music Bot — Session Generator")
    print("=" * 50)
    print()
    print("Log in with your ASSISTANT account (not the bot).")
    print("This account joins voice chats to stream audio/video.")
    print()

    async with Client("session_gen", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        session_string = await app.export_session_string()
        print()
        print("Your SESSION_STRING (add to .env):")
        print("-" * 50)
        print(session_string)
        print("-" * 50)
        print()
        print("⚠️  Keep this secret! Anyone with it can control your account.")


if __name__ == "__main__":
    asyncio.run(main())
