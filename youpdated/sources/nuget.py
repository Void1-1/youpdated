"""NuGet package releases from nuget.org's registration API"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    MAX_VERSIONS, ConfigEntryError, accepted, entry_fields, highest, newest_first, parse_iso,
    recent_versions, require, semver_prerelease, url_parts,
)

REGISTRATION = "https://api.nuget.org/v3/registration5-gz-semver2"
# NuGet's package id rules. `_` is already in `\w`, so only `.` and `-` separate runs
ID_RE = re.compile(r"^\w+(?:[.-]\w+)*$")
# Unlisting a version sets its published date to 1900, because why not ig
_UNLISTED_YEAR = 1900


@register
class NuGetSource:
    name: ClassVar[str] = "nuget"
    summary: ClassVar[str] = "New releases of a NuGet package"

    def claim(self, value: str) -> str | None:
        """package page on nuget.org"""
        url = url_parts(value)
        if url and url[0] == "nuget.org":
            segments = url[1].strip("/").split("/")
            if len(segments) >= 2 and segments[0] == "packages":
                return accepted(self, segments[1])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if not ID_RE.match(package):
                raise ConfigEntryError(f"sources.{self.name}: `{package}` is not a package id")
            # Ids are not case-sensitive
            targets.append(
                Target(
                    source=self.name, key=package.lower(), label=fields.get("name") or package,
                    params={},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"{REGISTRATION}/{target.key}/index.json"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no package named `{target.key}` on nuget.org")

        entries: list[dict[str, Any]] = []
        for page in reversed(fetched.json().get("items") or []):
            items = page.get("items")
            if items is None:
                linked = client.get(str(page["@id"]))
                if linked is None:
                    return []
                items = linked.json().get("items") or []
            entries = [i.get("catalogEntry") or {} for i in items] + entries
            finals = [e for e in entries if not semver_prerelease(str(e.get("version") or ""))]
            if len(finals) >= MAX_VERSIONS:
                break

        live: dict[str, dict[str, Any]] = {}
        for entry in entries:
            version = str(entry.get("version") or "")
            published = parse_iso(entry.get("published"))
            if not version or entry.get("listed") is False:
                continue
            if published and published.year == _UNLISTED_YEAR:
                continue
            live[version] = entry

        dated: list[tuple[str, datetime | None]] = newest_first(
            [(v, parse_iso(e.get("published"))) for v, e in live.items()]
        )
        latest = highest(v for v in live if not semver_prerelease(v))
        listed = recent_versions([v for v, _ in dated], semver_prerelease, latest)

        updates = []
        for version, published in dated:
            if version not in listed:
                continue
            entry = live[version]
            name = entry.get("id") or target.label or target.key
            tags = ("release",)
            if semver_prerelease(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version.lower()}",
                    title=f"{name} {version}",
                    url=f"https://www.nuget.org/packages/{name}/{version}",
                    published=published,
                    version=version,
                    body=entry.get("summary") or entry.get("description"),
                    tags=tags,
                )
            )
        return updates
