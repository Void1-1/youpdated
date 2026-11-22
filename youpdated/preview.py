"""What a value would watch, looked up before it's added"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .http import Client
from .models import Target, Update
from .registry import all_sources, canonical_name, get_source
from .sources.base import ConfigEntryError

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class Option:
    """An entry to add, and what fetching it found"""

    source: str
    entry: str
    target: Target
    # How it was found
    why: str | None = None
    newest: Update | None = None
    error: str | None = None


def resolver(value: str, picked: str | None = None) -> str | None:
    """The source that places ``value`` by fetching it. ``picked``: only that source is asked."""
    sources = all_sources()
    names = [canonical_name(picked)] if picked is not None else sorted(sources)
    for name in names:
        resolves = getattr(sources.get(name), "resolves", None)
        try:
            if resolves is not None and resolves(value):
                return name
        except Exception:
            continue
    return None


def resolve(name: str, value: str, client: Client) -> tuple[str, str]:
    """``(entry, why)`` the source ``name`` fetched for ``value``"""
    return all_sources()[name].resolve(value, client)


def target(name: str, entry: str) -> Target:
    """The target ``entry`` is under source ``name``; raises ``ConfigEntryError`` if none"""
    source = get_source(name)
    if source is None:
        raise ConfigEntryError(f"unknown source `{name}`")
    built = source.targets([entry])
    if not built:
        raise ConfigEntryError(f"`{entry}` names nothing to watch")
    return built[0]


def options(
    name: str,
    entry: str,
    client: Client,
    *,
    why: str | None = None,
    track_latest: bool = False,
) -> list[Option]:
    """``entry`` under source ``name``, and what fetching it offers in its place, best first"""
    first = _option(name, entry, why, track_latest)
    with client.run_scope():  # what `instead` fetches, the source's own fetch reuses
        offered = _instead(first, client)
        _fetch(first, client)
        others = []
        for source, other, other_why in offered:
            try:
                option = _option(source, other, other_why, track_latest)
            except ConfigEntryError:
                continue
            if (option.source, option.target.key) != (first.source, first.target.key):
                _fetch(option, client)
                others.append(option)
    working = [o for o in others if o.error is None]
    if first.error is not None and working:
        return working
    return [first, *others]


def _option(name: str, entry: str, why: str | None, track_latest: bool) -> Option:
    built = target(name, entry)
    built.track_latest = track_latest
    return Option(source=canonical_name(name), entry=entry, target=built, why=why)


def _instead(option: Option, client: Client) -> list[tuple[str, str, str]]:
    instead = getattr(get_source(option.source), "instead", None)
    if instead is None:
        return []
    try:
        return list(instead(option.target, client))
    except Exception:
        return []


def _fetch(option: Option, client: Client) -> None:
    source = get_source(option.source)
    assert source is not None
    try:
        items = list(source.fetch(option.target, client))
    except Exception as exc:
        option.error = f"{type(exc).__name__}: {exc}"
        return
    items = [u for u in items if not option.target.ignores(u.tags)] or items
    # the newest by date, else the first the source listed
    option.newest = max(items, key=lambda u: u.published or _EPOCH, default=None)
