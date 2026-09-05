"""Rich Message builder with automatic HTML fallback.

Telegram's Bot API 9.3 ``sendRichMessage`` supports structured blocks —
headings, tables, checklists, collapsible details, pull quotes, dividers and
expandable blockquotes.  Not every chat/bot can use it yet, so every card in
this module is built once as a :class:`RichCard` and can render **either**:

* a native ``InputRichMessage`` (blocks), or
* an equivalent HTML string, used automatically when the API says no.

The first ``sendRichMessage`` rejection flips a module-level switch so we stop
paying the round-trip cost and serve HTML for the rest of the process.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockDetails,
    InputRichBlockDivider,
    InputRichBlockExpandableBlockQuotation,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPreformatted,
    InputRichBlockPullQuotation,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichMessage,
    Message,
    RichBlockTableCell,
    RichTextBold,
    RichTextCode,
    RichTextItalic,
    RichTextMarked,
    RichTextSpoiler,
    RichTextStrikethrough,
    RichTextUnderline,
    RichTextUrl,
)

logger = logging.getLogger(__name__)

#: Flipped to False the first time the API rejects a rich message.
_RICH_SUPPORTED = True


def rich_supported() -> bool:
    return _RICH_SUPPORTED


def disable_rich(reason: str = "") -> None:
    global _RICH_SUPPORTED
    if _RICH_SUPPORTED:
        _RICH_SUPPORTED = False
        logger.info("Rich messages disabled for this process — falling back to HTML. %s", reason)


# ─────────────────────────────────────────────────────────────────────────────
# Inline text spans — each carries its own HTML rendering
# ─────────────────────────────────────────────────────────────────────────────

def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


@dataclass(slots=True)
class Span:
    """One inline run of text with a style."""

    text: str
    style: str = "plain"
    url: str = ""

    def to_rich(self) -> Any:
        t = self.text
        match self.style:
            case "bold":
                return RichTextBold(text=t)
            case "italic":
                return RichTextItalic(text=t)
            case "code":
                return RichTextCode(text=t)
            case "underline":
                return RichTextUnderline(text=t)
            case "strike":
                return RichTextStrikethrough(text=t)
            case "spoiler":
                return RichTextSpoiler(text=t)
            case "marked":
                return RichTextMarked(text=t)
            case "url":
                return RichTextUrl(text=t, url=self.url or t)
            case _:
                return t

    def to_html(self) -> str:
        t = esc(self.text)
        match self.style:
            case "bold":
                return f"<b>{t}</b>"
            case "italic":
                return f"<i>{t}</i>"
            case "code":
                return f"<code>{t}</code>"
            case "underline":
                return f"<u>{t}</u>"
            case "strike":
                return f"<s>{t}</s>"
            case "spoiler":
                return f"<tg-spoiler>{t}</tg-spoiler>"
            case "marked":  # no HTML equivalent — bold reads closest
                return f"<b>{t}</b>"
            case "url":
                return f'<a href="{esc(self.url or self.text)}">{t}</a>'
            case _:
                return t


def _style(text: Any, style: str) -> Span:
    """Apply a style, preserving a link when wrapping an existing url span."""
    if isinstance(text, Span):
        # Telegram renders one style per span; a link wins over decoration.
        if text.style == "url":
            return Span(text.text, "url", text.url)
        return Span(text.text, style, text.url)
    return Span(str(text), style)


# Convenience constructors
def plain(text: Any) -> Span: return _style(text, "plain")
def b(text: Any) -> Span: return _style(text, "bold")
def i(text: Any) -> Span: return _style(text, "italic")
def c(text: Any) -> Span: return _style(text, "code")
def u(text: Any) -> Span: return _style(text, "underline")
def s(text: Any) -> Span: return _style(text, "strike")
def spoiler(text: Any) -> Span: return _style(text, "spoiler")
def mark(text: Any) -> Span: return _style(text, "marked")
def a(text: Any, url: str) -> Span:
    return Span(text.text if isinstance(text, Span) else str(text), "url", url)


Inline = Span | str | Sequence["Span | str"]


def _norm(value: Inline) -> list[Span]:
    if isinstance(value, Span):
        return [value]
    if isinstance(value, str):
        return [Span(value)]
    out: list[Span] = []
    for item in value:
        out.extend(_norm(item))
    return out


def _rich_text(value: Inline) -> Any:
    spans = _norm(value)
    if len(spans) == 1 and spans[0].style == "plain":
        return spans[0].text
    return [sp.to_rich() for sp in spans]


def _wrap(value: Inline, tag: str, absorb: tuple[str, ...]) -> str:
    """Wrap `value` in <tag>, flattening spans whose style the tag already applies.

    Telegram renders one style per span, so <b><b>x</b></b> is invalid nesting.
    If every span is plain or already carries a style this tag provides, emit
    the raw text inside a single wrapper instead of nesting.
    """
    spans = _norm(value)
    if all(sp.style in absorb for sp in spans):
        inner = "".join(esc(sp.text) for sp in spans)
    else:
        inner = "".join(sp.to_html() for sp in spans)
    return f"<{tag}>{inner}</{tag}>"


def _html_text(value: Inline) -> str:
    return "".join(sp.to_html() for sp in _norm(value))


# ─────────────────────────────────────────────────────────────────────────────
# Blocks
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class _Block:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


class RichCard:
    """Fluent builder producing rich blocks *and* an HTML twin."""

    def __init__(self) -> None:
        self._blocks: list[_Block] = []

    # ── structural blocks ───────────────────────────────────────────────
    def heading(self, text: Inline, size: int = 1) -> "RichCard":
        self._blocks.append(_Block("heading", {"text": text, "size": max(1, min(3, size))}))
        return self

    def para(self, text: Inline) -> "RichCard":
        self._blocks.append(_Block("para", {"text": text}))
        return self

    def quote(self, lines: Iterable[Inline], credit: Inline | None = None) -> "RichCard":
        self._blocks.append(_Block("quote", {"lines": list(lines), "credit": credit}))
        return self

    def expandable(self, text: Inline) -> "RichCard":
        """Collapsible blockquote — perfect for long lyrics."""
        self._blocks.append(_Block("expandable", {"text": text}))
        return self

    def pull(self, text: Inline, credit: Inline | None = None) -> "RichCard":
        self._blocks.append(_Block("pull", {"text": text, "credit": credit}))
        return self

    def details(self, summary: Inline, lines: Iterable[Inline]) -> "RichCard":
        """Collapsed <details> section the user taps to expand."""
        self._blocks.append(_Block("details", {"summary": summary, "lines": list(lines)}))
        return self

    def bullets(self, items: Iterable[Inline], ordered: bool = False) -> "RichCard":
        self._blocks.append(_Block("list", {"items": list(items), "ordered": ordered}))
        return self

    def checklist(self, items: Iterable[tuple[bool, Inline]]) -> "RichCard":
        self._blocks.append(_Block("checklist", {"items": list(items)}))
        return self

    def table(self, header: Sequence[Inline], rows: Iterable[Sequence[Inline]]) -> "RichCard":
        self._blocks.append(_Block("table", {"header": list(header), "rows": [list(r) for r in rows]}))
        return self

    def pre(self, text: str, language: str = "") -> "RichCard":
        self._blocks.append(_Block("pre", {"text": text, "language": language}))
        return self

    def divider(self) -> "RichCard":
        self._blocks.append(_Block("divider", {}))
        return self

    def footer(self, text: Inline) -> "RichCard":
        self._blocks.append(_Block("footer", {"text": text}))
        return self

    def blank(self) -> "RichCard":
        self._blocks.append(_Block("blank", {}))
        return self

    # ── rendering ───────────────────────────────────────────────────────
    def to_rich_message(self) -> InputRichMessage:
        blocks: list[Any] = []
        for blk in self._blocks:
            p = blk.payload
            match blk.kind:
                case "heading":
                    blocks.append(
                        InputRichBlockSectionHeading(text=_rich_text(p["text"]), size=p["size"])
                    )
                case "para":
                    blocks.append(InputRichBlockParagraph(text=_rich_text(p["text"])))
                case "quote":
                    inner = [InputRichBlockParagraph(text=_rich_text(ln)) for ln in p["lines"]]
                    if not inner:
                        continue
                    blocks.append(
                        InputRichBlockBlockQuotation(
                            blocks=inner,
                            credit=_rich_text(p["credit"]) if p["credit"] else None,
                        )
                    )
                case "expandable":
                    blocks.append(
                        InputRichBlockExpandableBlockQuotation(text=_rich_text(p["text"]))
                    )
                case "pull":
                    blocks.append(
                        InputRichBlockPullQuotation(
                            text=_rich_text(p["text"]),
                            credit=_rich_text(p["credit"]) if p["credit"] else None,
                        )
                    )
                case "details":
                    inner = [InputRichBlockParagraph(text=_rich_text(ln)) for ln in p["lines"]]
                    blocks.append(
                        InputRichBlockDetails(
                            summary=_rich_text(p["summary"]),
                            blocks=inner or [InputRichBlockParagraph(text="—")],
                        )
                    )
                case "list":
                    items = [
                        InputRichBlockListItem(
                            blocks=[InputRichBlockParagraph(text=_rich_text(it))]
                        )
                        for it in p["items"]
                    ]
                    if not items:
                        continue
                    blocks.append(InputRichBlockList(items=items, is_ordered=p["ordered"] or None))
                case "checklist":
                    items = [
                        InputRichBlockListItem(
                            blocks=[InputRichBlockParagraph(text=_rich_text(txt))],
                            has_checkbox=True,
                            is_checked=bool(done),
                        )
                        for done, txt in p["items"]
                    ]
                    if not items:
                        continue
                    blocks.append(InputRichBlockList(items=items))
                case "table":
                    cells: list[list[RichBlockTableCell]] = []
                    if p["header"]:
                        cells.append(
                            [
                                RichBlockTableCell(
                                    blocks=[InputRichBlockParagraph(text=_rich_text(h))],
                                    is_header=True,
                                    align="center",
                                    valign="middle",
                                )
                                for h in p["header"]
                            ]
                        )
                    for row in p["rows"]:
                        cells.append(
                            [
                                RichBlockTableCell(
                                    blocks=[InputRichBlockParagraph(text=_rich_text(cell))],
                                    align="left",
                                    valign="middle",
                                )
                                for cell in row
                            ]
                        )
                    if cells:
                        blocks.append(InputRichBlockTable(cells=cells, is_bordered=True))
                case "pre":
                    blocks.append(
                        InputRichBlockPreformatted(
                            text=p["text"], language=p["language"] or None
                        )
                    )
                case "divider":
                    blocks.append(InputRichBlockDivider())
                case "footer":
                    blocks.append(InputRichBlockFooter(text=_rich_text(p["text"])))
                case "blank":
                    continue
        if not blocks:
            blocks = [InputRichBlockParagraph(text="—")]
        return InputRichMessage(blocks=blocks)

    def to_html(self) -> str:
        out: list[str] = []
        for blk in self._blocks:
            p = blk.payload
            match blk.kind:
                case "heading":
                    out.append(_wrap(p["text"], "b", ("bold", "plain")))
                case "para":
                    out.append(_html_text(p["text"]))
                case "quote":
                    body = "\n".join(_html_text(ln) for ln in p["lines"])
                    if p["credit"]:
                        body += f"\n<i>— {_html_text(p['credit'])}</i>"
                    out.append(f"<blockquote>{body}</blockquote>")
                case "expandable":
                    out.append(f"<blockquote expandable>{_html_text(p['text'])}</blockquote>")
                case "pull":
                    body = f"<b><i>{_html_text(p['text'])}</i></b>"
                    if p["credit"]:
                        body += f"\n<i>— {_html_text(p['credit'])}</i>"
                    out.append(f"<blockquote>{body}</blockquote>")
                case "details":
                    body = "\n".join(_html_text(ln) for ln in p["lines"])
                    summary = _wrap(p["summary"], "b", ("bold", "plain"))
                    out.append(f"{summary}\n<blockquote expandable>{body}</blockquote>")
                case "list":
                    lines = []
                    for idx, item in enumerate(p["items"], 1):
                        bullet = f"{idx}." if p["ordered"] else "•"
                        lines.append(f"{bullet} {_html_text(item)}")
                    out.append("\n".join(lines))
                case "checklist":
                    lines = [
                        f"{'☑' if done else '☐'} {_html_text(txt)}" for done, txt in p["items"]
                    ]
                    out.append("\n".join(lines))
                case "table":
                    lines = []
                    if p["header"]:
                        lines.append(
                            "  ".join(f"<b>{_html_text(h)}</b>" for h in p["header"])
                        )
                    for row in p["rows"]:
                        lines.append("  ".join(_html_text(cell) for cell in row))
                    out.append("\n".join(lines))
                case "pre":
                    lang = f' class="language-{esc(p["language"])}"' if p["language"] else ""
                    out.append(f"<pre><code{lang}>{esc(p['text'])}</code></pre>")
                case "divider":
                    out.append("━━━━━━━━━━━━━━━")
                case "footer":
                    out.append(_wrap(p["text"], "i", ("italic", "plain")))
                case "blank":
                    out.append("")
        text = "\n".join(out).strip()
        # Telegram hard-caps message text at 4096 characters.
        return text[:4090] if len(text) > 4096 else text


# ─────────────────────────────────────────────────────────────────────────────
# Sending helpers — rich first, HTML on failure
# ─────────────────────────────────────────────────────────────────────────────

_RICH_ERROR_HINTS = (
    "rich_message",
    "RICH_MESSAGE",
    "not supported",
    "unknown method",
    "method not found",
    "BUTTON_TYPE_INVALID",
)


def _looks_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint.lower() in text for hint in _RICH_ERROR_HINTS)


async def send_card(
    message: Message,
    card: RichCard,
    *,
    reply_markup: Any = None,
    edit: Message | None = None,
    reply: bool = False,
    disable_preview: bool = True,
    transient: bool = False,
) -> Message | None:
    """Send (or edit) ``card`` using rich blocks, degrading to HTML.

    ``edit`` — when given, that message is edited instead of sending a new one.
    Rich messages cannot be edited, so editing always uses the HTML twin.

    ``transient`` — mark throwaway output (errors, acknowledgements) so clean
    mode can remove it later. Ignored unless the chat enabled clean mode.
    """
    if edit is not None:
        try:
            return await edit.edit_text(
                card.to_html(),
                parse_mode="HTML",
                reply_markup=reply_markup,
                link_preview_options=_no_preview() if disable_preview else None,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return edit
            logger.debug("Card edit failed: %s", exc)
            return edit

    sent: Message | None = None
    if _RICH_SUPPORTED:
        try:
            sender = message.reply_rich if reply else message.answer_rich
            sent = await sender(
                rich_message=card.to_rich_message(),
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            if _looks_unsupported(exc):
                disable_rich(str(exc))
            else:
                logger.debug("Rich send failed (%s) — using HTML", exc)
        except Exception as exc:  # network/serialisation issues shouldn't lose the message
            logger.debug("Rich send error (%s) — using HTML", exc)

    if sent is None:
        sender = message.reply if reply else message.answer
        sent = await sender(
            card.to_html(),
            parse_mode="HTML",
            reply_markup=reply_markup,
            link_preview_options=_no_preview() if disable_preview else None,
        )

    if transient:
        # Imported lazily: cleanup imports config/database, and rich.py is
        # pulled in by almost everything.
        from bot.services.cleanup import schedule_cleanup

        schedule_cleanup(sent)
    return sent


def _no_preview():
    from aiogram.types import LinkPreviewOptions

    return LinkPreviewOptions(is_disabled=True)


async def send_html(
    message: Message,
    text: str,
    *,
    reply_markup: Any = None,
    edit: Message | None = None,
    reply: bool = False,
) -> Message | None:
    """Plain HTML send/edit that never raises on 'message is not modified'."""
    try:
        if edit is not None:
            return await edit.edit_text(
                text, parse_mode="HTML", reply_markup=reply_markup,
                link_preview_options=_no_preview(),
            )
        sender = message.reply if reply else message.answer
        return await sender(
            text, parse_mode="HTML", reply_markup=reply_markup,
            link_preview_options=_no_preview(),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return edit
        raise
