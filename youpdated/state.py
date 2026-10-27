"""Local SQLite state: what's reported, HTTP validators, and resolved-name caches"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Iterable

from . import crypto
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(age: timedelta) -> str:
    return (datetime.now(timezone.utc) - age).isoformat()


def _rules_value(rules: Iterable[frozenset[str]]) -> str:
    return "|".join(sorted({",".join(sorted(ignore)) for ignore in rules}))


class StateError(Exception):
    """raised with message for the user"""


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
                    raise StateError(
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
        return crypto.decrypt(blob, self.passphrase)

    def flush(self) -> None:
        """Write the in-memory database back out, encrypted."""
        if not self.encrypted or self._closed or not self._dirty:
            return
        assert self.path is not None and self.passphrase is not None
        with self._lock:
            image = self._conn.serialize()
        crypto.write_private(self.path, crypto.encrypt(image, self.passphrase))
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
            # Rules are recorded with the baseline mark, and go with it
            removed_rules = self._conn.execute(
                "DELETE FROM kv WHERE namespace='ignore' AND key NOT IN "
                "(SELECT key FROM kv WHERE namespace='baseline')"
            ).rowcount
            # URL links, and a link to a URL without a validator
            removed_links = self._conn.execute(
                "DELETE FROM http_cache_targets WHERE source || ':' || target NOT IN "
                "(SELECT key FROM kv WHERE namespace='baseline') "
                "OR url NOT IN (SELECT url FROM http_cache)"
            ).rowcount
            self._conn.commit()
            if removed or removed_validators or stale_marks or removed_rules or removed_links:
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
        rules, entries keyed by the target in the ``caches`` namespaces, ``shared`` namespaces,
        and validators of every URL they asked for.
        """
        caches = list(caches)
        seen = 0
        candidates = set(urls)
        with self._lock:
            self._conn.executemany(
                "DELETE FROM kv WHERE namespace=?", [(ns,) for ns in shared]
            )
            for target in list(targets):
                seen += self._conn.execute(
                    "DELETE FROM seen WHERE source=? AND target=?", (source, target)
                ).rowcount
                self._conn.executemany(
                    "DELETE FROM kv WHERE namespace=? AND key=?",
                    [(ns, f"{source}:{target}") for ns in ("baseline", "ignore")]
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

    # upkeeping

    def last_run(self) -> datetime | None:
        raw = self.cache_get("meta", "last_run")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def set_last_run(self, when: datetime | None = None) -> None:
        stamp = (when or datetime.now(timezone.utc)).isoformat()
        self.cache_set("meta", "last_run", stamp)
