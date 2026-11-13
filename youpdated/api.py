"""`youpdated api`: for desktop apps, JSON lines over stdio"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any, Callable, Iterator

from . import __version__, crypto, icons, notify
from .config import (
    DEFAULT_EXPIRY,
    PRIORITIES,
    Config,
    ConfigError,
    PrivacyConfig,
    default_config_path,
    default_state_path,
    find_config,
    load_config,
    parse_version_rules,
    read_config_text,
)
from .edit import (
    Watching,
    add_to_config,
    claims,
    remove_from_config,
    requested_urls,
    set_entry_rules,
    write_config,
)
from .edit import watching as entries_watching
from .http import Client, ProxyUnavailable, probe_proxy
from .importers import READERS, Found, read_file
from .models import RunError, Target, Update
from .registry import all_aliases, all_sources, canonical_name, get_source
from .runner import RunResult, _split_entry_rules, build_targets, record, run
from .sources.base import ConfigEntryError, Source, parse_iso
from .state import State, StateBusy, StateError, inbox_id, stamp
from .versions import ANY_BUMP, BUMPS, VersionRules, format_release, suggest_patterns

CONTRACT = 1

OPS = (
    "hello",
    "unlock",
    "status",
    "check",
    "inbox.list",
    "inbox.mark",
    "inbox.snooze",
    "inbox.delete",
    "targets.list",
    "targets.preview",
    "targets.add",
    "targets.remove",
    "import.preview",
    "import.apply",
    "settings.get",
    "settings.set",
    "priority.set",
    "filters.get",
    "filters.set",
    "filters.preview",
    "problems.list",
    "icon.get",
    "digest.send",
    "snapshot.write",
    "config.init",
    "shutdown",
)
# answerable while locked
_OPEN_OPS = frozenset({"hello", "status", "unlock", "shutdown"})

# app retries later
LOCK_TIMEOUT = 5.0
# `filters.preview` asks a source for its versions again after this long
LISTED_FOR = 600.0
# max versions returned
LISTED_MAX = 30
# how long a new check waits for the last one to finish fetchin an icon
ICON_WAIT = 30.0

# This app's releases
_APP_TAGS = {"darwin": "app-macos-v*", "win32": "app-windows-v*"}.get(sys.platform, "app-*")
SELF_WATCH: dict[str, Any] = {
    "repo": "Void1-1/youpdated",
    "ignore": ["prerelease"],
    "versions": {"match": _APP_TAGS},
}
_SELF_TARGET = ("git", "github.com/void1-1/youpdated")

STARTER_CONFIG = f"""\
# Written by the Youpdated app. Edit with the app or `youpdated add`;
# options: https://github.com/Void1-1/youpdated/blob/main/docs/configuration.md

sources:
  git:
    # Youpdated, so the app can give update notifications
    - repo: Void1-1/youpdated
      ignore: [prerelease]
      versions: {{match: "{_APP_TAGS}"}}
"""
# `config.init` without self-watch
_EMPTY_CONFIG = """\
# Written by the Youpdated app. Edit with the app or `youpdated add`;
# options: https://github.com/Void1-1/youpdated/blob/main/docs/configuration.md

sources: {}
"""

CHECK_INTERVALS = (1, 3, 6, 12, 24)
DEFAULT_SETTINGS: dict[str, Any] = {
    "check_interval_hours": 6,
    "digest_time": None,
    "os_notifications": True,
    "self_watch_declined": False,
    "cli_notice_shown": False,
    "show_icons": False,
}
_DIGEST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# A package-like name, which `targets.preview` offers to every source that reads it
_NAME_RE = re.compile(r"^[\w@][\w.@/+-]*$")
# Newest unread items in the widget's snapshot
SNAPSHOT_ITEMS = 10
_SNAPSHOT_FIELDS = ("id", "source", "display", "title", "url", "found_at")

Key = tuple[str, str]
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class ApiError(Exception):
    """A failed op, with a contract error code"""

    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code = code
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.extra}


def _bad(message: str) -> ApiError:
    return ApiError("bad_request", message)


_KINDS: dict[str, tuple[Callable[[Any], bool], str]] = {
    "bool": (lambda v: isinstance(v, bool), "true or false"),
    "str": (lambda v: isinstance(v, str), "a string"),
    "str?": (lambda v: v is None or isinstance(v, str), "a string or null"),
    "int": (lambda v: isinstance(v, int) and not isinstance(v, bool), "a whole number"),
    "object": (lambda v: isinstance(v, dict), "an object"),
    "strings": (
        lambda v: isinstance(v, list) and all(isinstance(s, str) for s in v),
        "a list of strings",
    ),
    "target": (
        lambda v: isinstance(v, dict)
        and set(v) == {"source", "key"}
        and all(isinstance(s, str) for s in v.values()),
        "an object with `source` and `key`",
    ),
}


class _Params:
    """op's params"""

    def __init__(self, raw: dict[str, Any]):
        self._raw = dict(raw)

    def get(self, name: str, kind: str, default: Any = None, *, required: bool = False) -> Any:
        if name not in self._raw:
            if required:
                raise _bad(f"`{name}` is required")
            return default
        value = self._raw.pop(name)
        if kind == "targets":
            check, what = _KINDS["target"]
            if not isinstance(value, list) or not all(check(v) for v in value):
                raise _bad(f"`{name}` must be a list of objects with `source` and `key`")
            return value
        check, what = _KINDS[kind]
        if not check(value):
            raise _bad(f"`{name}` must be {what}")
        return value

    def done(self) -> None:
        if self._raw:
            raise _bad(f"unknown param(s): {', '.join(sorted(self._raw))}")


class _Progress:
    """A check's progress as events"""

    def __init__(self, emit: Callable[[dict[str, Any]], None]):
        self._emit = emit
        self._lock = threading.Lock()
        self._done = 0
        self._of = 0

    def run_started(self, total: int) -> None:
        with self._lock:
            self._of = total
            self._emit({"event": "progress", "done": 0, "of": total})

    def target_started(self, target: Target) -> None:
        pass

    def target_finished(self, target: Target, error: Exception | None) -> None:
        # under lock, so `done` goes out in order
        with self._lock:
            self._done += 1
            self._emit({
                "event": "progress",
                "done": self._done,
                "of": self._of,
                "target": _ref(target),
                "failed": error is not None,
            })


def _ref(target: Target) -> dict[str, str]:
    return {"source": target.source, "key": target.key, "display": target.display}


