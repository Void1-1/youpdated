"""Linux app releases from Flathub's AppStream data

Releases come from an app's metainfo
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, highest, newest_first, recent_versions, require,
    url_parts,
)
from .feed import html_to_text

# Reverse-DNS app id with at least three parts
APP_ID_RE = re.compile(r"^[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*){2,}$")


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@register
class FlathubSource:
    name: ClassVar[str] = "flathub"
    summary: ClassVar[str] = "New releases of a Linux app on Flathub"

    def claim(self, value: str) -> str | None:
        """app page on flathub.org"""
        url = url_parts(value)
        if url and url[0] == "flathub.org":
            segments = url[1].strip("/").split("/")
            if "apps" in segments[:2]:
                rest = segments[segments.index("apps") + 1:]
                if rest:
                    return accepted(self, rest[0])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "app", self.name)
            app = str(require(fields, "app", self.name)).strip()
            if not APP_ID_RE.match(app):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{app}` is not a Flathub app id, "
                    "expected format: org.mozilla.firefox"
                )
            targets.append(Target(source=self.name, key=app, label=fields.get("name"), params={}))
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://flathub.org/api/v2/appstream/{target.key}"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no app `{target.key}` on Flathub")

        doc = fetched.json() or {}
        if not target.label:
            target.label = doc.get("name")
        name = doc.get("name") or target.display
        releases: dict[str, dict[str, Any]] = {}
        for item in doc.get("releases") or ():
            version = str(item.get("version") or "")
            if version and version not in releases:
                releases[version] = item

        is_development = lambda v: releases[v].get("type") == "development"  # noqa: E731
        dated = newest_first([(v, _timestamp(r.get("timestamp"))) for v, r in releases.items()])
        latest = highest(v for v, _ in dated if not is_development(v))
        listed = recent_versions([v for v, _ in dated], is_development, latest)

        updates = []
        for version, published in dated:
            if version not in listed:
                continue
            item = releases[version]
            tags = ("release",)
            if is_development(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{name} {version}",
                    url=item.get("url") or f"https://flathub.org/apps/{target.key}",
                    published=published,
                    version=version,
                    body=html_to_text(item.get("description")),
                    tags=tags,
                )
            )
        return updates
