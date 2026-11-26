"""PHP package releases from Packagist's Composer v2 metadata

Only tagged versions are read. Dev branches are in `~dev.json` which isn't fetched.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, highest, newest_first, parse_iso, recent_versions,
    require, url_parts,
)

# Composer's rule for `vendor/package`
NAME_RE = re.compile(r"^[a-z0-9]+(?:[_.-][a-z0-9]+)*/[a-z0-9]+(?:(?:[_.]|-{1,2})[a-z0-9]+)*$")
# Composer's unstable suffixes: alpha, beta, RC, dev, and `a1`/`b2`
_PRERELEASE_RE = re.compile(r"(?i)(alpha|beta|rc|dev)|\d[-.]?[ab]\d*$")


def _is_prerelease(version: str) -> bool:
    return bool(_PRERELEASE_RE.search(version))


def expand(minified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Composer's minified metadata: each entry only lists keys differing from the prior. `"__unset"` removes one"""
    expanded = []
    current: dict[str, Any] = {}
    for entry in minified:
        current = {**current, **entry}
        current = {k: v for k, v in current.items() if v != "__unset"}
        expanded.append(current)
    return expanded


@register
class PackagistSource:
    name: ClassVar[str] = "packagist"
    summary: ClassVar[str] = "New releases of a PHP package on Packagist"

    def claim(self, value: str) -> str | None:
        """package page on packagist.org, or a `vendor/package` that only Composer uses"""
        url = url_parts(value)
        if url and url[0] in ("packagist.org", "repo.packagist.org"):
            segments = url[1].strip("/").split("/")
            if len(segments) >= 3 and segments[0] == "packages":
                return accepted(self, f"{segments[1]}/{segments[2]}")
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip().lower()
            if not NAME_RE.match(package):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{package}` is not a package name, "
                    "expected `vendor/package`"
                )
            targets.append(
                Target(source=self.name, key=package, label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://repo.packagist.org/p2/{target.key}.json"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no package named `{target.key}` on Packagist")

        doc = fetched.json()
        entries = (doc.get("packages") or {}).get(target.key) or []
        if doc.get("minified") == "composer/2.0":
            entries = expand(entries)

        by_version = {str(e["version"]): e for e in entries if e.get("version")}
        dated = newest_first([(v, parse_iso(e.get("time"))) for v, e in by_version.items()])
        latest = highest(v for v, _ in dated if not _is_prerelease(v))
        listed = recent_versions([v for v, _ in dated], _is_prerelease, latest)

        updates = []
        for version, published in dated:
            if version not in listed:
                continue
            tags = ("release",)
            if _is_prerelease(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{target.key} {version}",
                    url=f"https://packagist.org/packages/{target.key}#{version}",
                    published=published,
                    version=version,
                    body=by_version[version].get("description"),
                    tags=tags,
                )
            )
        return updates
