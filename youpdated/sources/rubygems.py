"""Ruby gem releases from the rubygems.org API"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, highest, newest_first, parse_iso, recent_versions,
    require, url_parts,
)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@register
class RubyGemsSource:
    name: ClassVar[str] = "rubygems"
    summary: ClassVar[str] = "New releases of a Ruby gem"

    def claim(self, value: str) -> str | None:
        """gem page on rubygems.org"""
        url = url_parts(value)
        if url and url[0] == "rubygems.org":
            segments = url[1].strip("/").split("/")
            if len(segments) >= 2 and segments[0] == "gems":
                return accepted(self, segments[1])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "gem", self.name)
            gem = str(require(fields, "gem", self.name)).strip()
            if not NAME_RE.match(gem):
                raise ConfigEntryError(f"sources.{self.name}: `{gem}` is not a gem name")
            targets.append(Target(source=self.name, key=gem, label=fields.get("name"), params={}))
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://rubygems.org/api/v1/versions/{target.key}.json"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no gem named `{target.key}` on rubygems.org")

        # A version built for several platforms is listed once per platform
        first: dict[str, datetime | None] = {}
        prerelease: dict[str, bool] = {}
        summary: dict[str, str | None] = {}
        for item in fetched.json() or ():
            version = str(item.get("number") or "")
            if not version:
                continue
            published = parse_iso(item.get("created_at"))
            earlier = first.get(version)
            if version not in first or (published and (earlier is None or published < earlier)):
                first[version] = published
            prerelease[version] = bool(item.get("prerelease"))
            summary.setdefault(version, item.get("summary"))

        dated = newest_first(list(first.items()))
        latest = highest(v for v, _ in dated if not prerelease[v])
        listed = recent_versions([v for v, _ in dated], prerelease.__getitem__, latest)

        updates = []
        for version, published in dated:
            if version not in listed:
                continue
            tags = ("release",)
            if prerelease[version]:
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{target.key} {version}",
                    url=f"https://rubygems.org/gems/{target.key}/versions/{version}",
                    published=published,
                    version=version,
                    body=summary.get(version),
                    tags=tags,
                )
            )
        return updates
