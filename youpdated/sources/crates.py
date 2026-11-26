"""Rust crate releases from the crates.io API"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, newest_first, parse_iso, recent_versions, require,
    semver_prerelease, url_parts,
)

# crates.io's: ASCII letters, digits, `-` and `_`, starting with a letter
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@register
class CratesSource:
    name: ClassVar[str] = "crates"
    summary: ClassVar[str] = "New releases of a Rust crate on crates.io"

    def claim(self, value: str) -> str | None:
        """crate page on crates.io or docs.rs"""
        url = url_parts(value)
        if url and url[0] in ("crates.io", "docs.rs"):
            segments = url[1].strip("/").split("/")
            if url[0] == "crates.io" and len(segments) >= 2 and segments[0] == "crates":
                return accepted(self, segments[1])
            if url[0] == "docs.rs" and segments[0] and segments[0] != "crate":
                return accepted(self, segments[0])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "crate", self.name)
            crate = str(require(fields, "crate", self.name)).strip()
            if not NAME_RE.match(crate):
                raise ConfigEntryError(f"sources.{self.name}: `{crate}` is not a crate name")
            # crates.io ignores case and treats `-` and `_` the same: `serde-json` = `serde_json`
            targets.append(
                Target(
                    source=self.name, key=crate.lower().replace("_", "-"),
                    label=fields.get("name") or crate, params={},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://crates.io/api/v1/crates/{target.key}"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no crate named `{target.key}` on crates.io")

        doc = fetched.json()
        crate = doc.get("crate") or {}
        name = crate.get("name") or target.key
        latest = crate.get("max_stable_version") or crate.get("default_version")
        dated = newest_first([
            (str(v["num"]), parse_iso(v.get("created_at")))
            for v in doc.get("versions") or ()
            if v.get("num") and not v.get("yanked")
        ])
        listed = recent_versions([v for v, _ in dated], semver_prerelease, latest)

        updates = []
        for version, published in dated:
            if version not in listed:
                continue
            tags = ("release",)
            if semver_prerelease(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{name} {version}",
                    url=f"https://crates.io/crates/{name}/{version}",
                    published=published,
                    version=version,
                    body=crate.get("description"),
                    tags=tags,
                )
            )
        return updates
