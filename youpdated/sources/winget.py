"""Windows Package Manager (winget) versions from the community manifest repo

Anonymous GitHub API calls are limited to 60 an hour per IP; `GITHUB_TOKEN`, if set, is sent.
"""

from __future__ import annotations

import os
import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from ..versions import release
from .base import (
    ConfigEntryError, accepted, entry_fields, highest, recent_versions, require, url_parts,
)

CONTENTS = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests"
TOKEN_ENV = "GITHUB_TOKEN"
# `Publisher.Package`, each part without spaces or slashes
ID_RE = re.compile(r"^[^\s./\\]+(\.[^\s./\\]+)+$")
_PRERELEASE_RE = re.compile(r"(?i)(alpha|beta|preview|rc|pre|dev|nightly)")


def _is_prerelease(version: str) -> bool:
    return bool(_PRERELEASE_RE.search(version))


@register
class WingetSource:
    name: ClassVar[str] = "winget"
    summary: ClassVar[str] = "New versions of a winget package"

    def claim(self, value: str) -> str | None:
        """package page on winstall.app or winget.run"""
        url = url_parts(value)
        if url and url[0] in ("winstall.app", "winget.run"):
            segments = url[1].strip("/").split("/")
            if len(segments) >= 2 and segments[0] in ("apps", "pkg"):
                # winget.run's pages are /pkg/Publisher/Package
                package = ".".join(segments[1:3]) if url[0] == "winget.run" else segments[1]
                return accepted(self, package)
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if not ID_RE.match(package):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{package}` is not a winget package id, "
                    "expected `Publisher.Package` as `winget show` names it"
                )
            targets.append(
                Target(source=self.name, key=package, label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        path = "/".join(target.key.split("."))
        url = f"{CONTENTS}/{target.key[0].lower()}/{path}"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get(TOKEN_ENV)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        fetched = client.get(url, headers=headers, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            # winget ignores case, but the manifest repo's folders don't
            raise FetchError(
                f"no winget package `{target.key}` in microsoft/winget-pkgs. "
                "Package ids are case-sensitive here: write it as `winget show` prints it"
            )

        listing = fetched.json()
        if not isinstance(listing, list):
            raise FetchError(f"`{target.key}` is a file in microsoft/winget-pkgs, not a package")
        versions = [
            str(item["name"]) for item in listing
            if item.get("type") == "dir" and release(str(item.get("name") or "")) is not None
        ]
        if not versions:
            raise FetchError(
                f"`{target.key}` has no versions in microsoft/winget-pkgs; "
                "its folder may hold only sub-packages"
            )
        versions.sort(key=lambda v: release(v) or (), reverse=True)
        latest = highest(v for v in versions if not _is_prerelease(v))
        listed = recent_versions(versions, _is_prerelease, latest)

        updates = []
        for version in versions:
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
                    title=f"{target.display} {version}",
                    url=f"https://github.com/microsoft/winget-pkgs/tree/master/manifests/"
                    f"{target.key[0].lower()}/{path}/{version}",
                    version=version,
                    tags=tags,
                )
            )
        return updates
