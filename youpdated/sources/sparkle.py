"""Mac app releases from a Sparkle appcast

Separated from rss because some date data lives in metadata tags
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Iterable
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from ..versions import release
from .base import ConfigEntryError, entry_fields, highest, recent_versions, require, url_parts
from .feed import html_to_text

SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


def is_appcast_url(value: str) -> bool:
    """An http(s) URL whose path says it's an appcast (`appcast.xml`, `sparkle.xml`), as most do

    `sparkle` counts only in an `.xml` path
    """
    url = url_parts(value)
    if url is None:
        return False
    path = url[1].lower()
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return "appcast" in name or (path.endswith(".xml") and ("appcast" in path or "sparkle" in path))


def _sparkle(item: ET.Element, enclosure: ET.Element | None, name: str) -> str | None:
    """A Sparkle value from its element (Sparkle 2) or the enclosure's attribute (Sparkle 1)"""
    text = item.findtext(SPARKLE + name)
    if text and text.strip():
        return text.strip()
    if enclosure is not None:
        value = enclosure.get(SPARKLE + name)
        if value and value.strip():
            return value.strip()
    return None


def _published(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@register
class SparkleSource:
    name: ClassVar[str] = "sparkle"
    summary: ClassVar[str] = "New releases of a Mac app from its Sparkle appcast"

    def claim(self, value: str) -> str | None:
        """an http(s) URL with `appcast` or `sparkle` in its path"""
        return value if is_appcast_url(value) else None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "url", self.name)
            url = str(require(fields, "url", self.name)).strip()
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ConfigEntryError(f"sources.{self.name}: `{url}` is not an http(s) appcast URL")
            targets.append(Target(source=self.name, key=url, label=fields.get("name"), params={}))
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        fetched = client.get(target.key, conditional=True)
        if fetched is None:
            return []
        try:
            root = ET.fromstring(fetched.content)
        except ET.ParseError as exc:
            raise FetchError(f"{target.key} is not an appcast: {exc}") from None
        channel = root.find("channel")
        if root.tag != "rss" or channel is None:
            raise FetchError(f"{target.key} is not an appcast: no RSS channel")
        if not target.label:
            target.label = (channel.findtext("title") or "").strip() or None

        items: dict[str, dict[str, Any]] = {}
        for item in channel.iterfind("item"):
            enclosure = item.find("enclosure")
            build = _sparkle(item, enclosure, "version")
            shown = _sparkle(item, enclosure, "shortVersionString") or build
            if not build or not shown or build in items:
                continue
            items[build] = {
                "shown": shown,
                "title": (item.findtext("title") or "").strip(),
                "published": _published(item.findtext("pubDate")),
                "url": (item.findtext("link") or "").strip()
                or (item.findtext(SPARKLE + "releaseNotesLink") or "").strip()
                or (enclosure.get("url") if enclosure is not None else None)
                or target.key,
                "body": html_to_text(item.findtext("description")),
                "channel": (item.findtext(SPARKLE + "channel") or "").strip() or None,
                "critical": item.find(SPARKLE + "criticalUpdate") is not None,
            }
        if not items and channel.find("item") is not None:
            raise FetchError(f"{target.key} has items but none with a Sparkle version")

        undated = datetime.min.replace(tzinfo=timezone.utc)
        ordered = sorted(
            items,
            key=lambda b: (items[b]["published"] or undated, release(items[b]["shown"]) or ()),
            reverse=True,
        )
        on_channel = lambda build: items[build]["channel"] is not None  # noqa: E731
        latest = highest(b for b in ordered if not on_channel(b))
        listed = recent_versions(ordered, on_channel, latest)

        updates = []
        for build in ordered:
            if build not in listed:
                continue
            item = items[build]
            tags = ("release",)
            if item["channel"]:
                tags += ("prerelease", item["channel"].lower())
            if item["critical"]:
                tags += ("critical",)
            if build == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{build}",
                    title=item["title"] or f"{target.display} {item['shown']}",
                    url=item["url"],
                    published=item["published"],
                    version=item["shown"],
                    body=item["body"],
                    tags=tags,
                )
            )
        return updates
