"""Turns a config into a set of fetches and updates."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from .config import Config, ConfigError, parse_ignore_list
from .http import Client
from .models import RunError, Target, Update
from .registry import all_sources
from .state import State


@dataclass
class RunResult:
    updates: list[Update] = field(default_factory=list)
    errors: list[RunError] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    baseline: bool = False
    total_fetched: int = 0
    #: Items dropped by an `ignore:` rule, before any new/seen comparison
    ignored: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


@runtime_checkable
class ProgressReporter(Protocol):
    def run_started(self, total: int) -> None:
        """``total`` targets to be fetched."""

    def target_started(self, target: Target) -> None:
        """``target`` has been picked up by a worker."""

    def target_finished(self, target: Target, error: Exception | None) -> None:
        """``target`` is done, with the exception it raised or ``None``."""


_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(value: str) -> timedelta:
    """Parse a window like ``7d``, ``12h``, ``2w``."""
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise ValueError(f"`{value}` is not a duration like 30m, 12h, 7d, 2w")
    return timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})


def build_targets(config: Config) -> tuple[list[Target], list[RunError]]:
    available = all_sources()
    targets: list[Target] = []
    errors: list[RunError] = []

    for name, entries in config.sources.items():
        source = available.get(name)
        if source is None:
            errors.append(
                RunError(
                    source=name,
                    target="-",
                    message=f"unknown source `{name}`; known sources: "
                    + ", ".join(sorted(available)),
                )
            )
            continue
        base = config.ignored_tags(name)
        try:
            for entry in entries:
                entry_ignore, cleaned = _split_entry_ignore(entry, name)
                for target in source.targets([cleaned]):
                    target.ignore = base | entry_ignore
                    targets.append(target)
        except Exception as exc:
            errors.append(RunError(source=name, target="-", message=str(exc)))

    return targets, errors


def _split_entry_ignore(entry: Any, source: str) -> tuple[frozenset[str], Any]:
    """Take `ignore:` off a config entry before the source sees it"""
    if not isinstance(entry, dict) or "ignore" not in entry:
        return frozenset(), entry
    cleaned = dict(entry)
    raw = cleaned.pop("ignore")
    tags = parse_ignore_list(raw, f"sources.{source}: ", "ignore")
    return tags, cleaned


def run(
    config: Config,
    state: State,
    client: Client,
    *,
    only_sources: list[str] | None = None,
    show_all: bool = False,
    since: timedelta | None = None,
    save: bool = True,
    progress: ProgressReporter | None = None,
) -> RunResult:
    targets, errors = build_targets(config)
    if only_sources:
        wanted = set(only_sources)
        targets = [t for t in targets if t.source in wanted]
        errors = [e for e in errors if e.source in wanted]

    result = RunResult(errors=errors, targets=targets)
    if not targets:
        return result

    sources = all_sources()
    fetched: list[Update] = []

    def work(target: Target) -> tuple[Target, list[Update] | Exception, int]:
        if progress is not None:
            progress.target_started(target)
        outcome: list[Update] | Exception
        dropped = 0
        try:
            items = list(sources[target.source].fetch(target, client))
            kept = [u for u in items if not target.ignores(u.tags)]
            # Ignored items are not recorded as seen
            dropped = len(items) - len(kept)
            outcome = kept
        except Exception as exc:  # collected, non fatal
            outcome = exc
        if progress is not None:
            progress.target_finished(
                target, outcome if isinstance(outcome, Exception) else None
            )
        return target, outcome, dropped

    if progress is not None:
        progress.run_started(len(targets))

    workers = max(1, min(config.privacy.concurrency, len(targets)))
    # run_scope: targets that share an upstream document fetch it once between them
    with client.run_scope(), ThreadPoolExecutor(max_workers=workers) as pool:
        for target, outcome, dropped in pool.map(work, targets):
            result.ignored += dropped
            if isinstance(outcome, Exception):
                result.errors.append(
                    RunError(
                        source=target.source,
                        target=target.display,
                        message=f"{type(outcome).__name__}: {outcome}",
                    )
                )
            else:
                fetched.extend(outcome)

    result.total_fetched = len(fetched)

    # The first run would dump all published. Record baseline instead and report changes
    first_run = state.last_run() is None
    if first_run and not show_all:
        result.baseline = True
        if save:
            state.mark_seen(fetched)
            state.set_last_run()
        return result

    new = fetched if show_all else state.filter_new(fetched)

    if since is not None:
        cutoff = datetime.now(timezone.utc) - since
        new = [u for u in new if u.published is None or u.published >= cutoff]

    new.sort(key=lambda u: u.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result.updates = new

    if save:
        state.mark_seen(fetched)
        state.set_last_run()

    return result
