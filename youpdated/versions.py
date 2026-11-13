"""`versions:` rules: which versions of a target are reported"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .models import Update

# leading numbers `bump:` compares
BUMPS = {"major": 1, "minor": 2, "patch": 3}
# `bump: any` turns off a sources bump set
ANY_BUMP = "any"

Release = tuple[int, ...]

# first dotted numbers anywhere: `go1.21.0`, `pkg2@1.4.0`, `swift-6.0.3-RELEASE`
_DOTTED_RE = re.compile(r"\d+(?:\.\d+)+")
# Otherwise only at the start, after a word like `REL_` or `curl-`: underscored (`REL_17_2`) or
# lone (`10`), but not the year of a date (`bug-2026-08-12`) or a number mid-name (`fixes-v2`)
_LEADING_RE = re.compile(r"^[A-Za-z]*[-_ ]?v?(\d+(?:_\d+)+|\d+(?![-_]?\d))")
_MAX_DIGITS = 30


def release(version: str | None) -> Release | None:
    """the numbers of a version, `v3.15.0rc1` is ``(3, 15)``"""
    if not version:
        return None
    dotted = _DOTTED_RE.search(version)
    if dotted:
        parts = dotted.group().split(".")
    else:
        leading = _LEADING_RE.match(version)
        if not leading:
            return None
        parts = leading.group(1).split("_")
    if any(len(part) > _MAX_DIGITS for part in parts):
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def format_release(numbers: Release) -> str:
    return ".".join(map(str, numbers))


def _prefix(version: str) -> str | None:
    found = _DOTTED_RE.search(version) or _LEADING_RE.match(version)
    if found is None or release(version) is None:
        return None
    return version[: found.start(found.lastindex or 0)]


_GLOB_RE = re.compile(r"([*?\[])")
# most patterns offered
MAX_PATTERNS = 6


def suggest_patterns(versions: Iterable[str]) -> list[tuple[str, int]]:
    """`match:` globs that each take some of ``versions`` for an app to offer"""
    listed = list(dict.fromkeys(versions))
    prefixes = [prefix for version in listed if (prefix := _prefix(version)) is not None]
    candidates: list[str] = []
    if len(set(prefixes)) > 1:
        for prefix in dict.fromkeys(prefixes):
            candidates.append(_GLOB_RE.sub(r"[\1]", prefix) + ("*" if prefix else "[0-9]*"))
    elif prefixes:
        numbered = [numbers for version in listed if (numbers := release(version)) is not None]
        for size in (1, 2):
            heads = list(dict.fromkeys(_head(numbers, size) for numbers in numbered))
            if len(heads) > 1:
                start = _GLOB_RE.sub(r"[\1]", prefixes[0])
                candidates = [f"{start}{format_release(head)}.*" for head in heads]
                break
    counted = [
        (glob, sum(fnmatchcase(version, glob) for version in listed)) for glob in candidates
    ]
    return [(glob, count) for glob, count in counted if count][:MAX_PATTERNS]


def _prerelease(update: Update) -> bool:
    return "prerelease" in update.tags


@dataclass(frozen=True)
class Highest:
    """highest release of a target"""

    final: Release | None = None
    overall: Release | None = None

    @classmethod
    def parse(cls, text: str | None) -> Highest | None:
        if not text:
            return None
        final, _, overall = text.partition("|")
        return cls(release(final), release(overall))

    def __str__(self) -> str:
        return "|".join(format_release(n) if n else "" for n in (self.final, self.overall))


def highest(updates: Iterable[Update], previous: Highest | None) -> Highest | None:
    """``previous`` raised by versions of ``updates``"""
    finals, overall = [], []
    for update in updates:
        numbers = release(update.version)
        if numbers is None:
            continue
        overall.append(numbers)
        if not _prerelease(update):
            finals.append(numbers)
    if previous is not None:
        finals += [previous.final] if previous.final else []
        overall += [previous.overall] if previous.overall else []
    if not overall:
        return None
    return Highest(max(finals, default=None), max(overall))


def _head(numbers: Release, size: int) -> Release:
    return (numbers + (0,) * size)[:size]


def _earlier(
    other: Release, other_at: datetime | None, numbers: Release, at: datetime | None
) -> bool:
    """If ``other`` released before ``numbers``, by version when a date is missing"""
    if other_at is not None and at is not None:
        return (other_at, other) < (at, numbers)
    return other < numbers


@dataclass(frozen=True)
class VersionRules:
    """target's `versions:` rules"""

    # `major`, `minor`, `patch`: only versions that raise that number
    bump: str | None = None
    min: Release | None = None
    # Globs: only versions one of them matches
    match: tuple[str, ...] = ()

    @classmethod
    def from_parts(cls, parts: dict[str, Any]) -> VersionRules:
        """Rules from parsed config, see :func:`youpdated.config.parse_version_rules`"""
        bump = parts.get("bump")
        return cls(
            bump=None if bump == ANY_BUMP else bump,
            min=parts.get("min"),
            match=tuple(parts.get("match") or ()),
        )

    def matches(self, version: str | None) -> bool:
        """If ``version`` is a `match:`"""
        if not self.match or version is None:
            return True
        return any(fnmatchcase(version, glob) for glob in self.match)

    def admit(self, updates: list[Update], previous: Highest | None = None) -> list[Update]:
        """The ``updates`` reported by rules"""
        if self.bump is None and self.min is None:
            return updates
        listed = [
            (numbers, u.published, _prerelease(u))
            for u in updates
            if (numbers := release(u.version)) is not None
        ]
        kept = []
        for update in updates:
            # Commits, posts, status change: nothing to compare
            if update.version is None:
                kept.append(update)
                continue
            numbers = release(update.version)
            if numbers is None:
                continue
            if self.min is not None and numbers < self.min:
                continue
            if self.bump is not None and not self._bumps(
                numbers, update.published, _prerelease(update), listed, previous
            ):
                continue
            kept.append(update)
        return kept

    def _bumps(
        self,
        numbers: Release,
        at: datetime | None,
        prerelease: bool,
        listed: list[tuple[Release, datetime | None, bool]],
        previous: Highest | None,
    ) -> bool:
        """If ``numbers`` raises the bump's number over every release before it"""
        before = [
            other
            for other, other_at, other_prerelease in listed
            if (prerelease or not other_prerelease) and _earlier(other, other_at, numbers, at)
        ]
        if previous is not None:
            known = previous.overall if prerelease else previous.final
            if known is not None:
                before.append(known)
        if not before:
            return False
        assert self.bump is not None
        size = BUMPS[self.bump]
        return _head(numbers, size) > _head(max(before), size)
