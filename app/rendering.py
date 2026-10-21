"""Render a small, safe Markdown subset using Telegram's supported HTML tags."""

import re
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions, Message

Tag = tuple[str, str]
Span = tuple[str, tuple[Tag, ...]]
INLINE = re.compile(
    r"\\(?P<escaped>[\\`*_{}\[\]()#+\-.!>])"
    r"|`(?P<code>[^`\n]+)`"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*|__(?P<under>.+?)__"
    r"|(?<!\w)\*(?P<italic>[^*\n]+)\*(?!\w)"
    r"|(?<!\w)_(?P<underscore>[^_\n]+)_(?!\w)",
    re.DOTALL,
)


@dataclass(frozen=True)
class FormattedChunk:
    html: str
    text: str


def _inline(text: str, tags: tuple[Tag, ...] = (), depth=0) -> list[Span]:
    if depth >= 8:
        return [(text, tags)]
    result = []
    start = 0
    for match in INLINE.finditer(text):
        result.append((text[start : match.start()], tags))
        if match.group("escaped"):
            result.append((match.group("escaped"), tags))
        elif match.group("code") is not None:
            result.append((match.group("code"), (("<code>", "</code>"),)))
        elif match.group("label") is not None:
            url = match.group("url")
            try:
                parsed = urlsplit(url)
                _ = parsed.port
                safe = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and len(url) <= 2048
                    and not any(ord(char) < 33 for char in url)
                )
            except ValueError:
                safe = False
            if safe:
                link = (f'<a href="{escape(url, quote=True)}">', "</a>")
                result.extend(_inline(match.group("label"), (*tags, link), depth + 1))
            else:
                result.extend(_inline(match.group("label"), tags, depth + 1))
        else:
            strong = match.group("bold") or match.group("under")
            value = strong or match.group("italic") or match.group("underscore")
            tag = ("<b>", "</b>") if strong else ("<i>", "</i>")
            nested = tags if tag in tags else (*tags, tag)
            result.extend(_inline(value, nested, depth + 1))
        start = match.end()
    result.append((text[start:], tags))
    return result


def _spans(text: str) -> list[Span]:
    spans = []
    normal = []
    code = None

    def flush_normal():
        spans.extend(_inline("".join(normal)))
        normal.clear()

    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            flush_normal()
            if code is None:
                code = []
            else:
                spans.append(("".join(code), (("<pre>", "</pre>"),)))
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.*?)(\n?)$", line)
        if heading:
            flush_normal()
            spans.extend(_inline(heading[1], (("<b>", "</b>"),)))
            spans.append((heading[2], ()))
        else:
            line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
            normal.append(line)
    flush_normal()
    if code is not None:
        spans.append(("".join(code), (("<pre>", "</pre>"),)))
    return spans


def _chunk_ends(text: str, limit: int) -> list[int]:
    ends = []
    start = 0
    while start < len(text):
        end, units = start, 0
        while end < len(text):
            width = 2 if ord(text[end]) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end += 1
        if end < len(text):
            floor = start + (end - start) // 2
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, floor, end)
                if boundary >= floor:
                    end = boundary + len(separator)
                    break
        ends.append(end)
        start = end
    return ends


def format_chunks(text: str, limit=4096) -> list[FormattedChunk]:
    """Split rendered text by UTF-16 size; close/reopen formatting in each chunk."""
    if limit < 2:
        raise ValueError("Message limit must be at least 2.")
    chunks = []
    html_parts, text_parts = [], []
    spans = _spans(text)
    boundaries = iter(_chunk_ends("".join(value for value, _ in spans), limit))
    boundary = next(boundaries, None)
    position = 0

    def flush():
        nonlocal html_parts, text_parts
        if text_parts:
            chunks.append(FormattedChunk("".join(html_parts), "".join(text_parts)))
        html_parts, text_parts = [], []

    def append_fragment(fragment, tags):
        if fragment:
            raw = "".join(fragment)
            html_parts.append(
                "".join(tag[0] for tag in tags)
                + escape(raw, quote=False)
                + "".join(tag[1] for tag in reversed(tags))
            )
            text_parts.append(raw)
            fragment.clear()

    for value, tags in spans:
        fragment = []
        for char in value:
            if position == boundary:
                append_fragment(fragment, tags)
                flush()
                boundary = next(boundaries, None)
            fragment.append(char)
            position += 1
        append_fragment(fragment, tags)
    flush()
    return chunks


async def answer_formatted(message: Message, text: str) -> None:
    for chunk in format_chunks(text):
        try:
            await message.answer(
                chunk.html,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except TelegramBadRequest as exc:
            if "can't parse entities" not in exc.message.lower():
                raise
            await message.answer(chunk.text, parse_mode=None)
