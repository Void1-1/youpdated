"""Go module versions from the module proxy (proxy.golang.org)

The proxy's version list has no dates, only listed versions get ``.info`` fetched.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, parse_iso, recent_versions, require,
    semver_prerelease, url_parts,
)

PROXY = "https://proxy.golang.org"
INFO_CACHE = "go_info"
# Letters, digits, `-._~/+`; the first element has a dot and a path follows
# (a module at a bare domain is allowed by Go but rare)
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~+-]*\.[A-Za-z0-9._~+-]+(/[A-Za-z0-9._~+-]+)+$")
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def _escape(path: str) -> str:
    """The proxy's case encoding: each capital letter becomes `!` and its lowercase"""
    return re.sub(r"[A-Z]", lambda m: "!" + m.group().lower(), path)


def _semver_key(version: str) -> tuple[Any, ...]:
    """Sort key in SemVer precedence; versions that aren't SemVer sort lowest"""
    match = SEMVER_RE.match(version)
    if not match:
        return (0,)
    core = tuple(int(part) for part in match.groups()[:3])
    pre = match.group(4)
    if pre is None:
        return (1, core, 1, ())
    # Numeric identifiers sort below alphanumeric ones and compare as numbers
    idents = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (1, core, 0, idents)


@register
class GoSource:
    name: ClassVar[str] = "go"
    summary: ClassVar[str] = "New versions of a Go module"
    caches: ClassVar[tuple[str, ...]] = (INFO_CACHE,)

    def claim(self, value: str) -> str | None:
        """module page on pkg.go.dev"""
        url = url_parts(value)
        if url and url[0] == "pkg.go.dev":
            path = url[1].strip("/").split("@", 1)[0]
            return accepted(self, path) if path else None
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "module", self.name)
            module = str(require(fields, "module", self.name)).strip().strip("/")
            if not PATH_RE.match(module):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{module}` is not a module path, "
                    "expected format: github.com/spf13/cobra"
                )
            targets.append(
                Target(source=self.name, key=module, label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        base = f"{PROXY}/{_escape(target.key)}/@v"
        fetched = client.get(f"{base}/list", conditional=True, soft_statuses=(404, 410))
        if fetched is None:
            return []
        if fetched.status in (404, 410):
            raise FetchError(f"no module named `{target.key}` on {PROXY}")

        versions = sorted(set(fetched.text.split()), key=_semver_key, reverse=True)
        if not versions:
            # No tagged versions
            return self._untagged(target, client)

        finals = [v for v in versions if not semver_prerelease(v)]
        latest = finals[0] if finals else versions[0]
        listed = recent_versions(versions, semver_prerelease, latest)
        times = self._times(target, client, base, [v for v in versions if v in listed])

        updates = []
        for version in versions:
            if version not in listed:
                continue
            tags = ("release",)
            if semver_prerelease(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            updates.append(self._update(target, version, parse_iso(times.get(version)), tags))
        return updates

    def _times(
        self, target: Target, client: Client, base: str, versions: list[str]
    ) -> dict[str, str]:
        """each version's publish time, from cache or `.info`"""
        cached = client.state.cache_get(INFO_CACHE, target.key) if client.state else None
        times: dict[str, str] = json.loads(cached) if cached else {}
        missing = [v for v in versions if v not in times]
        for version in missing:
            info = client.get(f"{base}/{version}.info", soft_statuses=(404, 410))
            if info is None or info.status != 200:
                continue
            when = (info.json() or {}).get("Time")
            if when:
                times[version] = str(when)
        if missing and client.state:
            client.state.cache_set(INFO_CACHE, target.key, json.dumps(times, sort_keys=True))
        return times

    def _untagged(self, target: Target, client: Client) -> list[Update]:
        fetched = client.get(
            f"{PROXY}/{_escape(target.key)}/@latest", soft_statuses=(404, 410)
        )
        if fetched is None:
            return []
        if fetched.status in (404, 410):
            raise FetchError(f"no module named `{target.key}` on {PROXY}")
        info = fetched.json() or {}
        version = info.get("Version")
        if not version:
            raise FetchError(f"{PROXY} gave no version for `{target.key}`")
        return [self._update(target, str(version), parse_iso(info.get("Time")), ("prerelease",))]

    def _update(self, target: Target, version: str, published: Any, tags: tuple[str, ...]) -> Update:
        return Update(
            source=self.name,
            target=target.key,
            uid=f"version:{version}",
            title=f"{target.key} {version}",
            url=f"https://pkg.go.dev/{target.key}@{version}",
            published=published,
            version=version,
            tags=tags,
        )
