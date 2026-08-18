"""Shared RSS/Atom handling

Steam, itch, GitHub, and YouTube all publish feeds, so entry to update mapping is here
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable

import feedparser

from ..models import Update

BODY_LIMIT = 400
_UNDATED = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None, limit: int | None = BODY_LIMIT) -> str | None:
    """Flatten feed HTML into plain-text summary"""
    if not html:
        return None
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        parser.parts = [re.sub(r"<[^>]+>", " ", html)]
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if not text:
        return None
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            # feedparser normalizes struct_time to UTC
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def entry_body(entry: Any) -> str | None:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return html_to_text(content[0]["value"])
    return html_to_text(entry.get("summary"))


def parse_feed(
    content: bytes,
    *,
    source: str,
    target: str,
    limit: int | None = 20,
    version_of: Callable[[Any], str | None] | None = None,
    tags: tuple[str, ...] = (),
) -> list[Update]:
    """Turn feed bytes into Updates, newest first."""
    parsed = feedparser.parse(content)
    updates: list[Update] = []

    for entry in parsed.entries[: limit or None]:
        link = entry.get("link") or ""
        title = (entry.get("title") or "").strip() or "(untitled)"
        uid = entry.get("id") or link or f"{title}|{entry.get('published', '')}"
        updates.append(
            Update(
                source=source,
                target=target,
                uid=str(uid),
                title=title,
                url=link,
                published=entry_datetime(entry),
                version=version_of(entry) if version_of else None,
                body=entry_body(entry),
                tags=tags,
            )
        )

    # Undated entries sort last
    updates.sort(key=lambda u: u.published or _UNDATED, reverse=True)
    return updates
