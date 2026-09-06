"""Translate PyTgCalls failures into something a user can act on.

Playback used to surface the raw exception — "Playback failed:
NoActiveGroupCall()" — followed by a generic catch-all hint. That tells
someone nothing about which of several very different problems they have:
the voice chat is not open, the assistant was never added, Telegram is
having a bad day, or the media itself is unplayable.

Each case has a different fix, so each gets its own message.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnosis:
    """A user-facing explanation of a failed join or stream."""

    title: str
    hint: str
    #: True when retrying unchanged might work (transient server problems).
    retryable: bool = False


def _assistant(name: str) -> str:
    return f"@{name}" if name else "the assistant"


def diagnose(exc: BaseException, assistant_username: str = "") -> Diagnosis:
    """Explain a PyTgCalls/streaming exception in plain language."""
    kind = type(exc).__name__
    text = str(exc).lower()
    who = _assistant(assistant_username)

    if kind == "NoActiveGroupCall":
        return Diagnosis(
            "There's no voice chat running here.",
            "Open the group menu and start a voice chat, then try again.",
        )

    if kind == "AlreadyJoinedError" or "already" in text and "join" in text:
        return Diagnosis(
            "I'm already connected to this voice chat.",
            "Use /stop first if you want to start something new.",
        )

    if kind in {"TelegramServerError", "TimedOutAnswer"} or "server error" in text:
        return Diagnosis(
            "Telegram's voice servers didn't respond.",
            "This is on Telegram's side and usually clears in a moment.",
            retryable=True,
        )

    if kind in {"ClientNotStarted", "MTProtoClientNotConnected", "NoMTProtoClientSet"}:
        return Diagnosis(
            "The streaming assistant isn't connected.",
            "Its session may have expired — the bot owner needs to refresh "
            "SESSION_STRING.",
        )

    if kind == "InvalidMTProtoClient":
        return Diagnosis(
            "The streaming assistant is misconfigured.",
            "The bot owner needs to check API_ID, API_HASH and SESSION_STRING.",
        )

    if kind in {"NoAudioSourceFound", "NoVideoSourceFound", "YtDlpError"}:
        return Diagnosis(
            "That media had no playable audio.",
            "The source may be region-locked or a broken link. Try another track.",
        )

    if kind == "InvalidVideoProportion":
        return Diagnosis(
            "That video's dimensions aren't supported.",
            "Try /play instead to stream it as audio only.",
        )

    if kind == "NotInCallError":
        return Diagnosis(
            "I'm not in the voice chat any more.",
            "Someone may have removed me. Start playback again.",
        )

    # Telegram-side permission problems arrive as plain RPC errors.
    if "chat_admin_required" in text or "not enough rights" in text:
        return Diagnosis(
            f"{who} isn't allowed to join the voice chat.",
            "Promote it to admin, or allow members to join voice chats in the "
            "group's permissions.",
        )
    if "userbanned" in text or "user_banned" in text or "kicked" in text:
        return Diagnosis(
            f"{who} is banned from this group.",
            "Unban it, then try again.",
        )
    if "channel_private" in text or "chat_write_forbidden" in text:
        return Diagnosis(
            f"{who} can't access this chat.",
            "Add it to the group and make sure it isn't restricted.",
        )
    if "flood" in text and "wait" in text:
        return Diagnosis(
            "Telegram is rate-limiting the assistant.",
            "Wait a minute before trying again.",
            retryable=True,
        )

    # Network failures reached here and were reported as "make sure a voice
    # chat is running" — sending users to check something that was never the
    # problem. Name the transport explicitly before falling back.
    from bot.services.music import looks_blocked, looks_transient

    raw = str(exc)
    if looks_blocked(raw):
        return Diagnosis(
            "The media host refused this server.",
            "This is an IP-level block, not a problem with your group. "
            "The operator needs to set COOKIES_DATA or YTDLP_PROXY.",
        )
    if looks_transient(raw):
        return Diagnosis(
            "Couldn't reach the media host.",
            "The network dropped the connection. This is usually temporary — try again.",
            retryable=True,
        )

    return Diagnosis(
        "Playback failed.",
        "Make sure a voice chat is running and the assistant can join it.",
    )
