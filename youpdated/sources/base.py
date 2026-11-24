"""Source plugin contract"""

from __future__ import annotations

import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Iterable, Protocol, runtime_checkable
from urllib.parse import urlsplit

from ..http import Client
from ..models import Target, Update
from ..versions import release


class ConfigEntryError(Exception):
    """Unknown entry or formats"""


@runtime_checkable
class Source(Protocol):
    name: ClassVar[str]
    # shown by `youpdated sources`
    summary: ClassVar[str]

    def targets(self, entries: list[Any]) -> list[Target]:
        """Normalize raw config into targets"""
        ...

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        """Fetch current items for one target. May raise errs; collected."""
        ...


def accepted(source: Source, entry: str) -> str | None:
    """``entry`` if ``source`` reads it, for a ``claim()`` to return"""
    try:
        source.targets([entry])
    except ConfigEntryError:
        return None
    return entry


def url_parts(value: str) -> tuple[str, str] | None:
    """``(host, path)`` of an http(s) URL, host lowercased without `www.`; else None"""
    if "://" not in value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.removeprefix("www."), parts.path


# Keys `entry_fields` gave scalar entries while :func:`scalar_key` listens
_SCALAR_KEYS: ContextVar[list[str] | None] = ContextVar("scalar_keys", default=None)


def entry_fields(entry: Any, scalar_key: str, source: str) -> dict[str, Any]:
    """Accept both config shapes: a scalar or mapping.

    ``- python/cpython`` and ``- {repo: python/cpython, watch: [...]}`` both arrive and leave as dict
    """
    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, (str, int, float)):
        heard = _SCALAR_KEYS.get()
        if heard is not None:
            heard.append(scalar_key)
        return {scalar_key: entry}
    raise ConfigEntryError(f"sources.{source}: entry must be a value or a mapping, got {entry!r}")


def scalar_key(source: Source, entry: Any) -> str | None:
    """The key a scalar ``entry`` stands for: `- python/cpython` is `- repo: python/cpython`"""
    heard: list[str] = []
    token = _SCALAR_KEYS.set(heard)
    try:
        source.targets([entry])
    except Exception:  # a plugin may raise anything
        return None
    finally:
        _SCALAR_KEYS.reset(token)
    return heard[0] if len(set(heard)) == 1 else None


def require(fields: dict[str, Any], key: str, source: str) -> Any:
    value = fields.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ConfigEntryError(f"sources.{source}: entry is missing required `{key}`")
    return value


_FRACTION_RE = re.compile(r"\.(\d+)")


def parse_iso(value: Any) -> datetime | None:
    """An ISO 8601 timestamp from an API, in UTC if no zone; None if unreadable"""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


MAX_VERSIONS = 10


def recent_versions(
    ordered: Iterable[str],
    is_prerelease: Callable[[str], bool],
    latest: str | None = None,
    limit: int = MAX_VERSIONS,
) -> set[str]:
    """Which of ``ordered`` (newest first) to list.

    The newest ``limit``, plus the newest ``limit`` finals, plus ``latest``
    """
    ordered = [version for version in ordered if version]
    listed = set(ordered[:limit])
    listed |= set([version for version in ordered if not is_prerelease(version)][:limit])
    if latest:
        listed.add(latest)
    return listed


def newest_first(dated: list[tuple[str, datetime | None]]) -> list[tuple[str, datetime | None]]:
    """``(version, published)`` pairs sorted newest first, undated last"""
    return sorted(dated, key=lambda pair: pair[1] or _UNDATED, reverse=True)


_UNDATED = datetime.min.replace(tzinfo=timezone.utc)


def semver_prerelease(version: str) -> bool:
    """A SemVer version with a pre-release part, ``1.0.0-rc.1`` (build metadata ``+...`` aside)"""
    return "-" in version.split("+", 1)[0]


def highest(versions: Iterable[str]) -> str | None:
    """The version with the highest numbers, the first of a tie"""
    best: str | None = None
    best_numbers: tuple[int, ...] = ()
    for version in versions:
        numbers = release(version) or ()
        if best is None or numbers > best_numbers:
            best, best_numbers = version, numbers
    return best
