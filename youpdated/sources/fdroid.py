"""Android app versions from F-Droid's package API

The API lists F-Droid build versions without dates.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, require, url_parts

# A Java package name: at least two parts
PACKAGE_RE = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$")


@register
class FDroidSource:
    name: ClassVar[str] = "fdroid"
    summary: ClassVar[str] = "New versions of an Android app on F-Droid"

    def claim(self, value: str) -> str | None:
        """app page on f-droid.org"""
        url = url_parts(value)
        if url and url[0] == "f-droid.org":
            segments = url[1].strip("/").split("/")
            if "packages" in segments[:2]:
                rest = segments[segments.index("packages") + 1:]
                if rest:
                    return accepted(self, rest[0])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if not PACKAGE_RE.match(package):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{package}` is not an Android package name, "
                    "expected format: org.fdroid.fdroid"
                )
            targets.append(
                Target(source=self.name, key=package, label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://f-droid.org/api/v1/packages/{target.key}"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no app `{target.key}` on F-Droid")

        doc = fetched.json() or {}
        suggested = doc.get("suggestedVersionCode")
        packages = sorted(
            (p for p in doc.get("packages") or () if p.get("versionCode") is not None),
            key=lambda p: int(p["versionCode"]),
            reverse=True,
        )
        updates = []
        for package in packages:
            code = int(package["versionCode"])
            version = str(package.get("versionName") or code)
            tags = ("release",)
            if suggested is not None and code > int(suggested):
                tags += ("prerelease",)
            if suggested is not None and code == int(suggested):
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"code:{code}",
                    title=f"{target.display} {version}",
                    url=f"https://f-droid.org/packages/{target.key}/",
                    version=version,
                    tags=tags,
                )
            )
        return updates
