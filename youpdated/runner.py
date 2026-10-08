"""Turns a config into a set of fetches and updates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from .config import Config, ConfigError, parse_duration, parse_ignore_list
from .http import Client
from .models import RunError, Target, Update
from .registry import all_sources
from .state import State


@dataclass
class RunResult:
    updates: list[Update] = field(default_factory=list)
    errors: list[RunError] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    #: Every target fetched this run was new, so nothing was reported
    baseline: bool = False
    #: Targets fetched for the first time; their items were recorded, not reported
    baselined: list[Target] = field(default_factory=list)
    total_fetched: int = 0
    #: Items dropped by an `ignore:` rule, before any new/seen comparison
    ignored: int = 0
    #: Seen items forgotten this run for passing the `expiry:`
    pruned: int = 0

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


#: ``--since`` takes the same durations as ``expiry:``
parse_since = parse_duration


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
    configured, errors = build_targets(config)
    # Sources whose targets could not be listed, so none of their history is known stale
    unlisted = {e.source for e in errors}
    targets = configured
    if only_sources:
        wanted = set(only_sources)
        targets = [t for t in targets if t.source in wanted]
        errors = [e for e in errors if e.source in wanted]

    result = RunResult(errors=errors, targets=targets)
    if not targets:
        return result

    sources = all_sources()
    fetched: list[Update] = []
    succeeded: dict[tuple[str, str], Target] = {}

    def work(target: Target) -> tuple[Target, list[Update] | Exception, int]:
        if progress is not None:
            progress.target_started(target)
        outcome: list[Update] | Exception
        dropped = 0
        # A first fetch must see the whole document
        first = not state.is_baselined(target.source, target.key)
        try:
            with client.unconditional() if first else nullcontext():
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
                succeeded.setdefault((target.source, target.key), target)

    result.total_fetched = len(fetched)

    fresh = set() if show_all else {
        entry for entry in succeeded if not state.is_baselined(*entry)
    }
    result.baselined = [t for entry, t in succeeded.items() if entry in fresh]
    result.baseline = bool(fresh) and len(fresh) == len(succeeded)

    known = [u for u in fetched if (u.source, u.target) not in fresh]
    new = known if show_all else state.filter_new(known)

    if since is not None:
        cutoff = datetime.now(timezone.utc) - since
        new = [u for u in new if u.published is None or u.published >= cutoff]

    new.sort(key=lambda u: u.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result.updates = new

    if save:
        state.mark_seen(fetched)
        state.mark_baselined(succeeded)
        state.set_last_run()
        # Prune only after marking. Targets not fetched cleanly (failed, or left out by --source) keep history
        result.pruned = state.prune(
            keep_targets={(t.source, t.key) for t in configured} - succeeded.keys(),
            keep_sources=unlisted,
        )

    return result
