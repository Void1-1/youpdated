"""Local SQLite state: what's reported, HTTP validators, and resolved-name caches"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
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


class State:
    """Thread-safe wrapper over SQLite file."""

    def __init__(self, path: str | Path | None = None, passphrase: str | None = None):
        self.path = Path(path) if path is not None else None
        self.passphrase = passphrase
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
            self._conn.commit()

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
        rows = [(*u.dedupe_key, _now()) for u in updates]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO seen (source, target, uid, first_seen) VALUES (?,?,?,?)",
                rows,
            )
            self._conn.commit()
            self._dirty = True

    def seen_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    # conditional get validators

    def conditional_headers(self, url: str) -> dict[str, str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT etag, last_modified FROM http_cache WHERE url=?", (url,)
            ).fetchone()
        if row is None:
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
