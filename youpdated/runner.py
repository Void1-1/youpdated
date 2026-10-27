"""Turns a config into a set of fetches and updates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from .config import IGNORE_ALL, Config, parse_duration, parse_ignore_list
from .http import Client, Validator
from .models import RunError, Target, Update
from .registry import all_sources, canonical_name
from .state import State

# ``(source, key)`` of a target
Key = tuple[str, str]


@dataclass
class _Pending:
    """What a run has to record, see :func:`record`"""

    seen: list[Update]
    validators: list[Validator]
    links: list[tuple[str, str, str]]
    succeeded: dict[Key, Target]
    # Ignore rules of each entry fetched cleanly, by key
    rules: dict[Key, list[frozenset[str]]]
    keep_targets: set[Key]
    keep_sources: set[str]


@dataclass
class RunResult:
    updates: list[Update] = field(default_factory=list)
    errors: list[RunError] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    # Every target fetched this run was new, so nothing was reported
    baseline: bool = False
    # Targets fetched for the first time; their items were recorded, not reported
    baselined: list[Target] = field(default_factory=list)
    total_fetched: int = 0
    # Items dropped by an `ignore:` rule, before any new/seen comparison
    ignored: int = 0
    # Seen items forgotten this run for passing the `expiry:`
    pruned: int = 0
    # Config that does nothing, not a failure
    warnings: list[str] = field(default_factory=list)
    _pending: _Pending | None = field(default=None, repr=False, compare=False)

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


# ``--since`` takes the same durations as ``expiry:``
parse_since = parse_duration


def build_targets(config: Config) -> tuple[list[Target], list[RunError]]:
    available = all_sources()
    targets: list[Target] = []
    errors: list[RunError] = []

    for name, entries in config.sources.items():
        canonical = canonical_name(name)
        source = available.get(canonical)
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
        base = config.ignored_tags(canonical)
        if canonical != name:
            # `ignore: {github: [...]}` still covers entries listed under `github:`
            base |= config.ignored_tags(name)
        for entry in entries:
            # One bad entry is reported on its own; the rest of the source still runs
            try:
                entry_ignore, cleaned = _split_entry_ignore(entry, name)
                for target in source.targets([cleaned]):
                    target.ignore = base | entry_ignore
                    targets.append(target)
            except Exception as exc:
                errors.append(RunError(source=canonical, target="-", message=str(exc)))

    return targets, errors


def ignore_warnings(config: Config) -> list[str]:
    """`ignore:` keys with no source in config"""
    listed = set(config.sources) | {canonical_name(name) for name in config.sources}
    warnings = []
    for name in config.ignore:
        if name == IGNORE_ALL or name in listed:
            continue
        message = f"`ignore.{name}` covers nothing: no entries are listed under `sources.{name}`"
        canonical = canonical_name(name)
        if canonical != name and canonical in listed:
            message += f"; the ones under `sources.{canonical}` need `ignore.{canonical}`"
        warnings.append(message)
    return warnings


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
        wanted = {canonical_name(name) for name in only_sources}
        targets = [t for t in targets if t.source in wanted]
        errors = [e for e in errors if e.source in wanted]

    result = RunResult(errors=errors, targets=targets, warnings=ignore_warnings(config))
    if not targets:
        return result

    sources = all_sources()
    fetched: list[Update] = []
    succeeded: dict[Key, Target] = {}
    # Every entry's rules count for its key: a repo can be listed twice with different ones
    rules: dict[Key, list[frozenset[str]]] = {}
    for target in targets:
        rules.setdefault((target.source, target.key), []).append(target.ignore)
    succeeded_rules: dict[Key, list[frozenset[str]]] = {}
    # Stored only for targets whose items are recorded, see Client.deferred_validators
    validators: list[Validator] = []
    # (source, target, url) for `State.forget` to find target's validators with
    links: list[tuple[str, str, str]] = []

    def work(
        target: Target,
    ) -> tuple[Target, list[Update] | Exception, int, list[Validator], list[str]]:
        if progress is not None:
            progress.target_started(target)
        outcome: list[Update] | Exception
        dropped = 0
        # bound up front so a failure entering the scopes below is still collected
        pending: list[Validator] = []
        urls: list[str] = []
        # A first fetch must see the whole document, same with one thats ignore rules changed
        full = (
            not state.is_baselined(target.source, target.key)
            or state.ignore_rules_changed(
                target.source, target.key, rules[(target.source, target.key)]
            )
        )
        try:
            with (
                client.deferred_validators() as pending,
                client.tracked_urls() as urls,
                client.unconditional() if full else nullcontext(),
            ):
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
        return target, outcome, dropped, pending, urls

    if progress is not None:
        progress.run_started(len(targets))

    workers = max(1, min(config.privacy.concurrency, len(targets)))
    # run_scope: targets that share an upstream document fetch it once between them
    with client.run_scope(), ThreadPoolExecutor(max_workers=workers) as pool:
        for target, outcome, dropped, pending, urls in pool.map(work, targets):
            result.ignored += dropped
            if isinstance(outcome, Exception) and client.test:
                # Nothing was sent, so a source that needs an answer to go on stopped early
                continue
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
                validators.extend(pending)
                links.extend((target.source, target.key, url) for url in urls)
                succeeded.setdefault((target.source, target.key), target)
                succeeded_rules.setdefault((target.source, target.key), []).append(target.ignore)

    # One repo can be listed twice, e.g. under both `github:` and `git:` while moving over
    fetched = list({u.dedupe_key: u for u in reversed(fetched)}.values())[::-1]
    result.total_fetched = len(fetched)

    fresh = set() if show_all else {
        entry for entry in succeeded if not state.is_baselined(*entry)
    }
    result.baselined = [t for entry, t in succeeded.items() if entry in fresh]
    result.baseline = bool(fresh) and len(fresh) == len(succeeded)

    known = [u for u in fetched if (u.source, u.target) not in fresh]
    new = known if show_all else state.filter_new(known)

    # New items --since leaves out stay new, so a run without still reports
    withheld: set[tuple[str, str, str]] = set()
    if since is not None:
        cutoff = datetime.now(timezone.utc) - since

        def recent(update: Update) -> bool:
            return update.published is None or update.published >= cutoff

        withheld = {u.dedupe_key for u in new if not recent(u) and state.is_new(u)}
        new = [u for u in new if recent(u)]

    new.sort(key=lambda u: u.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result.updates = new
    result._pending = _Pending(
        seen=[u for u in fetched if u.dedupe_key not in withheld],
        validators=validators,
        links=links,
        succeeded=succeeded,
        rules=succeeded_rules,
        # Targets not fetched cleanly (failed, or left out by --source) keep history
        keep_targets={(t.source, t.key) for t in configured} - succeeded.keys(),
        keep_sources=unlisted,
    )

    if save:
        record(state, result)

    return result


def record(state: State, result: RunResult) -> None:
    """Record what :func:`run` fetched: seen items, validators, and baselines, then prune"""
    pending, result._pending = result._pending, None
    if pending is None:
        return
    state.mark_seen(pending.seen)
    for validator in pending.validators:
        state.remember_validators(*validator)
    state.link_urls(pending.links)
    state.mark_baselined(pending.succeeded)
    state.mark_ignore_rules((s, k, pending.rules[(s, k)]) for s, k in pending.succeeded)
    state.set_last_run()
    # Prune only after marking
    result.pruned = state.prune(
        keep_targets=pending.keep_targets, keep_sources=pending.keep_sources
    )
