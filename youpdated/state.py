"""Local SQLite state: what's reported, HTTP validators, and resolved-name caches"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterable

from . import crypto
from .config import PRIORITIES
from .models import Update

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# How long to wait for another run to release state
LOCK_TIMEOUT = 60.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    uid        TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT,
    PRIMARY KEY (source, target, uid)
);

CREATE TABLE IF NOT EXISTS http_cache (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS http_cache_targets (
    url    TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (url, source, target)
);

CREATE TABLE IF NOT EXISTS kv (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE TABLE IF NOT EXISTS inbox (
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    uid         TEXT NOT NULL,
    id          TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    version     TEXT,
    published   TEXT,
    tags        TEXT NOT NULL,
    body        TEXT,
    found_at    TEXT NOT NULL,
    read_at     TEXT,
    notified_at TEXT,
    baseline    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, target, uid)
);

CREATE TABLE IF NOT EXISTS icons (
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    mime       TEXT,
    data       BLOB,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, target)
);
"""

INBOX_BODY_LIMIT = 1000
_INBOX_SORTS = {
    "newest": "",
    "priority": "CASE p.value WHEN 'high' THEN 0 WHEN 'low' THEN 2 ELSE 1 END, ",
    "source": "i.source, ",
    "name": "i.label COLLATE NOCASE, ",
}


def stamp(when: datetime | None = None) -> str:
    """``when`` (default now) in UTC to seconds, as inbox and api write times"""
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inbox_id(source: str, target: str, uid: str) -> str:
    """Stable, opaque id of an inbox item"""
    digest = hashlib.sha256(f"{source}\0{target}\0{uid}".encode("utf-8", "surrogatepass"))
    return "i_" + digest.hexdigest()[:16]


