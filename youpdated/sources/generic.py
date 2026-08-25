"""Any RSS or Atom feed

    sources:
      feed:
        - https://blog.rust-lang.org/feed.xml
        - url: https://github.com/obsidianmd/obsidian-releases/releases.atom
          name: Obsidian
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable
from urllib.parse import urlsplit

from ..http import Client
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import feed_title, parse_document, parse_entries

MAX_ITEMS = 20
VERSION_RE = re.compile(r"\bv?(\d+\.\d+[\d.]*(?:-[A-Za-z0-9.]+)?)\b")


@register
class FeedSource:
    name: ClassVar[str] = "feed"
    summary: ClassVar[str] = "Any RSS or Atom feed, for apps without a dedicated source"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "url", self.name)
            url = str(require(fields, "url", self.name)).strip()
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ConfigEntryError(
                    f"sources.{self.name}: `{url}` is not an http(s) feed URL"
                )
            targets.append(
                Target(
                    source=self.name,
                    key=url,
                    label=fields.get("name"),
                    params={"limit": int(fields.get("limit") or MAX_ITEMS)},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        fetched = client.get(target.key, conditional=True)
        if fetched is None:
            return []

        # One parse feeds both the label and the entries; feedparser is the most
        # expensive step in a run, and parsing the same bytes twice doubled it.
        parsed = parse_document(fetched.content)

        # Prefer the feed's title over raw URL
        if not target.label:
            target.label = feed_title(parsed) or urlsplit(target.key).netloc

        return parse_entries(
            parsed,
            source=self.name,
            target=target.key,
            limit=target.params["limit"],
            version_of=_version_from_title,
            tags=("item",),
        )


def _version_from_title(entry: Any) -> str | None:
    match = VERSION_RE.search(entry.get("title") or "")
    return match.group(1) if match else None
