"""Turns a config into a set of fetches and updates."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config
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

    @property
    def ok(self) -> bool:
        return not self.errors


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
        try:
            targets.extend(source.targets(entries))
        except Exception as exc:
            errors.append(RunError(source=name, target="-", message=str(exc)))

    return targets, errors


def run(
    config: Config,
    state: State,
    client: Client,
    *,
    only_sources: list[str] | None = None,
    show_all: bool = False,
    since: timedelta | None = None,
    save: bool = True,
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

    def work(target: Target) -> tuple[Target, list[Update] | Exception]:
        try:
            return target, list(sources[target.source].fetch(target, client))
        except Exception as exc:  # collected, non fatal
            return target, exc

    workers = max(1, min(config.privacy.concurrency, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for target, outcome in pool.map(work, targets):
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