def _reads(source: Source, value: str) -> bool:
    """If ``source`` takes ``value`` as an entry"""
    try:
        return bool(source.targets([value]))
    except Exception:  # a plugin may raise anything
        return False


def _starred(source: str, key: str) -> bool:
    return (source, key.lower()) == _SELF_TARGET


def _commit() -> str | None:
    """The short commit a frozen engine was built from"""
    try:
        build = importlib.import_module(f"{__package__}._build")
    except ImportError:
        return None
    return getattr(build, "COMMIT", None) or None


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _when(raw: str | None) -> str | None:
    """A stored ISO time as the contract writes times"""
    if not raw:
        return None
    try:
        return stamp(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _problem(error: RunError) -> dict[str, str | None]:
    kind = "notify" if error.source == "notify" else "config" if error.target == "-" else "source"
    return {
        "kind": kind,
        "source": error.source,
        "target": error.target,
        "key": error.key if kind == "source" else None,
        "message": error.message,
    }


def _update(row: dict[str, Any]) -> Update:
    """An inbox item as the update to notify about"""
    return Update(
        source=row["source"],
        target=row["target"],
        uid=row["uid"],
        title=row["title"],
        url=row["url"],
        published=parse_iso(row["published"]),
        version=row["version"],
        body=row["body"],
        tags=tuple(json.loads(row["tags"])),
    )


@dataclass
class _Filters:
    """A config entry's rules, and general ones"""

    # Why the app can't edit
    locked: str | None
    own_ignore: list[str]
    base_ignore: frozenset[str]
    own: dict[str, Any]
    base: dict[str, Any]

    def reply(self) -> dict[str, Any]:
        rules = VersionRules.from_parts({**self.base, **self.own})
        ignored = self.base_ignore | {tag.strip().lower() for tag in self.own_ignore}
        return {
            "match": [] if rules.match == ("*",) else list(rules.match),
            "skip_prereleases": "prerelease" in ignored,
            "skip_prereleases_inherited": "prerelease" in self.base_ignore,
            "bump": rules.bump or ANY_BUMP,
            "min": format_release(rules.min) if rules.min and rules.min != (0,) else None,
            "editable": self.locked is None,
            "reason": self.locked,
        }


def _pattern_label(glob: str) -> str:
    """`app-v*` as `app-v...`, and a line like `v3.12.*` as `v3.12.x`"""
    if glob == "[0-9]*":
        return "No prefix"
    if glob.endswith(".*"):
        return glob[:-1] + "x"
    return glob.removesuffix("*") + "…"


def _check_bump(bump: str) -> None:
    if bump not in (ANY_BUMP, *BUMPS):
        raise _bad(f"bump must be: {', '.join((ANY_BUMP, *BUMPS))}")


class Engine:
    """The app's working files, passphrase after unlock, running check"""

    def __init__(
        self,
        config: str | Path | None = None,
        state: str | Path | None = None,
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
        lock_timeout: float = LOCK_TIMEOUT,
    ):
        self.config_override = Path(config).expanduser() if config else None
        self.state_path = Path(state).expanduser() if state else default_state_path()
        self.emit = emit or (lambda event: None)
        self.lock_timeout = lock_timeout
        self._passphrase: str | None = None
        # versions `filters.preview` had a source list, by target, with when
        self._listed: dict[Key, tuple[float, list[Update]]] = {}
        # running check's history, other ops share until closed
        self._cond = threading.Condition()
        self._shared: State | None = None
        self._opening = False
        self._borrowers = 0
        # held from a check's start until icons fetched, after reply
        self._checking = threading.Lock()
        self._icon_job: tuple[int, Callable[[], None]] | None = None
        self._fetching_icons = False
        self._stop_icons = threading.Event()

    # dispatch

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        """reply to ``request``, without ``id``"""
        op = request.get("op")
        params = {k: v for k, v in request.items() if k not in ("id", "op")}
        try:
            handler = _HANDLERS.get(op) if isinstance(op, str) else None
            if handler is None:
                if not isinstance(op, str):
                    raise _bad("`op` must be a string")
                raise ApiError("unknown_op", f"unknown op `{op}`")
            if op not in _OPEN_OPS and self.locked():
                raise ApiError("locked", "the config and history are encrypted; unlock first")
            return {"ok": True, **handler(self, _Params(params))}
        except ApiError as exc:
            error = exc
        except ConfigError as exc:
            error = ApiError("config_error", str(exc))
        except crypto.EncryptionError as exc:
            error = ApiError(
                "config_error" if crypto.available() else "encryption_unavailable", str(exc)
            )
        except StateBusy as exc:
            error = ApiError("history_busy", str(exc))
        except (StateError, ProxyUnavailable, OSError) as exc:
            error = ApiError("io_error", str(exc))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            error = ApiError("internal", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": error.to_dict()}

    @property
    def checking(self) -> bool:
        return self._checking.locked()

    @property
    def fetching_icons(self) -> bool:
        """A check has replied and is fetching icons"""
        return self._fetching_icons

    # files

    def config_path(self) -> Path:
        """config in use, or where `config.init` would write one"""
        if self.config_override is not None:
            return self.config_override
        return find_config() or default_config_path()

    def encrypted(self) -> dict[str, bool]:
        return {
            "config": crypto.is_encrypted_file(self.config_path()),
            "history": crypto.is_encrypted_file(self.state_path),
        }

    def locked(self) -> bool:
        return self._passphrase is None and any(self.encrypted().values())

    def _passphrase_for(self, path: Path) -> str | None:
        return self._passphrase if crypto.is_encrypted_file(path) else None

    def _setup_passphrase(self) -> str | None:
        """Like CLI: either file encrypted means both are"""
        return self._passphrase if any(self.encrypted().values()) else None

    def _config(self) -> Config:
        path = self.config_path()
        if not path.is_file():
            raise ApiError("no_config", f"no config at {path}; `config.init` creates one")
        # watching nothing is a state the app shows, not an error
        return load_config(path, passphrase=self._passphrase_for(path), allow_empty=True)

    def _config_text(self) -> tuple[Path, str]:
        path = self.config_path()
        if not path.is_file():
            raise ApiError("no_config", f"no config at {path}; `config.init` creates one")
        return path, read_config_text(path, self._passphrase_for(path))

    def _write_config(self, path: Path, text: str) -> None:
        write_config(path, text, self._passphrase_for(path))

    def _open_state(self, config: Config | None) -> State:
        return State(
            self.state_path,
            passphrase=self._setup_passphrase(),
            expiry=config.expiry if config is not None else DEFAULT_EXPIRY,
            lock_timeout=self.lock_timeout,
        )

    @contextmanager
    def _history(self, config: Config | None = None) -> Iterator[State]:
        """running check's history, or one opened for this op"""
        with self._cond:
            # one opens at a time, or an op could wait out a whole check for the lock
            while self._opening:
                self._cond.wait()
            shared = self._shared
            if shared is not None:
                self._borrowers += 1
            else:
                self._opening = True
        if shared is None:
            try:
                state = self._open_state(config)
            finally:
                with self._cond:
                    self._opening = False
                    self._cond.notify_all()
            with state:
                yield state
            return
        try:
            yield shared
            shared.flush()
        finally:
            with self._cond:
                self._borrowers -= 1
                self._cond.notify_all()

    # what the history keeps for the app

    def _settings(self, state: State) -> dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        for name in settings:
            raw = state.cache_get("app", name)
            if raw is not None:
                try:
                    settings[name] = json.loads(raw)
                except ValueError:
                    pass
        return settings

    def _problems(self, state: State) -> tuple[list[dict[str, str | None]], str | None]:
        raw = state.cache_get("app", "problems")
        if not raw:
            return [], None
        try:
            stored = json.loads(raw)
            problems = [{"key": None, **problem} for problem in stored["problems"]]
            return problems, stored["checked_at"]
        except (ValueError, KeyError, TypeError):
            return [], None

    def _item(
        self,
        row: dict[str, Any],
        priorities: dict[Key, str],
        snoozes: dict[str, str],
        now: str,
    ) -> dict[str, Any]:
        key = (row["source"], row["target"])
        snoozed = snoozes.get(row["id"])
        return {
            "id": row["id"],
            "source": row["source"],
            "target": row["target"],
            "display": row["label"],
            "title": row["title"],
            "url": row["url"],
            "version": row["version"],
            "published": row["published"],
            "tags": json.loads(row["tags"]),
            "body": row["body"],
            "found_at": row["found_at"],
            "read": row["read_at"] is not None,
            "baseline": bool(row["baseline"]),
            "priority": priorities.get(key, "normal"),
            "starred": _starred(*key),
            "snoozed_until": snoozed if snoozed and snoozed > now else None,
        }

    def _items(self, state: State, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        priorities = state.priorities()
        snoozes = state.cache_items("app", "snooze:")
        now = stamp()
        return [self._item(row, priorities, snoozes, now) for row in rows]

    # ops

    def op_hello(self, p: _Params) -> dict[str, Any]:
        client = p.get("client", "object")
        p.done()
        if client is not None and not all(
            isinstance(client.get(k), str) for k in ("name", "version", "platform")
        ):
            raise _bad("`client` needs `name`, `version`, and `platform`")
        return {
            "engine": __version__,
            "contract": CONTRACT,
            "ops": list(OPS),
            "commit": _commit(),
            "encryption": crypto.available(),
        }

    def op_unlock(self, p: _Params) -> dict[str, Any]:
        passphrase = p.get("passphrase", "str", required=True)
        p.done()
        if not passphrase:
            raise _bad("`passphrase` must not be empty")
        encrypted = self.encrypted()
        if not any(encrypted.values()):
            return {"encrypted": encrypted}
        crypto.require_backend()
        for path, on in ((self.config_path(), encrypted["config"]), (self.state_path, encrypted["history"])):
            if on:
                try:
                    crypto.decrypt(path.read_bytes(), passphrase)
                except crypto.WrongPassphrase as exc:
                    raise ApiError("wrong_passphrase", str(exc)) from exc
        self._passphrase = passphrase
        return {"encrypted": encrypted}

    def op_status(self, p: _Params) -> dict[str, Any]:
        p.done()
        path = self.config_path()
        locked = self.locked()
        reply: dict[str, Any] = {
            "locked": locked,
            "encrypted": self.encrypted(),
            "config_path": str(path),
            "config_exists": path.is_file(),
            "config_error": None,
            "history_path": str(self.state_path),
            "last_run": None,
            "last_run_by": None,
            "self_watch": None,
            "counts": None,
        }
        if locked or not path.is_file():
            return reply
        config: Config | None = None
        try:
            config = self._config()
        except (ConfigError, crypto.EncryptionError) as exc:
            reply["config_error"] = str(exc)

        settings, unread, problems = dict(DEFAULT_SETTINGS), 0, 0
        # status doesn't create the history
        if self.state_path.exists() or self._shared is not None:
            with self._history(config) as state:
                last_run = state.last_run()
                by = state.cache_get("app", "last_run_by")
                settings = self._settings(state)
                unread = state.inbox_unread()
                problems = len(self._problems(state)[0])
            reply["last_run"] = stamp(last_run) if last_run else None
            reply["last_run_by"] = by if by in ("app", "cli") else None
        if config is not None:
            targets, _ = build_targets(config)
            keys = {(t.source, t.key.lower()) for t in targets}
            reply["self_watch"] = (
                "watching" if _SELF_TARGET in keys
                else "declined" if settings["self_watch_declined"]
                else "absent"
            )
            reply["counts"] = {
                "targets": len({(t.source, t.key) for t in targets}),
                "unread": unread,
                "problems": problems,
            }
        return reply

    def op_check(self, p: _Params) -> dict[str, Any]:
        sources = p.get("sources", "strings")
        only = p.get("targets", "targets")
        send = p.get("notify", "bool", True)
        p.done()
        if sources is not None:
            unknown = set(sources) - set(all_sources()) - set(all_aliases())
            if unknown:
                raise _bad(f"unknown source(s): {', '.join(sorted(unknown))}")
        if not self._checking.acquire(blocking=False):
            if not self.fetching_icons:
                raise ApiError("busy", "a check is already running")
            # the last check has replied
            self._stop_icons.set()
            if not self._checking.acquire(timeout=ICON_WAIT):
                raise ApiError("busy", "a check is already running")
        self._stop_icons.clear()
        try:
            reply = self._check(
                sources,
                None if only is None else [(t["source"], t["key"]) for t in only],
                send,
            )
        except BaseException:
            self._checking.release()
            raise
        if self._icon_job is None:
            self._checking.release()
        return reply

    def follow_up(self) -> None:
        """After a check's reply: fetch icons, then next check"""
        if self._icon_job is None or self._icon_job[0] != threading.get_ident():
            return
        job = self._icon_job[1]
        self._icon_job = None
        try:
            job()
        except Exception:  # the reply is out, nothing to answer with
            traceback.print_exc(file=sys.stderr)
        finally:
            self._fetching_icons = False
            self._checking.release()

    def _check(
        self, sources: list[str] | None, only: list[Key] | None, send: bool
    ) -> dict[str, Any]:
        config = self._config()
        if config.privacy.proxy:
            probe_proxy(config.privacy.proxy, timeout=config.privacy.timeout)
        with self._cond:
            while self._opening:
                self._cond.wait()
            self._opening = True
        try:
            state = self._open_state(config)
        except BaseException:
            with self._cond:
                self._opening = False
                self._cond.notify_all()
            raise
        with self._cond:
            self._shared = state
            self._opening = False
            self._cond.notify_all()
        # kept open after the reply when icons are fetched
        client = Client(config.privacy, state)
        due: list[Target] = []
        try:
            result = run(
                config,
                state,
                client,
                only_sources=sources,
                only_targets=only,
                save=False,
                progress=_Progress(self.emit),
            )
            record(state, result, by="app")
            reply = self._file(state, config, client, result, send, full=not (sources or only))
            if self._settings(state)["show_icons"]:
                due = self._icons_due(state, result.targets)
        except BaseException:
            client.close()
            raise
        finally:
            # ops sharing finish first
            with self._cond:
                self._shared = None
                while self._borrowers:
                    self._cond.wait()
            state.close()
        if not due:
            client.close()
            return reply
        self._fetching_icons = True
        self._icon_job = (
            threading.get_ident(),
            lambda: self._fetch_icons(config, client, due),
        )
        return reply

    def _file(
        self,
        state: State,
        config: Config,
        client: Client,
        result: RunResult,
        send: bool,
        *,
        full: bool,
    ) -> dict[str, Any]:
        """Put a check's findings in the inbox, and notify"""
        labels = {(t.source, t.key): t.display for t in result.targets}
        for target in result.targets:
            if target.label:
                state.cache_set("app", f"label:{target.source}:{target.key}", target.display)
        new_ids = state.inbox_add(result.updates, labels)
        baseline_ids = state.inbox_add(result.baseline_items, labels, baseline=True)
        if full:
            # Removed from the config
            watched = {(t.source, t.key) for t in result.targets}
            unlisted = {e.source for e in result.errors if e.target == "-"}
            state.inbox_drop_unread(
                key for key in state.inbox_targets() - watched if key[0] not in unlisted
            )

        fresh = set(new_ids)
        added = [u for u in result.updates if inbox_id(*u.dedupe_key) in fresh]
        settings = self._settings(state)
        priorities = state.priorities()
        digest = "hold" if settings["digest_time"] else None
        desktop = notify.Rules() if settings["os_notifications"] else None
        # Held for a digest that's been turned off: send now
        released = state.inbox_held() if send and digest is None else []
        late = [_update(row) for row in released]
        labels = {**{(row["source"], row["target"]): row["label"] for row in released}, **labels}
        alerts: list[dict[str, Any]] = []
        failures: list[RunError] = []
        held = 0
        if send and (added or released):
            endpoints: list[notify.Endpoint] = [*config.notify.webhooks, *config.notify.commands]
            if desktop is not None:
                endpoints.append(desktop)
            holding = [
                u for u in added
                if digest is not None
                and notify.priority_of(u, priorities) != "high"
                and any(
                    e.digest and notify.receives(e, notify.priority_of(u, priorities))
                    for e in endpoints
                )
            ]
            held = len(holding)
            state.inbox_set_notified((inbox_id(*u.dedupe_key) for u in holding), notified=False)
            state.inbox_set_notified(row["id"] for row in released)
            if desktop is not None:
                unread_late = [u for u, row in zip(late, released) if row["read_at"] is None]
                alerts = _alerts(
                    notify.takes(desktop, added, priorities, digest)
                    + notify.takes(desktop, unread_late, priorities, "send"),
                    priorities,
                    labels,
                )
            # save first
            state.flush()
            failures = notify.send(added, labels, config.notify, client, priorities, digest=digest)
            failures += notify.send(late, labels, config.notify, client, priorities, digest="send")

        checked_at = stamp()
        problems = [_problem(e) for e in [*result.errors, *failures]]
        state.cache_set("app", "problems", json.dumps({"checked_at": checked_at, "problems": problems}))
        rows = state.inbox_get([*new_ids, *baseline_ids, *(row["id"] for row in released)])
        return {
            "checked_at": checked_at,
            "new": len(added),
            "items": self._items(state, rows),
            "baselined": [_ref(t) for t in result.baselined],
            "errors": [_problem(e) for e in result.errors],
            "warnings": list(result.warnings),
            "notify_failures": [_problem(e) for e in failures],
            "alerts": alerts,
            "held": held,
            "unread": state.inbox_unread(),
        }

    def op_inbox_list(self, p: _Params) -> dict[str, Any]:
        unread = p.get("unread", "bool", False)
        source = p.get("source", "str")
        target = p.get("target", "target")
        sort = p.get("sort", "str", "newest")
        limit = p.get("limit", "int", 100)
        cursor = p.get("cursor", "str?")
        p.done()
        if sort not in ("newest", "priority", "source", "name"):
            raise _bad("sort must be: newest, priority, source, name")
        if not 1 <= limit <= 500:
            raise _bad("limit must be between 1 and 500")
        offset = 0
        if cursor is not None:
            match = re.fullmatch(r"c_(\d+)", cursor)
            if match is None:
                raise _bad("`cursor` is not one this engine gave out")
            offset = int(match.group(1))
        with self._history() as state:
            rows, total = state.inbox_query(
                unread=unread,
                source=None if source is None else canonical_name(source),
                target=None if target is None else (canonical_name(target["source"]), target["key"]),
                sort=sort,
                limit=limit,
                offset=offset,
            )
            end = offset + len(rows)
            return {
                "items": self._items(state, rows),
                "cursor": f"c_{end}" if end < total else None,
                "total": total,
                "unread": state.inbox_unread(),
            }

    def op_inbox_mark(self, p: _Params) -> dict[str, Any]:
        read = p.get("read", "bool", required=True)
        ids = p.get("ids", "strings")
        every = p.get("all", "bool")
        source = p.get("source", "str")
        p.done()
        if (ids is None) == (every is None) or every is False:
            raise _bad("give either `ids` or `all: true`")
        if ids is not None and not ids:
            raise _bad("`ids` must not be empty")
        if source is not None and ids is not None:
            raise _bad("`source` goes with `all`")
        with self._history() as state:
            changed = state.inbox_mark(
                read, ids=ids, source=None if source is None else canonical_name(source)
            )
            return {"changed": changed, "unread": state.inbox_unread()}

    def op_inbox_snooze(self, p: _Params) -> dict[str, Any]:
        ids = p.get("ids", "strings", required=True)
        minutes = p.get("minutes", "int", 60)
        p.done()
        if not ids:
            raise _bad("`ids` must not be empty")
        if not 1 <= minutes <= 10080:
            raise _bad("minutes must be between 1 and 10080")
        until = stamp(datetime.now(timezone.utc) + timedelta(minutes=minutes))
        with self._history() as state:
            found = [row["id"] for row in state.inbox_get(ids)]
            if not found:
                raise ApiError("not_found", "none of these items are in the inbox")
            for item in found:
                state.cache_set("app", f"snooze:{item}", until)
        return {"until": until}

    def op_inbox_delete(self, p: _Params) -> dict[str, Any]:
        ids = p.get("ids", "strings", required=True)
        p.done()
        if not ids:
            raise _bad("`ids` must not be empty")
        with self._history() as state:
            deleted = state.inbox_delete(ids)
            return {"deleted": deleted, "unread": state.inbox_unread()}

    def op_targets_list(self, p: _Params) -> dict[str, Any]:
        p.done()
        config = self._config()
        targets, _ = build_targets(config)
        with self._history(config) as state:
            labels = state.cache_items("app", "label:")
            priorities = state.priorities()
            baselines = state.cache_items("baseline")
            unread = state.inbox_unread_by_target()
            latest = state.latest()
            problems, _ = self._problems(state)
        listed = []
        seen: set[Key] = set()
        for t in targets:
            key = (t.source, t.key)
            if key in seen:
                continue
            seen.add(key)
            display = labels.get(f"{t.source}:{t.key}") or t.display
            # by key; older problems without one match by name
            names = {display, t.display, t.key}
            error = next(
                (
                    pr["message"] for pr in problems
                    if pr["kind"] == "source" and pr["source"] == t.source
                    and (pr["key"] == t.key if pr["key"] is not None else pr["target"] in names)
                ),
                None,
            )
            listed.append({
                "source": t.source,
                "key": t.key,
                "display": display,
                "priority": priorities.get(key, "normal"),
                "starred": _starred(*key),
                "last_checked": _when(baselines.get(f"{t.source}:{t.key}")),
                "last_error": error,
                "unread": unread.get(key, 0),
                "latest": latest.get(key),
            })
        return {"targets": listed}

    def op_targets_preview(self, p: _Params) -> dict[str, Any]:
        value = p.get("value", "str", required=True).strip()
        picked = p.get("source", "str")
        p.done()
        if not value:
            raise _bad("`value` must not be empty")
        if picked is not None:
            handler = get_source(picked)
            if handler is None:
                raise _bad(f"unknown source `{picked}`")
            name = canonical_name(picked)
            claim = getattr(handler, "claim", None)
            try:
                entry = (claim(value) if claim is not None else None) or value
            except Exception:  # a plugin's broken claim
                entry = value
        else:
            found = claims(value)
            claimed = bool(found)
            if not claimed and _NAME_RE.match(value):
                # A bare name: whichever sources read it, for the user to pick
                found = {n: value for n, s in sorted(all_sources().items()) if _reads(s, value)}
            if not found:
                raise ApiError(
                    "unrecognized", f"source type could not be determined for `{value}`"
                )
            if len(found) > 1 or not claimed:
                raise ApiError(
                    "ambiguous", f"`{value}` could be {' or '.join(found)}", choices=list(found)
                )
            name, entry = next(iter(found.items()))
            handler = all_sources()[name]
        try:
            built = handler.targets([entry])
        except Exception as exc:
            raise ApiError("bad_entry", str(exc)) from exc
        if not built:
            raise ApiError("bad_entry", f"`{value}` names nothing to watch")
        target = built[0]

        # a bad config might name a proxy
        config = self._config() if self.config_path().is_file() else None
        watching = False
        if config is not None:
            target.track_latest = config.track_latest
            configured, _ = build_targets(config)
            watching = any((t.source, t.key) == (name, target.key) for t in configured)
        newest, fetch_error = None, None
        with Client(config.privacy if config else PrivacyConfig()) as client:
            try:
                items = list(handler.fetch(target, client))
            except Exception as exc:
                fetch_error = f"{type(exc).__name__}: {exc}"
            else:
                items = [u for u in items if not target.ignores(u.tags)] or items
                # the newest by date, else the first the source listed
                top = max(items, key=lambda u: u.published or _EPOCH, default=None)
                if top is not None:
                    newest = {
                        "title": top.title,
                        "url": top.url,
                        "version": top.version,
                        "published": stamp(top.published) if top.published else None,
                    }
        return {
            "source": name,
            "entry": str(entry),
            "key": target.key,
            "display": target.display,
            "already_watching": watching,
            "newest": newest,
            "fetch_error": fetch_error,
        }

    def op_targets_add(self, p: _Params) -> dict[str, Any]:
        source = p.get("source", "str", required=True)
        entries = p.get("entries", "strings", required=True)
        p.done()
        if not entries:
            raise _bad("`entries` must not be empty")
        handler = get_source(source)
        if handler is None:
            raise _bad(f"unknown source `{source}`")
        refs = []
        for entry in entries:
            try:
                refs.append([_ref(t) for t in handler.targets([entry.strip()])])
            except ConfigEntryError as exc:
                raise ApiError("bad_entry", str(exc)) from exc
        path, text = self._config_text()
        change = add_to_config(text, source, entries)
        if change.applied:
            self._write_config(path, change.text)
        applied = {e.strip() for e in change.applied}
        return {
            "section": change.section,
            "added": change.applied,
            "skipped": change.skipped,
            "targets": [r for entry, rs in zip(entries, refs) if entry.strip() in applied for r in rs],
        }

    def op_targets_remove(self, p: _Params) -> dict[str, Any]:
        source = p.get("source", "str", required=True)
        key = p.get("key", "str", required=True)
        wipe = p.get("wipe", "bool", False)
        p.done()
        handler = get_source(source)
        if handler is None:
            raise _bad(f"unknown source `{source}`")
        name = canonical_name(source)
        config = self._config()
        path, text = self._config_text()
        change = remove_from_config(text, name, [key], by_key=True)
        if not change.applied:
            raise ApiError("not_found", f"not watching {key} under {name}")
        dropped = change.dropped_keys
        wiped = None
        # history first
        if wipe:
            wiped = {"seen": 0, "validators": 0, "inbox": 0}
            if self.state_path.exists() or self._shared is not None:
                with self._history(config) as state:
                    urls = requested_urls(handler, change.dropped, state)
                    wiped["inbox"] = state.forget_inbox(name, dropped)
                    wiped["seen"], wiped["validators"] = state.forget(
                        name,
                        dropped,
                        getattr(handler, "caches", ()),
                        urls,
                        shared=getattr(handler, "shared_caches", ()),
                    )
        elif self.state_path.exists():
            try:
                with self._history(config) as state:
                    state.inbox_drop_unread((name, k) for k in dropped)
            except StateBusy:
                pass  # the app's next check drops them
        self._write_config(path, change.text)
        return {"removed": change.applied, "wiped": wiped}

    def _found(self, p: _Params) -> Found:
        path = p.get("path", "str")
        content = p.get("content", "str")
        kind = p.get("format", "str")
        p.done()
        if (path is None) == (content is None):
            raise _bad("give either `path`, or `content` and `format`")
        found = Found()
        try:
            if path is not None:
                if kind is not None:
                    raise _bad("`format` goes with `content`")
                read_file(Path(path).expanduser(), found)
                return found
            if kind not in READERS:
                raise _bad(f"format must be one of {', '.join(READERS)}")
            with tempfile.TemporaryDirectory(prefix="youpdated-import-") as folder:
                # named after the format
                file = Path(folder) / kind
                file.write_text(content, encoding="utf-8")
                READERS[kind](file, found)
        except ConfigError as exc:
            raise _bad(str(exc)) from exc
        return found

    def op_import_preview(self, p: _Params) -> dict[str, Any]:
        found = self._found(p)
        text = self._config_text()[1] if self.config_path().is_file() else None
        add, already = [], []
        for source, entries in found.entries.items():
            if text is None:
                add.append({"source": source, "entries": entries})
                continue
            change = add_to_config(text, source, entries)
            text = change.text
            if change.applied:
                add.append({"source": source, "entries": change.applied})
            if change.skipped:
                already.append({"source": source, "entries": change.skipped})
        return {
            "add": add,
            "already": already,
            "skipped": [{"what": what, "why": why} for what, why in found.skipped],
            "warnings": list(found.warnings),
        }

    def op_import_apply(self, p: _Params) -> dict[str, Any]:
        found = self._found(p)
        path, text = self._config_text()
        edited = text
        added, refs = [], []
        for source, entries in found.entries.items():
            change = add_to_config(edited, source, entries)
            edited = change.text
            if change.applied:
                added.append({"source": source, "entries": change.applied})
                handler = all_sources()[source]
                for entry in change.applied:
                    try:
                        refs += [_ref(t) for t in handler.targets([entry])]
                    except ConfigEntryError:
                        pass
        if edited != text:
            self._write_config(path, edited)
        return {
            "added": added,
            "skipped": [{"what": what, "why": why} for what, why in found.skipped],
            "warnings": list(found.warnings),
            "targets": refs,
        }

    def op_settings_get(self, p: _Params) -> dict[str, Any]:
        p.done()
        with self._history() as state:
            return {"settings": self._settings(state)}

    def op_settings_set(self, p: _Params) -> dict[str, Any]:
        patch = p.get("settings", "object", required=True)
        p.done()
        if not patch:
            raise _bad("`settings` must not be empty")
        for name, value in patch.items():
            if name not in DEFAULT_SETTINGS:
                raise _bad(f"unknown setting `{name}`")
            if name == "check_interval_hours":
                if not _KINDS["int"][0](value) or value not in CHECK_INTERVALS:
                    raise _bad(
                        f"check_interval_hours must be one of {', '.join(map(str, CHECK_INTERVALS))}"
                    )
            elif name == "digest_time":
                if value is not None and not (isinstance(value, str) and _DIGEST_TIME_RE.match(value)):
                    raise _bad("digest_time must be HH:MM, or null for no digest")
            elif not isinstance(value, bool):
                raise _bad(f"{name} must be true or false")
        with self._history() as state:
            for name, value in patch.items():
                state.cache_set("app", name, json.dumps(value))
            if patch.get("show_icons") is False:
                state.clear_icons()
            return {"settings": self._settings(state)}

    def op_priority_set(self, p: _Params) -> dict[str, Any]:
        source = p.get("source", "str", required=True)
        key = p.get("key", "str", required=True)
        priority = p.get("priority", "str", required=True)
        p.done()
        if priority not in PRIORITIES:
            raise _bad(f"priority must be one of {', '.join(PRIORITIES)}")
        if get_source(source) is None:
            raise _bad(f"unknown source `{source}`")
        with self._history() as state:
            state.set_priority(canonical_name(source), key, priority)
        return {"priority": priority}

    # filters: an entry's `ignore: [prerelease]` and `versions:`

    def _watched(self, p: _Params) -> Key:
        source = p.get("source", "str", required=True)
        key = p.get("key", "str", required=True)
        if get_source(source) is None:
            raise _bad(f"unknown source `{source}`")
        return canonical_name(source), key

    def _filters(self, name: str, key: str) -> _Filters:
        config = self._config()
        _, text = self._config_text()
        found = entries_watching(text, name, key)
        if not found:
            raise ApiError("not_found", f"not watching {key} under {name}")
        hit: Watching = found[0]
        locked = None
        if len(found) > 1:
            locked = "It's listed multiple times in the config, so its filters are edited there."
        elif len(hit.keys) > 1:
            locked = "Its config entry follows other items too, so its filters are edited there."
        _, own, _ = _split_entry_rules(hit.entry, hit.section)
        listed = hit.entry.get("ignore") if isinstance(hit.entry, dict) else None
        # as build_targets gives them to the entry
        names = tuple(dict.fromkeys((name, hit.section)))
        return _Filters(
            locked=locked,
            own_ignore=[listed] if isinstance(listed, str) else [str(tag) for tag in listed or []],
            base_ignore=frozenset().union(*(config.ignored_tags(n) for n in names)),
            own=own,
            base=config.version_rules(*names),
        )

    def op_filters_get(self, p: _Params) -> dict[str, Any]:
        name, key = self._watched(p)
        p.done()
        return self._filters(name, key).reply()

    def op_filters_set(self, p: _Params) -> dict[str, Any]:
        name, key = self._watched(p)
        match = p.get("match", "strings", required=True)
        skip = p.get("skip_prereleases", "bool", required=True)
        bump = p.get("bump", "str", required=True)
        lowest = (p.get("min", "str?") or "").strip()
        p.done()
        _check_bump(bump)
        filters = self._filters(name, key)
        if filters.locked is not None:
            raise _bad(filters.locked)
        base = filters.base
        # Only what's different from the source rules
        own: dict[str, Any] = {}
        globs = [glob.strip() for glob in match if glob.strip()]
        if globs and tuple(globs) != base.get("match"):
            own["match"] = globs[0] if len(globs) == 1 else globs
        elif not globs and base.get("match", ("*",)) != ("*",):
            own["match"] = "*"
        if bump != base.get("bump", ANY_BUMP):
            own["bump"] = bump
        if lowest:
            own["min"] = lowest
        elif base.get("min", (0,)) != (0,):
            own["min"] = 0
        try:
            parsed = parse_version_rules(own, "", "filters")
        except ConfigError as exc:
            raise _bad(str(exc)) from exc
        if lowest and parsed["min"] == base.get("min"):
            del own["min"]
        ignore = [tag for tag in filters.own_ignore if tag.strip().lower() != "prerelease"]
        if skip and "prerelease" not in filters.base_ignore:
            ignore.append("prerelease")
        path, text = self._config_text()
        change = set_entry_rules(text, name, key, {"ignore": ignore, "versions": own})
        if change.applied:
            self._write_config(path, change.text)
        return self._filters(name, key).reply()

    def _listed_versions(self, name: str, key: str, refresh: bool) -> tuple[list[Update], str | None]:
        """The versions a target's source lists now, newest first, whatever its rules drop"""
        held = self._listed.get((name, key))
        if held is not None and not refresh and time.monotonic() - held[0] < LISTED_FOR:
            return held[1], None
        config = self._config()
        configured, _ = build_targets(config)
        target = next((t for t in configured if (t.source, t.key) == (name, key)), None)
        if target is None:
            raise ApiError("not_found", f"not watching {key} under {name}")
        plain = replace(target, ignore=frozenset(), versions=VersionRules(), track_latest=False)
        try:
            with Client(config.privacy) as client:
                items = list(all_sources()[name].fetch(plain, client))
        except Exception as exc:
            return [], f"{type(exc).__name__}: {exc}"
        versions = list({u.version: u for u in reversed(items) if u.version}.values())[::-1]
        versions.sort(key=lambda u: u.published or _EPOCH, reverse=True)
        self._listed[(name, key)] = (time.monotonic(), versions[:LISTED_MAX])
        return versions[:LISTED_MAX], None

    def op_filters_preview(self, p: _Params) -> dict[str, Any]:
        name, key = self._watched(p)
        match = p.get("match", "strings", [])
        skip = p.get("skip_prereleases", "bool", False)
        bump = p.get("bump", "str", ANY_BUMP)
        lowest = (p.get("min", "str?") or "").strip()
        refresh = p.get("refresh", "bool", False)
        p.done()
        _check_bump(bump)
        raw: dict[str, Any] = {"bump": bump}
        if globs := [glob.strip() for glob in match if glob.strip()]:
            raw["match"] = globs
        if lowest:
            raw["min"] = lowest
        try:
            rules = VersionRules.from_parts(parse_version_rules(raw, "", "filters"))
        except ConfigError as exc:
            raise _bad(str(exc)) from exc
        listed, error = self._listed_versions(name, key, refresh)
        fits = [
            update
            for update in listed
            if not (skip and "prerelease" in update.tags) and rules.matches(update.version)
        ]
        kept = {id(update) for update in rules.admit(fits)}
        versions = [
            {
                "version": update.version,
                "prerelease": "prerelease" in update.tags,
                "kept": id(update) in kept,
            }
            for update in listed
        ]
        return {
            "versions": versions,
            "kept": sum(v["kept"] for v in versions),
            "patterns": [
                {"pattern": glob, "label": _pattern_label(glob), "count": count}
                for glob, count in suggest_patterns(v["version"] for v in versions)
            ],
            "fetch_error": error,
        }

    def op_icon_get(self, p: _Params) -> dict[str, Any]:
        source = p.get("source", "str", required=True)
        key = p.get("key", "str", required=True)
        p.done()
        with self._history() as state:
            found = None
            if self._settings(state)["show_icons"]:
                found = state.icon(canonical_name(source), key)
        if found is None:
            return {"icon": None}
        mime, data = found
        return {"icon": {"mime": mime, "data": base64.b64encode(data).decode("ascii")}}

    def _icons_due(self, state: State, targets: list[Target]) -> list[Target]:
        """Up to ``icons.PER_CHECK`` targets with no icon, or an old one"""
        sources = all_sources()
        by_key = {
            (t.source, t.key): t
            for t in targets
            if hasattr(sources.get(t.source), "icon") and state.is_baselined(t.source, t.key)
        }
        due = state.icons_due(by_key, icons.REFRESH, icons.RETRY)
        return [by_key[key] for key in due[: icons.PER_CHECK]]

    def _fetch_icons(self, config: Config, client: Client, due: list[Target]) -> None:
        """After a check's reply: ``due``'s icons, one at a time, through the check's client"""
        sources = all_sources()
        client.state = None
        try:
            for target in due:
                if self._stop_icons.is_set():
                    return
                try:
                    found = icons.find(sources[target.source], target, client)
                except Exception:  # asked again after icons.RETRY
                    found = None
                kept = None if found is None else (found.mime, found.data)
                try:
                    with self._history(config) as state:
                        state.set_icon(target.source, target.key, kept)
                except StateError:
                    return
                if found is not None:
                    self.emit(
                        {"event": "icon", "target": {"source": target.source, "key": target.key}}
                    )
        finally:
            client.close()

    def op_problems_list(self, p: _Params) -> dict[str, Any]:
        p.done()
        with self._history() as state:
            problems, checked_at = self._problems(state)
        return {"problems": problems, "checked_at": checked_at}

    def op_digest_send(self, p: _Params) -> dict[str, Any]:
        p.done()
        config = self._config()
        with self._history(config) as state:
            rows = state.inbox_held()
            if not rows:
                return {"items": [], "alerts": [], "notify_failures": []}
            settings = self._settings(state)
            priorities = state.priorities()
            updates = [_update(row) for row in rows]
            labels = {(row["source"], row["target"]): row["label"] for row in rows}
            state.inbox_set_notified(row["id"] for row in rows)
            alerts: list[dict[str, Any]] = []
            if settings["os_notifications"]:
                unread = [_update(row) for row in rows if row["read_at"] is None]
                wanted = notify.takes(notify.Rules(), unread, priorities, "send")
                if wanted:
                    alerts = [{"priority": "normal", "target": None, "item_ids": _ids(wanted)}]
            # save first
            state.flush()
            with Client(config.privacy, state) as client:
                failures = notify.send(
                    updates, labels, config.notify, client, priorities, digest="send"
                )
            if failures:
                problems, checked_at = self._problems(state)
                problems += [_problem(e) for e in failures]
                state.cache_set(
                    "app", "problems",
                    json.dumps({"checked_at": checked_at or stamp(), "problems": problems}),
                )
            return {
                "items": self._items(state, rows),
                "alerts": alerts,
                "notify_failures": [_problem(e) for e in failures],
            }

    def op_snapshot_write(self, p: _Params) -> dict[str, Any]:
        path = Path(p.get("path", "str", required=True)).expanduser()
        p.done()
        with self._history() as state:
            # History-derived text must stay encrypted/safe
            redacted = state.encrypted
            rows = [] if redacted else state.inbox_query(unread=True, limit=SNAPSHOT_ITEMS)[0]
            last_run = state.last_run()
            snapshot = {
                "version": 1,
                "written_at": stamp(),
                "last_check": stamp(last_run) if last_run else None,
                "unread": state.inbox_unread(),
                "redacted": redacted,
                "items": [
                    {k: row["label"] if k == "display" else row[k] for k in _SNAPSHOT_FIELDS}
                    for row in rows
                ],
            }
        try:
            crypto.write_private(path, json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
        except crypto.EncryptionError as exc:
            raise ApiError("io_error", str(exc)) from exc
        return {"path": str(path), "redacted": redacted, "items": len(rows)}

    def op_config_init(self, p: _Params) -> dict[str, Any]:
        self_watch = p.get("self_watch", "bool", True)
        p.done()
        path = self.config_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(STARTER_CONFIG if self_watch else _EMPTY_CONFIG, encoding="utf-8")
            return {"config_path": str(path), "created": True, "self_watch_added": self_watch}
        added = False
        if self_watch:
            path, text = self._config_text()
            change = add_to_config(text, "git", [SELF_WATCH])
            if change.applied:
                self._write_config(path, change.text)
                added = True
        return {"config_path": str(path), "created": False, "self_watch_added": added}

    def op_shutdown(self, p: _Params) -> dict[str, Any]:
        p.done()
        return {}


def _ids(updates: list[Update]) -> list[str]:
    return [inbox_id(*u.dedupe_key) for u in updates]


def _alerts(
    updates: list[Update], priorities: dict[Key, str], labels: dict[Key, str]
) -> list[dict[str, Any]]:
    """High priority: one alert per target. Normal: one for the remaining"""
    by_target: dict[Key, list[Update]] = {}
    normal = []
    for update in updates:
        if notify.priority_of(update, priorities) == "high":
            by_target.setdefault((update.source, update.target), []).append(update)
        else:
            normal.append(update)
    alerts: list[dict[str, Any]] = [
        {
            "priority": "high",
            "target": {"source": source, "key": key, "display": labels.get((source, key), key)},
            "item_ids": _ids(found),
        }
        for (source, key), found in by_target.items()
    ]
    if normal:
        alerts.append({"priority": "normal", "target": None, "item_ids": _ids(normal)})
    return alerts


_HANDLERS: dict[str, Callable[[Engine, _Params], dict[str, Any]]] = {
    op: getattr(Engine, "op_" + op.replace(".", "_")) for op in OPS
}


def serve(engine: Engine, stdin: IO[bytes], stdout: IO[bytes]) -> int:
    """Answer requests from ``stdin`` until `shutdown`"""
    write_lock = threading.Lock()

    def write(message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with write_lock:
            stdout.write(line.encode("utf-8"))
            stdout.flush()

    engine.emit = write
    lines: queue.Queue[bytes | None] = queue.Queue()

    def read() -> None:
        try:
            for raw in stdin:
                lines.put(raw)
        finally:
            lines.put(None)

    # Requests start in the order they arrive
    threading.Thread(target=read, name="youpdated-api-stdin", daemon=True).start()
    check: threading.Thread | None = None
    while (raw := lines.get()) is not None:
        if not raw.strip():
            continue
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            write({"id": None, "ok": False, "error": _bad(f"not a JSON line: {exc}").to_dict()})
            continue
        rid = request.get("id") if isinstance(request, dict) else None
        if not isinstance(rid, int) or isinstance(rid, bool) or rid < 1:
            error = _bad("a request is an object with a positive integer `id`")
            write({"id": None, "ok": False, "error": error.to_dict()})
            continue
        if request.get("op") == "check":
            running = (check is not None and check.is_alive()) or engine.checking
            if running and not engine.fetching_icons:
                busy = ApiError("busy", "a check is already running").to_dict()
                write({"id": rid, "ok": False, "error": busy})
                continue

            def answer(request: dict[str, Any] = request, rid: int = rid) -> None:
                write({"id": rid, **engine.call(request)})
                engine.follow_up()

            check = threading.Thread(target=answer, name="youpdated-api-check", daemon=True)
            check.start()
            continue
        reply = engine.call(request)
        write({"id": rid, **reply})
        if request.get("op") == "shutdown" and reply["ok"]:
            break
    return 0


def main(args: argparse.Namespace) -> int:
    """`youpdated api serve`, or `youpdated api OP` once"""
    engine = Engine(args.config, args.state)
    if args.op == "serve":
        crypto.cache_keys()
        stdout = sys.stdout.buffer
        sys.stdout = sys.stderr
        code = serve(engine, sys.stdin.buffer, stdout)
        stdout.flush()
        sys.stderr.flush()
        os._exit(code)

    def show(reply: dict[str, Any]) -> int:
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if reply["ok"] else 1

    try:
        params = json.loads(args.params) if args.params else {}
    except ValueError as exc:
        return show({"ok": False, "error": _bad(f"--params: not JSON: {exc}").to_dict()})
    if not isinstance(params, dict):
        return show({"ok": False, "error": _bad("--params must be a JSON object").to_dict()})
    if args.op not in _OPEN_OPS and engine.locked():
        try:
            passphrase = crypto.prompt_passphrase()
        except crypto.EncryptionError as exc:
            return show({"ok": False, "error": ApiError("locked", str(exc)).to_dict()})
        unlocked = engine.call({"op": "unlock", "passphrase": passphrase})
        if not unlocked["ok"]:
            return show(unlocked)
    code = show(engine.call({**params, "op": args.op}))
    # check's icons, after reply
    engine.follow_up()
    return code
