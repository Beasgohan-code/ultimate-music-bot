"""Generate a now-playing image card.

Downloads the track's cover art, blurs it into a background, and composites the
title, artist, requester and a progress bar over it. Results are cached on disk
by track id so the same song never gets rendered twice.

Everything here degrades gracefully: if Pillow is missing, the network is down,
or the artwork 404s, the caller just gets None and falls back to a text card.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from bot.config import CACHE_DIR

logger = logging.getLogger(__name__)

W, H = 1280, 640
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:\\Windows\\Fonts\\segoeui.ttf",
]
_SAFE_ID = re.compile(r"[^\w\-]+")

THUMB_DIR = CACHE_DIR / "thumbs"


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    order = _FONT_CANDIDATES if bold else _FONT_CANDIDATES[::-1]
    for path in order:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _ellipsize(draw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return (text + "…") if text else ""


def _fmt(seconds: Any) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "--:--"
    if total <= 0:
        return "LIVE"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _fetch(url: str) -> bytes | None:
    if not url or not url.startswith("http"):
        return None
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                # Guard against a hostile or mislabelled URL handing us 50 MB.
                if int(resp.headers.get("Content-Length") or 0) > 12 * 1024 * 1024:
                    return None
                return await resp.read()
    except Exception as exc:
        logger.debug("Thumbnail fetch failed: %s", exc)
        return None


def _render(
    cover: bytes | None,
    title: str,
    artist: str,
    requester: str,
    duration: Any,
    elapsed: int,
    bot_name: str,
    out_path: Path,
) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    if cover:
        import io

        src = Image.open(io.BytesIO(cover)).convert("RGB")
    else:
        src = Image.new("RGB", (W, H), (18, 22, 34))

    # ── Background: cover art, cropped to fill, heavily blurred and darkened.
    bg = src.copy()
    ratio = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * ratio) + 1, int(bg.height * ratio) + 1), Image.LANCZOS)
    left = (bg.width - W) // 2
    top = (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(28))

    canvas = Image.new("RGB", (W, H))
    canvas.paste(bg, (0, 0))
    # Dark scrim so white text is always legible over a bright cover.
    scrim = Image.new("RGBA", (W, H), (8, 10, 18, 150))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), scrim).convert("RGB")

    draw = ImageDraw.Draw(canvas)

    # ── Cover art thumbnail with a soft border.
    art_size = 380
    art_x, art_y = 70, (H - art_size) // 2
    art = src.copy()
    r = max(art_size / art.width, art_size / art.height)
    art = art.resize((int(art.width * r) + 1, int(art.height * r) + 1), Image.LANCZOS)
    ax = (art.width - art_size) // 2
    ay = (art.height - art_size) // 2
    art = art.crop((ax, ay, ax + art_size, ay + art_size))

    from PIL import ImageOps

    art = ImageOps.expand(art, border=3, fill=(255, 255, 255))
    canvas.paste(art, (art_x, art_y))

    # ── Text column.
    tx = art_x + art_size + 60
    avail = W - tx - 70

    f_label = _font(26, True)
    f_title = _font(52, True)
    f_artist = _font(34)
    f_meta = _font(26)

    draw.text((tx, art_y + 6), "NOW PLAYING", font=f_label, fill=(120, 200, 255))
    draw.text(
        (tx, art_y + 52),
        _ellipsize(draw, title or "Unknown", f_title, avail),
        font=f_title,
        fill=(255, 255, 255),
    )
    if artist:
        draw.text(
            (tx, art_y + 122),
            _ellipsize(draw, artist, f_artist, avail),
            font=f_artist,
            fill=(196, 208, 228),
        )

    # ── Progress bar.
    bar_y = art_y + 210
    bar_h = 10
    draw.rounded_rectangle(
        [tx, bar_y, tx + avail, bar_y + bar_h], radius=5, fill=(78, 86, 104)
    )

    try:
        total = int(duration or 0)
    except (TypeError, ValueError):
        total = 0
    frac = 0.0 if total <= 0 else max(0.0, min(1.0, elapsed / total))
    if total <= 0:
        frac = 1.0  # live streams show a full bar

    if frac > 0:
        draw.rounded_rectangle(
            [tx, bar_y, tx + int(avail * frac), bar_y + bar_h],
            radius=5,
            fill=(94, 186, 255),
        )
        knob_x = tx + int(avail * frac)
        draw.ellipse(
            [knob_x - 11, bar_y - 6, knob_x + 11, bar_y + bar_h + 6], fill=(255, 255, 255)
        )

    draw.text((tx, bar_y + 30), _fmt(elapsed) if total else "LIVE", font=f_meta, fill=(210, 220, 238))
    right = _fmt(total)
    draw.text(
        (tx + avail - draw.textlength(right, font=f_meta), bar_y + 30),
        right,
        font=f_meta,
        fill=(210, 220, 238),
    )

    if requester:
        draw.text(
            (tx, bar_y + 76),
            _ellipsize(draw, f"Requested by {requester}", f_meta, avail),
            font=f_meta,
            fill=(150, 165, 190),
        )

    draw.text(
        (W - 70 - draw.textlength(bot_name, font=f_meta), H - 52),
        bot_name,
        font=f_meta,
        fill=(120, 134, 158),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=88, optimize=True)
    return out_path


async def now_playing_image(
    track: dict[str, Any],
    *,
    elapsed: int = 0,
    bot_name: str = "",
) -> Path | None:
    """Render (or reuse) a now-playing card. Returns None if unavailable."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return None

    ident = _SAFE_ID.sub("", str(track.get("id") or track.get("title", "x")))[:60]
    # Bucket the elapsed time so we reuse a render for ~15s rather than
    # regenerating on every panel refresh.
    bucket = int(elapsed // 15)
    out = THUMB_DIR / f"{ident}-{bucket}.jpg"
    if out.exists():
        return out

    cover = await _fetch(track.get("thumbnail", ""))
    try:
        return await asyncio.to_thread(
            _render,
            cover,
            str(track.get("title", "Unknown")),
            str(track.get("artist", "") or ""),
            str(track.get("requester", "") or ""),
            track.get("duration"),
            int(elapsed or 0),
            bot_name or "Music",
            out,
        )
    except Exception as exc:
        logger.warning("Thumbnail render failed: %s", exc)
        return None


async def prune_thumbnails(keep: int = 400) -> int:
    """Keep the cache from growing without bound."""
    if not THUMB_DIR.exists():
        return 0
    files = sorted(THUMB_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
