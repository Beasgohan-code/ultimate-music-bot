"""Generate an assistant session string from inside the bot.

`python session_generator.py` needs shell access to the host. On a PaaS you
often do not have one, so this drives the same Pyrogram login over a Telegram
DM instead: the owner sends a phone number, Telegram sends a login code, the
owner forwards it back, and the resulting session is saved.

Three rules shape everything here, and all three are security rather than
convenience:

*   **Owner only, DM only.** A login code grants full account access. The
    handler refuses to run in a group even for the owner, because a group has
    other members and message history.
*   **Codes expire, sessions must not linger.** Half-finished logins hold an
    open Pyrogram client; they are dropped after a timeout so an abandoned
    attempt cannot be resumed later by whoever gets there first.
*   **Never echo the secret.** The string is saved and confirmed, not printed.
    Telegram messages are backed up, forwarded and screenshotted.

Persistence goes to the database *and*, when the filesystem is writable, to
.env — a PaaS wipes the disk on redeploy, so the database copy is what
actually survives, while the .env copy keeps a self-hosted setup working the
way its owner expects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.config import config
from bot.services.database import database

logger = logging.getLogger(__name__)

#: Abandon a half-finished login after this long. Telegram's own code
#: validity is around five minutes; this is deliberately a little longer so
#: the timeout message is ours rather than a confusing PhoneCodeExpired.
ATTEMPT_TTL = 420.0

#: Where the session lands in the global settings store.
SETTING_KEY = "session_string"


@dataclass
class Attempt:
    """One in-flight login. Holds a connected client between steps."""

    user_id: int
    client: Any
    phone: str = ""
    phone_code_hash: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > ATTEMPT_TTL


class SessionGenerator:
    """Serialises login attempts and owns their lifecycle."""

    def __init__(self) -> None:
        self._attempts: dict[int, Attempt] = {}
        self._lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────
    async def cancel(self, user_id: int) -> bool:
        """Drop an attempt and disconnect its client. True if one existed."""
        async with self._lock:
            attempt = self._attempts.pop(user_id, None)
        if not attempt:
            return False
        await self._disconnect(attempt)
        return True

    async def sweep(self) -> int:
        """Drop expired attempts. Returns how many were removed."""
        async with self._lock:
            stale = [uid for uid, a in self._attempts.items() if a.expired]
            attempts = [self._attempts.pop(uid) for uid in stale]
        for attempt in attempts:
            await self._disconnect(attempt)
        return len(attempts)

    async def active(self, user_id: int) -> Attempt | None:
        async with self._lock:
            attempt = self._attempts.get(user_id)
        if attempt and attempt.expired:
            await self.cancel(user_id)
            return None
        return attempt

    @staticmethod
    async def _disconnect(attempt: Attempt) -> None:
        try:
            if attempt.client and attempt.client.is_connected:
                await attempt.client.disconnect()
        except Exception as exc:  # a dead client must not block a retry
            logger.debug("Could not disconnect a session attempt: %s", exc)

    # ── step 1: phone number ────────────────────────────────────────────
    async def start(self, user_id: int, phone: str) -> tuple[bool, str]:
        """Request a login code. Returns (ok, message)."""
        if not config.api_id or not config.api_hash:
            return False, "API_ID and API_HASH are not configured on this deployment."

        phone = _normalise_phone(phone)
        if not phone:
            return False, "That does not look like a phone number. Use +919876543210."

        await self.cancel(user_id)  # restart cleanly if one was in flight

        try:
            from pyrogram import Client
        except ImportError:
            return False, "Pyrogram is not installed on this deployment."

        client = Client(
            f"gen_{user_id}",
            api_id=config.api_id,
            api_hash=config.api_hash,
            in_memory=True,
        )
        try:
            await client.connect()
            sent = await client.send_code(phone)
        except Exception as exc:
            await self._disconnect(Attempt(user_id, client))
            return False, _explain(exc)

        async with self._lock:
            self._attempts[user_id] = Attempt(
                user_id=user_id,
                client=client,
                phone=phone,
                phone_code_hash=sent.phone_code_hash,
            )
        return True, phone

    # ── step 2: the code (and maybe a 2FA password) ─────────────────────
    async def submit_code(self, user_id: int, code: str) -> tuple[str, str]:
        """Sign in with a login code.

        Returns (status, detail) where status is one of:
        ``ok`` (detail is the session string), ``password`` (2FA needed),
        or ``error`` (detail is a user-facing message).
        """
        attempt = await self.active(user_id)
        if not attempt:
            return "error", "That login expired. Send /genstring to start again."

        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits:
            return "error", "Send just the code digits, for example 12345."

        try:
            await attempt.client.sign_in(
                attempt.phone, attempt.phone_code_hash, digits
            )
        except Exception as exc:
            name = type(exc).__name__
            if name == "SessionPasswordNeeded":
                return "password", "This account has two-step verification enabled."
            # A wrong code is recoverable — keep the attempt alive so the
            # owner can simply send the right one.
            if name in ("PhoneCodeInvalid", "PhoneCodeEmpty"):
                return "error", "That code is not right. Check it and send it again."
            await self.cancel(user_id)
            return "error", _explain(exc)

        return await self._finish(user_id)

    async def submit_password(self, user_id: int, password: str) -> tuple[str, str]:
        attempt = await self.active(user_id)
        if not attempt:
            return "error", "That login expired. Send /genstring to start again."
        try:
            await attempt.client.check_password(password)
        except Exception as exc:
            if type(exc).__name__ == "PasswordHashInvalid":
                return "error", "Wrong password. Send it again."
            await self.cancel(user_id)
            return "error", _explain(exc)
        return await self._finish(user_id)

    async def _finish(self, user_id: int) -> tuple[str, str]:
        attempt = await self.active(user_id)
        if not attempt:
            return "error", "That login expired. Send /genstring to start again."
        try:
            session_string = await attempt.client.export_session_string()
        except Exception as exc:
            await self.cancel(user_id)
            return "error", _explain(exc)
        finally:
            await self.cancel(user_id)
        return "ok", session_string


# ── persistence ─────────────────────────────────────────────────────────────
async def save(session_string: str) -> list[str]:
    """Persist a session string. Returns human-readable destinations."""
    saved: list[str] = []

    try:
        await database.set_setting(SETTING_KEY, session_string)
        saved.append(f"database ({database.backend})")
    except Exception as exc:
        logger.error("Could not save the session to the database: %s", exc)

    path = Path(".env")
    try:
        # Reuse the CLI's writer so both paths behave identically — including
        # the backup, the 0600 mode and the `export ` handling.
        import sys

        sys.path.insert(0, str(Path.cwd()))
        from session_generator import write_env_value

        action = write_env_value(path, "SESSION_STRING", session_string)
        saved.append(f".env ({action})")
    except Exception as exc:
        # Read-only filesystems are normal on a PaaS; the database copy is
        # the one that matters there, so this is not an error.
        logger.info("Could not write .env (%s) — database copy is authoritative", exc)

    return saved


async def stored() -> str:
    """The saved session string, preferring the database over the env."""
    try:
        value = await database.get_setting(SETTING_KEY, "")
        if value:
            return str(value)
    except Exception as exc:
        logger.debug("Could not read the stored session: %s", exc)
    return config.session_string or ""


# ── helpers ─────────────────────────────────────────────────────────────────
def _normalise_phone(raw: str) -> str:
    cleaned = "".join(ch for ch in (raw or "") if ch.isdigit() or ch == "+")
    digits = cleaned.lstrip("+")
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        return ""
    return f"+{digits}"


def _explain(exc: BaseException) -> str:
    """Turn a Pyrogram exception into something a human can act on."""
    name = type(exc).__name__
    mapping = {
        "PhoneNumberInvalid": "Telegram does not recognise that phone number.",
        "PhoneNumberBanned": "That number is banned from Telegram.",
        "PhoneCodeExpired": "The code expired. Send /genstring to start again.",
        "PhoneCodeInvalid": "That code is not right.",
        "PasswordHashInvalid": "Wrong two-step verification password.",
        "ApiIdInvalid": "API_ID and API_HASH do not match. Check both.",
        "FloodWait": "Telegram is rate-limiting this number. Try again later.",
    }
    if name in mapping:
        return mapping[name]
    text = str(exc).strip()
    return f"{name}: {text[:200]}" if text else name


session_generator = SessionGenerator()
