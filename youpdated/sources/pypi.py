"""PyPI package releases from the JSON Simple API (PEP 691, with PEP 700 and PEP 792 fields)

A project marked archived, deprecated, or quarantined is reported once as its own item.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from packaging.utils import canonicalize_version
from packaging.version import InvalidVersion, Version

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require

MAX_VERSIONS = 10
SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
# PEP 508 project names
NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
# Distribution formats PyPI has accepted, the compound `.tar.*` ones before bare `.tar`
_EXTENSIONS = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.z", ".tgz", ".tbz",
    ".zip", ".whl", ".egg", ".exe", ".msi", ".rpm", ".dmg", ".tar",
)


def normalize(name: str) -> str:
    """PEP 503 normalization"""
    return re.sub(r"[-_.]+", "-", name).lower()


@register
class PyPISource:
    name: ClassVar[str] = "pypi"
    summary: ClassVar[str] = "New releases of a PyPI package"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if not NAME_RE.match(package):
                raise ConfigEntryError(f"sources.{self.name}: `{package}` is not a package name")
            targets.append(
                Target(source=self.name, key=normalize(package), label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        url = f"https://pypi.org/simple/{target.key}/"
        fetched = client.get(
            url, headers={"Accept": SIMPLE_JSON}, conditional=True, soft_statuses=(404,)
        )
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no package named `{target.key}` on PyPI")

        doc = fetched.json()
        dated = _live_versions(doc, target.key)
        latest = _latest(dated)
        dated.sort(key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        updates = [_status_update(doc, target)] if _status(doc) else []
        for version, published in dated[:MAX_VERSIONS]:
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
                    url=f"https://pypi.org/project/{target.key}/{version}/",
                    published=published,
                    version=version,
                    tags=tags,
                )
            )
        return updates


def _status(doc: dict[str, Any]) -> str | None:
    """the project's status if not active (PEP 792)"""
    status = (doc.get("project-status") or {}).get("status")
    return status if status and status != "active" else None


def _status_update(doc: dict[str, Any], target: Target) -> Update:
    status = _status(doc)
    return Update(
        source=PyPISource.name,
        target=target.key,
        uid=f"status:{status}",
        title=f"{target.key} is {status}",
        url=f"https://pypi.org/project/{target.key}/",
        body=(doc.get("project-status") or {}).get("reason"),
        tags=("status", status),
    )


def _live_versions(doc: dict[str, Any], project: str) -> list[tuple[str, datetime | None]]:
    """Each version with its first upload, skipping those without files or only yanked ones"""
    versions = [str(v) for v in doc.get("versions") or ()]
    exact = set(versions)
    by_canonical: dict[str, str] = {}
    for version in versions:
        by_canonical.setdefault(_canonical(version), version)

    first_upload: dict[str, datetime | None] = {}
    for file in doc.get("files") or ():
        version = _version_of(str(file.get("filename") or ""), project, exact, by_canonical)
        if version is None or file.get("yanked"):
            continue
        uploaded = _parse_iso(file.get("upload-time"))
        earliest = first_upload.get(version)
        if version not in first_upload or (uploaded and (earliest is None or uploaded < earliest)):
            first_upload[version] = uploaded
    return list(first_upload.items())


def _version_of(
    filename: str, project: str, exact: set[str], by_canonical: dict[str, str]
) -> str | None:
    """Which listed version a distribution file belongs to"""
    stem = filename
    extension = next((e for e in _EXTENSIONS if stem.lower().endswith(e)), "")
    stem = stem[: len(stem) - len(extension)]
    rest = next(
        (stem[i + 1 :] for i, c in enumerate(stem) if c == "-" and normalize(stem[:i]) == project),
        None,
    )
    if not rest:
        return None
    if extension == ".whl":
        # The wheel spec escapes `-` out of the version, so it ends at the next one
        cuts = [rest.find("-") if "-" in rest else len(rest)]
    else:
        cuts = [i for i, c in enumerate(rest) if c in "-."] + [len(rest)]
    for cut in reversed(cuts):
        prefix = rest[:cut]
        if prefix in exact:
            return prefix
        if _canonical(prefix) in by_canonical:
            return by_canonical[_canonical(prefix)]
    return None


def _latest(dated: list[tuple[str, datetime | None]]) -> str | None:
    """The highest final release, or highest pre-release if no final, as pip picks"""
    parsed = []
    for version, _ in dated:
        try:
            parsed.append((Version(version), version))
        except InvalidVersion:
            continue
    finals = [pair for pair in parsed if not pair[0].is_prerelease]
    return max(finals or parsed, default=(None, None))[1]


def _is_prerelease(version: str) -> bool:
    """Alphas, betas, release candidates, dev releases"""
    try:
        return Version(version).is_prerelease
    except InvalidVersion:
        return False


def _canonical(version: str) -> str:
    """`1.0.0` and `1.0` are one version; invalid strings return unchanged"""
    try:
        return canonicalize_version(version)
    except InvalidVersion:
        return version


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
