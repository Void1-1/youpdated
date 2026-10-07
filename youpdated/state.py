"""Local SQLite state: what's reported, HTTP validators, and resolved-name caches"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import crypto
from .models import Update

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


class State:
    """Thread-safe wrapper over SQLite file."""

    def __init__(
        self,
        path: str | Path | None = None,
        passphrase: str | None = None,
        *,
        expiry: timedelta | None = None,
    ):
        self.path = Path(path) if path is not None else None
        self.passphrase = passphrase
        #: Seen items absent from every fetch this long are pruned. ``None`` keeps
        self.expiry = expiry
        self.encrypted = passphrase is not None and self.path is not None
        self._closed = False
        self._dirty = False
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

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
                    raise crypto.EncryptionError(
                        f"{self.path}: decrypted, but the contents are not a database: {exc}"
                    ) from exc
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    # only applicable to minor version differences, WILL NOT HANDLE MAJOR ONES HERE, will also only exist for a limited amount of versions after change
    def _migrate(self) -> None:
        """Bring a database written by an older version up to the current schema"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(seen)")}
        if "last_seen" not in columns:
            self._conn.execute("ALTER TABLE seen ADD COLUMN last_seen TEXT")
            self._conn.execute("UPDATE seen SET last_seen = first_seen")
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
        self.flush()
        self._closed = True
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # already seen items

    def is_new(self, update: Update) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM seen WHERE source=? AND target=? AND uid=?",
                update.dedupe_key,
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

    def prune(
        self,
        keep_targets: Iterable[tuple[str, str]] = (),
        keep_sources: Iterable[str] = (),
    ) -> int:
        """Forget seen items and validators not refreshed within the expiry"""
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
            self._conn.commit()
            if removed or removed_validators:
                # reclaim empty so the file shrinks
                self._conn.execute("VACUUM")
                self._dirty = True
        return removed

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
