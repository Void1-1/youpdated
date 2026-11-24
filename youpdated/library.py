"""Entry points for using Youpdated as a Python library, see docs/library.md"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from . import config as _config
from .config import Config, ConfigError
from .errors import YoupdatedError
from .http import Client, FetchError
from .models import Update
from .registry import all_sources, canonical_name
from .runner import _split_entry_rules, build_targets
from .sources.base import ConfigEntryError
from .versions import Highest, Release, release

# `user_agent:` when a host config doesnt set
LIBRARY_USER_AGENT = "youpdated"

_PRERELEASE_RE = re.compile(
    r"(?i)(?<![a-z])(?:alpha|beta|preview|pre|rc|dev|nightly|snapshot)(?![a-z])"
)


class NoVersionsError(YoupdatedError):
    """:func:`newer` found no versions to compare"""


def parse_config(raw: dict[str, Any], *, allow_empty: bool = True) -> Config:
    """A config from a mapping shaped like `youpdated.yaml`

    ``allow_empty``: a config watching nothing is fine
    """
    return _config.parse_config(
        raw, None, allow_empty=allow_empty, default_user_agent=LIBRARY_USER_AGENT, fix_hint=False
    )


def load_config(
    path: str | Path, *, passphrase: str | None = None, allow_empty: bool = True
) -> Config:
    """The config at ``path``, decrypted in memory with ``passphrase`` if encrypted

    Only ``path`` is read: there is no lookup of a default or project config.
    ``allow_empty``: a config watching nothing is fine, as for :func:`parse_config`
    """
    if path is None:
        raise TypeError("load_config() needs the path of a config file")
    path = Path(path).expanduser()
    try:
        text = _config.read_config_text(path, passphrase)
    except OSError as exc:
        raise ConfigError(f"{path}: can't read the config ({exc.strerror or exc})") from exc
    return _config.parse_config_text(
        text, path, allow_empty=allow_empty, default_user_agent=LIBRARY_USER_AGENT, fix_hint=False
    )


def newer(
    source: str,
    entry: str | dict[str, Any],
    *,
    current: str,
    versions: dict[str, Any] | None = None,
    ignore: list[str] | str | None = None,
    prereleases: bool = False,
    client: Client | None = None,
    product: str | None = None,
) -> list[Update]:
    """Releases of ``entry`` newer than ``current``, highest first; empty when up to date

    Unlike :func:`youpdated.run` there is no first-run baseline.
    Youpdated never checks for its own updates, and this checks whatever the host names.

    ``source`` and ``entry`` are what a config lists under `sources:`.
    ``versions`` and ``ignore`` are that entry's `versions:` and `ignore:` rules; a dict ``entry`` may carry them too.
    Prereleases are left out unless ``prereleases``.
    ``client`` is used and left open if given; otherwise one is made with ``product`` as the host's User-Agent token, and closed.
    Any failure to fetch or read a release list is a :class:`FetchError`, never an empty list.
    """
    current_release = release(current) if isinstance(current, str) else None
    if current_release is None:
        raise ValueError(f"current version {current!r} has no version numbers to compare")
    current_pep = _pep440(current)
    current_pre = _looks_prerelease(current, current_pep)

    known = all_sources()
    if canonical_name(source) not in known:
        raise ConfigError(
            f"unknown source `{source}`; known sources: {', '.join(sorted(known))}"
        )
    raw: dict[str, Any] = {
        "sources": {source: [entry]},
        # git and steam ask for the latest release too
        "settings": {"track_latest": True},
    }
    if versions is not None:
        raw["versions"] = {source: versions}
    if ignore is not None:
        raw["ignore"] = {source: ignore}
    config = parse_config(raw, allow_empty=False)
    # Raises ConfigError for a dict entry's bad `versions:`/`ignore:`
    # build_targets() would only collect it, below, as the source's ConfigEntryError
    _split_entry_rules(entry, source)
    targets, errors = build_targets(config)
    if errors:
        raise ConfigEntryError(errors[0].message)

    own = client is None
    if client is None:
        client = Client(product=product)
    fetched: list[Update] = []
    kept: list[Update] = []
    previous = Highest(final=None if current_pre else current_release, overall=current_release)
    sources = all_sources()
    try:
        client.check_proxy()
        # No run_scope: an entry is fetched once, and calls sharing a client stay independent
        for target in targets:
            # Validators and URLs are left out of the client's history
            with (
                client.unconditional(),
                client.deferred_validators(),
                client.tracked_urls(),
            ):
                try:
                    items = list(sources[target.source].fetch(target, client))
                except YoupdatedError:
                    raise
                except Exception as exc:
                    # A source that can't read the answer failed to fetch
                    raise FetchError(
                        f"{target.source} `{target.display}`: {type(exc).__name__}: {exc}"
                    ) from exc
            fetched.extend(items)
            kept.extend(target.versions.admit([u for u in items if target.keeps(u)], previous))
    finally:
        if own:
            client.close()

    if not any(release(u.version) is not None for u in fetched):
        shown = ", ".join(t.display for t in targets) or str(entry)
        raise NoVersionsError(
            f"{source} `{shown}`: no item has a version to compare against {current!r}"
        )

    found: dict[tuple[str, str, str], Update] = {}
    for update in kept:
        numbers = release(update.version)
        if numbers is None:
            continue
        pep = _pep440(update.version)
        pre = "prerelease" in update.tags or _looks_prerelease(update.version, pep)
        if pre and not prereleases:
            continue
        if _is_newer(numbers, pep, pre, current_release, current_pep, current_pre):
            found.setdefault(update.dedupe_key, update)
    return sorted(found.values(), key=_version_key, reverse=True)


def _pep440(version: str | None) -> Version | None:
    if not version:
        return None
    try:
        return Version(version)
    except InvalidVersion:
        return None


def _looks_prerelease(version: str | None, pep: Version | None) -> bool:
    if pep is not None:
        return pep.is_prerelease
    return bool(version and _PRERELEASE_RE.search(version))


def _is_newer(
    numbers: Release,
    pep: Version | None,
    pre: bool,
    current: Release,
    current_pep: Version | None,
    current_pre: bool,
) -> bool:
    if pep is not None and current_pep is not None:
        return pep > current_pep
    if numbers != current:
        return numbers > current
    return current_pre and not pre


def _version_key(update: Update) -> tuple[Release, tuple[Any, ...]]:
    pep = _pep440(update.version)
    return release(update.version) or (), (1, pep) if pep is not None else (0,)