def _trim(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(age: timedelta) -> str:
    return (datetime.now(timezone.utc) - age).isoformat()


def _rules_value(rules: Iterable[frozenset[str]]) -> str:
    return "|".join(sorted({",".join(sorted(ignore)) for ignore in rules}))


class StateError(Exception):
    """raised with message for the user"""


class StateBusy(StateError):
    """Another run held the history past the lock timeout"""


def lock_path(path: Path) -> Path:
    """The file :class:`State` locks to have ``path`` to itself"""
    return path.with_name(path.name + ".lock")


class _FileLock:
    """exclusive lock on a file, between processes"""

    def __init__(self, path: Path, timeout: float):
        self.path = path
        self.timeout = timeout
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _try_lock(fd)
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise StateBusy(
                        f"{self.path.with_suffix('')} is in use by another youpdated run; "
                        "try again once it finishes"
                    ) from None
                time.sleep(0.1)
                continue
            self._fd = fd
            return

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


@contextmanager
def state_lock(path: Path, timeout: float = LOCK_TIMEOUT) -> Generator[None, None, None]:
    """Hold the lock :class:`State` takes, to change the file at ``path`` without opening"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _FileLock(lock_path(path), timeout)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


class State:
    """Thread-safe wrapper over SQLite file"""

    def __init__(
        self,
        path: str | Path | None = None,
        passphrase: str | None = None,
        *,
        expiry: timedelta | None = None,
        lock_timeout: float = LOCK_TIMEOUT,
    ):
        self.path = Path(path) if path is not None else None
        self.passphrase = passphrase
        # Seen items absent from every fetch this long are pruned. ``None`` keeps
        self.expiry = expiry
        self.encrypted = passphrase is not None and self.path is not None
        self._closed = False
        self._dirty = False
        # Salt the file was read or first written with
        self._salt: bytes | None = None
        self._file_lock: _FileLock | None = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock = _FileLock(lock_path(self.path), lock_timeout)
            lock.acquire()
            self._file_lock = lock
        try:
            self._open()
        except BaseException:
            if self._file_lock is not None:
                self._file_lock.release()
            raise

    def _open(self) -> None:
        restore = self._read_encrypted() if self.encrypted else None
        target = ":memory:" if (self.path is None or self.encrypted) else str(self.path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if restore is not None:
                try:
                    self._conn.deserialize(restore)
                except sqlite3.Error as exc:
                    self._conn.close()
                    raise crypto.EncryptionError(
                        f"{self.path}: decrypted, but the contents are not a database: {exc}"
                    ) from exc
            try:
                self._conn.executescript(_SCHEMA)
                self._migrate()
                self._conn.commit()
            except sqlite3.DatabaseError as exc:
                self._conn.close()
                raise StateError(f"{self.path}: not a youpdated history database ({exc})") from exc

    # only applicable to minor version differences, WILL NOT HANDLE MAJOR ONES HERE, will also only exist for a limited amount of versions after change
    def _migrate(self) -> None:
        """Bring a database written by an older version up to the current schema"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(seen)")}
        if "last_seen" not in columns:
            self._conn.execute("ALTER TABLE seen ADD COLUMN last_seen TEXT")
            self._conn.execute("UPDATE seen SET last_seen = first_seen")
            self._dirty = True
        # <= 0.3.0 stored YouTube Shorts from the official feed as yt:video:yt:video:<id>
        fixed = self._conn.execute(
            "UPDATE OR IGNORE seen SET uid = substr(uid, 10) "
            "WHERE source = 'youtube' AND uid LIKE 'yt:video:yt:video:%'"
        ).rowcount
        # Rows left over already had the correct id recorded by a fallback path
        fixed += self._conn.execute(
            "DELETE FROM seen WHERE source = 'youtube' AND uid LIKE 'yt:video:yt:video:%'"
        ).rowcount
        if fixed:
            self._dirty = True
        self._migrate_github_to_git()

    def _migrate_github_to_git(self) -> None:
        """<= 0.3.1 had a `github` source keyed by owner/repo; it is now `git`, keyed by host"""
        moved = self._conn.execute(
            "UPDATE OR IGNORE seen SET source = 'git', target = 'github.com/' || target "
            "WHERE source = 'github'"
        ).rowcount
        # Leftovers were already recorded under `git` too
        moved += self._conn.execute("DELETE FROM seen WHERE source = 'github'").rowcount
        # Baselines and ignore rules are kept as `source:target`
        moved += self._conn.execute(
            "UPDATE OR IGNORE kv SET key = 'git:github.com/' || substr(key, 8) "
            "WHERE namespace IN ('baseline', 'ignore') AND key LIKE 'github:%'"
        ).rowcount
        moved += self._conn.execute(
            "DELETE FROM kv WHERE namespace IN ('baseline', 'ignore') AND key LIKE 'github:%'"
        ).rowcount
        if moved:
            self._dirty = True

    def _read_encrypted(self) -> bytes | None:
        """Decrypt the state file into a database image, or None if there is no file yet."""
        assert self.path is not None and self.passphrase is not None
        if not hasattr(sqlite3.Connection, "deserialize"):
            raise crypto.EncryptionError(
                "this Python is built against a SQLite too old for in-memory databases "
                "(needs 3.36+). The state database cannot be encrypted."
            )
        if not self.path.exists():
            return None
        blob = self.path.read_bytes()
        if not crypto.is_encrypted(blob):
            raise crypto.EncryptionError(
                f"{self.path} is not encrypted, but a passphrase was given. "
                "Run `youpdated encrypt` to convert it."
            )
        payload = crypto.decrypt(blob, self.passphrase)
        self._salt = crypto.salt_of(blob)
        return payload

    def flush(self) -> None:
        """Write the in-memory database back out, encrypted."""
        if not self.encrypted or self._closed or not self._dirty:
            return
        assert self.path is not None and self.passphrase is not None
        with self._lock:
            image = self._conn.serialize()
        blob = crypto.encrypt(image, self.passphrase, salt=self._salt)
        crypto.write_private(self.path, blob)
        self._salt = crypto.salt_of(blob)
        self._dirty = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
        finally:
            self._closed = True
            with self._lock:
                self._conn.close()
            if self._file_lock is not None:
                self._file_lock.release()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # already seen items

    def is_new(self, update: Update) -> bool:
        uids = (update.uid, *update.aliases)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen WHERE source=? AND target=? AND uid IN "
                f"({','.join('?' * len(uids))})",
                (update.source, update.target, *uids),
            ).fetchone()
        return row is None

    def filter_new(self, updates: Iterable[Update]) -> list[Update]:
        return [u for u in updates if self.is_new(u)]

    def mark_seen(self, updates: Iterable[Update]) -> None:
        """Record ``updates``, and refresh ``last_seen`` on any recorded"""
        now = _now()
        rows = [(*u.dedupe_key, now, now) for u in updates]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO seen (source, target, uid, first_seen, last_seen) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(source, target, uid) DO UPDATE SET last_seen=excluded.last_seen",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    def seen_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    # conditional get validators

    def conditional_headers(self, url: str) -> dict[str, str]:
        """Validators for ``url``

        A 304 returns no items, so no ``last_seen`` refreshed. 
        Validators older than half the expiry are dropped to force a full fetch.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT etag, last_modified, fetched_at FROM http_cache WHERE url=?", (url,)
            ).fetchone()
        if row is None:
            return {}
        if self.expiry is not None and row["fetched_at"] < _ago(self.expiry / 2):
            return {}
        headers = {}
        if row["etag"]:
            headers["If-None-Match"] = row["etag"]
        if row["last_modified"]:
            headers["If-Modified-Since"] = row["last_modified"]
        return headers

    def remember_validators(self, url: str, etag: str | None, last_modified: str | None) -> None:
        if not etag and not last_modified:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO http_cache (url, etag, last_modified, fetched_at) VALUES (?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, "
                "last_modified=excluded.last_modified, fetched_at=excluded.fetched_at",
                (url, etag, last_modified, _now()),
            )
            self._conn.commit()
            self._dirty = True

    def link_urls(self, rows: Iterable[tuple[str, str, str]]) -> None:
        rows = list(rows)
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO http_cache_targets (source, target, url) VALUES (?,?,?)",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    def prune(
        self,
        keep_targets: Iterable[tuple[str, str]] = (),
        keep_sources: Iterable[str] = (),
    ) -> int:
        """Forget seen items, validators and baseline marks not refreshed within the expiry"""
        if self.expiry is None:
            return 0
        cutoff = _ago(self.expiry)
        keep = set(keep_targets)
        keep_whole = set(keep_sources)
        with self._lock:
            stale = [
                (row["source"], row["target"])
                for row in self._conn.execute(
                    "SELECT DISTINCT source, target FROM seen "
                    "WHERE COALESCE(last_seen, first_seen) < ?",
                    (cutoff,),
                )
            ]
            removed = 0
            for source, target in stale:
                if source in keep_whole or (source, target) in keep:
                    continue
                removed += self._conn.execute(
                    "DELETE FROM seen WHERE source=? AND target=? "
                    "AND COALESCE(last_seen, first_seen) < ?",
                    (source, target, cutoff),
                ).rowcount
            removed_validators = self._conn.execute(
                "DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,)
            ).rowcount
            stale_marks = [
                row["key"]
                for row in self._conn.execute(
                    "SELECT key FROM kv WHERE namespace='baseline' AND value < ?",
                    (cutoff,),
                )
                if row["key"].split(":", 1)[0] not in keep_whole
                and tuple(row["key"].split(":", 1)) not in keep
            ]
            self._conn.executemany(
                "DELETE FROM kv WHERE namespace='baseline' AND key=?",
                [(key,) for key in stale_marks],
            )
            # Rules and highest versions are recorded with the baseline mark, and are removed with it
            removed_rules = self._conn.execute(
                "DELETE FROM kv WHERE namespace IN ('ignore', 'version') AND key NOT IN "
                "(SELECT key FROM kv WHERE namespace='baseline')"
            ).rowcount
            # URL links, and a link to a URL without a validator
            removed_links = self._conn.execute(
                "DELETE FROM http_cache_targets WHERE source || ':' || target NOT IN "
                "(SELECT key FROM kv WHERE namespace='baseline') "
                "OR url NOT IN (SELECT url FROM http_cache)"
            ).rowcount
            # Unread items stay until read, or target is removed
            removed_inbox = self._conn.execute(
                "DELETE FROM inbox WHERE read_at IS NOT NULL AND found_at < ?",
                (stamp(datetime.now(timezone.utc) - self.expiry),),
            ).rowcount
            removed_inbox += self._drop_orphans()
            removed_icons = self._conn.execute(
                "DELETE FROM icons WHERE source || ':' || target NOT IN "
                "(SELECT key FROM kv WHERE namespace='baseline')"
            ).rowcount
            self._conn.commit()
            if (
                removed or removed_validators or stale_marks or removed_rules or removed_links
                or removed_inbox or removed_icons
            ):
                # reclaim empty so the file shrinks
                self._conn.execute("VACUUM")
                self._dirty = True
        return removed

    def forget(
        self,
        source: str,
        targets: Iterable[str],
        caches: Iterable[str] = (),
        urls: Iterable[str] = (),
        shared: Iterable[str] = (),
    ) -> tuple[int, int]:
        """Delete everything recorded about ``targets``: seen items, baseline marks, ignore
        rules, highest versions, entries keyed by the target in the ``caches`` namespaces,
        ``shared`` namespaces, and validators of every URL they asked for.
        """
        caches = list(caches)
        targets = list(targets)
        self.forget_inbox(source, targets)
        seen = 0
        candidates = set(urls)
        with self._lock:
            self._conn.executemany(
                "DELETE FROM kv WHERE namespace=?", [(ns,) for ns in shared]
            )
            for target in targets:
                seen += self._conn.execute(
                    "DELETE FROM seen WHERE source=? AND target=?", (source, target)
                ).rowcount
                self._conn.executemany(
                    "DELETE FROM kv WHERE namespace=? AND key=?",
                    [(ns, f"{source}:{target}") for ns in ("baseline", "ignore", "version")]
                    + [(ns, target) for ns in caches],
                )
                candidates.update(
                    row["url"]
                    for row in self._conn.execute(
                        "SELECT url FROM http_cache_targets WHERE source=? AND target=?",
                        (source, target),
                    )
                )
                self._conn.execute(
                    "DELETE FROM http_cache_targets WHERE source=? AND target=?", (source, target)
                )
            validators = 0
            for url in candidates:
                validators += self._conn.execute(
                    "DELETE FROM http_cache WHERE url=? AND url NOT IN "
                    "(SELECT url FROM http_cache_targets)",
                    (url,),
                ).rowcount
            self._conn.commit()
            # deleted rows are readable in free pages until the file is rebuilt
            self._conn.execute("VACUUM")
            self._dirty = True
        return seen, validators

    # small caches (resolved names, channel ids)

    def cache_get(self, namespace: str, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
        return row["value"] if row else None

    def cache_set(self, namespace: str, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (namespace, key, value) VALUES (?,?,?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                (namespace, key, value),
            )
            self._conn.commit()
            self._dirty = True

    # per target baselines

    def is_baselined(self, source: str, target: str) -> bool:
        """Whether ``target`` has had a successful fetch recorded"""
        if self.cache_get("baseline", f"{source}:{target}"):
            return True
        # Targets checked before baselines were tracked per target
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen WHERE source=? AND target=? LIMIT 1", (source, target)
            ).fetchone()
        return row is not None

    def mark_baselined(self, targets: Iterable[tuple[str, str]]) -> None:
        """Record a successful fetch; the stamp is the latest one, so ``prune`` can expire it"""
        stamp = _now()
        rows = [("baseline", f"{source}:{target}", stamp) for source, target in targets]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO kv (namespace, key, value) VALUES (?,?,?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    # per target ignore rules

    def ignore_rules_changed(
        self, source: str, target: str, rules: Iterable[frozenset[str]]
    ) -> bool:
        """If ``rules`` differ from the ones the target's last saved fetch ran under"""
        return self.cache_get("ignore", f"{source}:{target}") != _rules_value(rules)

    def mark_ignore_rules(
        self, targets: Iterable[tuple[str, str, Iterable[frozenset[str]]]]
    ) -> None:
        rows = [
            ("ignore", f"{source}:{target}", _rules_value(rules))
            for source, target, rules in targets
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO kv (namespace, key, value) VALUES (?,?,?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    # targets highest releases, for `versions: {bump: ...}`, see versions.Highest

    def highest_version(self, source: str, target: str) -> str | None:
        return self.cache_get("version", f"{source}:{target}")

    def mark_highest_versions(self, targets: Iterable[tuple[str, str, str]]) -> None:
        rows = [("version", f"{source}:{target}", value) for source, target, value in targets]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO kv (namespace, key, value) VALUES (?,?,?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    # upkeeping

    def last_run(self) -> datetime | None:
        raw = self.cache_get("meta", "last_run")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def set_last_run(self, when: datetime | None = None, by: str | None = None) -> None:
        """``by`` is ``app`` or ``cli``"""
        self.cache_set("meta", "last_run", (when or datetime.now(timezone.utc)).isoformat())
        if by is not None:
            self.cache_set("app", "last_run_by", by)

    def cache_delete(self, namespace: str, keys: Iterable[str]) -> None:
        rows = [(namespace, key) for key in keys]
        if not rows:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM kv WHERE namespace=? AND key=?", rows)
            self._conn.commit()
            self._dirty = True

    def cache_items(self, namespace: str, prefix: str = "") -> dict[str, str]:
        """``namespace``'s entries whose key starts with ``prefix``"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM kv WHERE namespace=? AND substr(key, 1, ?) = ?",
                (namespace, len(prefix), prefix),
            ).fetchall()
        return {row["key"][len(prefix):]: row["value"] for row in rows}

    # each target's newest item, kept apart from the inbox, which is pruned

    def mark_latest(self, updates: Iterable[Update]) -> None:
        rows = [
            (
                "app",
                f"latest:{u.source}:{u.target}",
                json.dumps({
                    "version": u.version,
                    "title": u.title,
                    "published": stamp(u.published) if u.published else None,
                }),
            )
            for u in updates
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO kv (namespace, key, value) VALUES (?,?,?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    def latest(self) -> dict[tuple[str, str], dict[str, Any]]:
        """``{version, title, published}`` of each target's newest item, by ``(source, key)``"""
        found = {}
        for name, value in self.cache_items("app", "latest:").items():
            source, _, key = name.partition(":")
            try:
                found[(source, key)] = json.loads(value)
            except ValueError:
                continue
        return found

    # priorities

    def priorities(self) -> dict[tuple[str, str], str]:
        """Every target not at ``normal``, by ``(source, key)``"""
        found = {}
        for name, value in self.cache_items("app", "priority:").items():
            source, _, key = name.partition(":")
            if value in PRIORITIES:
                found[(source, key)] = value
        return found

    def set_priority(self, source: str, key: str, priority: str) -> None:
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
        if priority == "normal":
            self.cache_delete("app", [f"priority:{source}:{key}"])
        else:
            self.cache_set("app", f"priority:{source}:{key}", priority)

    # inbox

    def inbox_add(
        self,
        updates: Iterable[Update],
        labels: dict[tuple[str, str], str],
        *,
        baseline: bool = False,
    ) -> list[str]:
        """Store ``updates``, already read when they're a new target's ``baseline``"""
        now = stamp()
        added = []
        with self._lock:
            for u in updates:
                item_id = inbox_id(*u.dedupe_key)
                inserted = self._conn.execute(
                    "INSERT OR IGNORE INTO inbox (source, target, uid, id, label, title, url, "
                    "version, published, tags, body, found_at, read_at, notified_at, baseline) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *u.dedupe_key,
                        item_id,
                        labels.get((u.source, u.target), u.target),
                        u.title,
                        u.url,
                        u.version,
                        stamp(u.published) if u.published else None,
                        json.dumps(list(u.tags)),
                        _trim(u.body, INBOX_BODY_LIMIT),
                        now,
                        now if baseline else None,
                        now,
                        int(baseline),
                    ),
                ).rowcount
                if inserted:
                    added.append(item_id)
            self._conn.commit()
            if added:
                self._dirty = True
        return added

    def inbox_get(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        """Items by id"""
        ids = list(ids)
        if not ids:
            return []
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM inbox WHERE id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def inbox_query(
        self,
        *,
        unread: bool = False,
        source: str | None = None,
        target: tuple[str, str] | None = None,
        sort: str = "newest",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """A page of items, and how many match"""
        where, args = ["1=1"], []
        if unread:
            where.append("i.read_at IS NULL")
        if source is not None:
            where.append("i.source = ?")
            args.append(source)
        if target is not None:
            where.append("i.source = ? AND i.target = ?")
            args += list(target)
        joined = (
            "FROM inbox i LEFT JOIN kv p ON p.namespace = 'app' "
            "AND p.key = 'priority:' || i.source || ':' || i.target "
            f"WHERE {' AND '.join(where)}"
        )
        # newest breaks ties
        order = _INBOX_SORTS[sort] + "COALESCE(i.published, i.found_at) DESC, i.found_at DESC, i.id"
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) {joined}", args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT i.* {joined} ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
        return [dict(row) for row in rows], total

    def inbox_unread(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM inbox WHERE read_at IS NULL"
            ).fetchone()[0]

    def inbox_unread_by_target(self) -> dict[tuple[str, str], int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, target, COUNT(*) AS n FROM inbox WHERE read_at IS NULL "
                "GROUP BY source, target"
            ).fetchall()
        return {(row["source"], row["target"]): row["n"] for row in rows}

    def inbox_mark(
        self, read: bool, *, ids: Iterable[str] | None = None, source: str | None = None
    ) -> int:
        """Mark ``ids``, or items (of ``source``), read or unread"""
        where = ["read_at IS NULL" if read else "read_at IS NOT NULL"]
        args: list[Any] = []
        if ids is not None:
            ids = list(ids)
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args += ids
        if source is not None:
            where.append("source = ?")
            args.append(source)
        now = stamp()
        with self._lock:
            changed = [
                row["id"]
                for row in self._conn.execute(
                    f"SELECT id FROM inbox WHERE {' AND '.join(where)}", args
                )
            ]
            self._conn.executemany(
                "UPDATE inbox SET read_at=? WHERE id=?",
                [(now if read else None, i) for i in changed],
            )
            if read:
                self._conn.executemany(
                    "DELETE FROM kv WHERE namespace='app' AND key=?",
                    [(f"snooze:{i}",) for i in changed],
                )
            self._conn.commit()
            if changed:
                self._dirty = True
        return len(changed)

    def inbox_held(self) -> list[dict[str, Any]]:
        """Items held for the digest, oldest first"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM inbox WHERE notified_at IS NULL ORDER BY found_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def inbox_set_notified(self, ids: Iterable[str], notified: bool = True) -> None:
        """Mark ``ids`` sent, or hold for digest with ``notified=False``"""
        now = stamp()
        rows = [(now if notified else None, i) for i in ids]
        if not rows:
            return
        with self._lock:
            self._conn.executemany("UPDATE inbox SET notified_at=? WHERE id=?", rows)
            self._conn.commit()
            self._dirty = True

    def inbox_targets(self) -> set[tuple[str, str]]:
        """Targets with unread items"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT source, target FROM inbox WHERE read_at IS NULL"
            ).fetchall()
        return {(row["source"], row["target"]) for row in rows}

    def inbox_drop_unread(self, targets: Iterable[tuple[str, str]]) -> int:
        """Forget unread items of targets no longer watched"""
        with self._lock:
            removed = 0
            for source, target in targets:
                removed += self._conn.execute(
                    "DELETE FROM inbox WHERE source=? AND target=? AND read_at IS NULL",
                    (source, target),
                ).rowcount
            self._drop_orphans()
            self._conn.commit()
            if removed:
                self._dirty = True
        return removed

    def forget_inbox(self, source: str, targets: Iterable[str]) -> int:
        """Delete what the app keeps on ``targets``: inbox items, snoozes, priorities,
        names, latest items, and icons
        """
        targets = list(targets)
        removed = 0
        with self._lock:
            for target in targets:
                removed += self._conn.execute(
                    "DELETE FROM inbox WHERE source=? AND target=?", (source, target)
                ).rowcount
                self._conn.execute(
                    "DELETE FROM icons WHERE source=? AND target=?", (source, target)
                )
            self._conn.executemany(
                "DELETE FROM kv WHERE namespace='app' AND key=?",
                [
                    (f"{kind}:{source}:{t}",)
                    for t in targets
                    for kind in ("priority", "label", "latest")
                ],
            )
            self._drop_orphans()
            self._conn.commit()
            self._dirty = True
        return removed

    # icons

    def icon(self, source: str, target: str) -> tuple[str, bytes] | None:
        """``(mime, data)``, or None"""
        with self._lock:
            row = self._conn.execute(
                "SELECT mime, data FROM icons WHERE source=? AND target=? AND data IS NOT NULL",
                (source, target),
            ).fetchone()
        return (row["mime"], bytes(row["data"])) if row else None

    def icons_due(
        self, targets: Iterable[tuple[str, str]], refresh: timedelta, retry: timedelta
    ) -> list[tuple[str, str]]:
        """Which of ``targets`` have no icons, one older than ``refresh``, or found none
        more than ``retry`` ago
        """
        with self._lock:
            kept = {
                (row["source"], row["target"]): (row["data"] is not None, row["fetched_at"])
                for row in self._conn.execute(
                    "SELECT source, target, data, fetched_at FROM icons"
                )
            }
        now = datetime.now(timezone.utc)
        refreshed, retried = stamp(now - refresh), stamp(now - retry)
        due = []
        for key in targets:
            found = kept.get(key)
            if found is None or found[1] < (refreshed if found[0] else retried):
                due.append(key)
        return due

    def set_icon(self, source: str, target: str, found: tuple[str, bytes] | None) -> None:
        """Keep ``(mime, data)``"""
        mime, data = found if found is not None else (None, None)
        with self._lock:
            self._conn.execute(
                "INSERT INTO icons (source, target, mime, data, fetched_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(source, target) DO UPDATE SET mime=excluded.mime, "
                "data=excluded.data, fetched_at=excluded.fetched_at",
                (source, target, mime, data, stamp()),
            )
            self._conn.commit()
            self._dirty = True

    def clear_icons(self) -> int:
        with self._lock:
            removed = self._conn.execute("DELETE FROM icons").rowcount
            self._conn.commit()
            if removed:
                # images are readable in free pages until the file is rebuilt
                self._conn.execute("VACUUM")
                self._dirty = True
        return removed

    def _drop_orphans(self) -> int:
        """Snoozes of items no longer in the inbox"""
        return self._conn.execute(
            "DELETE FROM kv WHERE namespace='app' AND key LIKE 'snooze:%' "
            "AND substr(key, 8) NOT IN (SELECT id FROM inbox)"
        ).rowcount
