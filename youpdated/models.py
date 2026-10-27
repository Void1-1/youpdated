"""Core data types shared by sources and renderer"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_UNSAFE_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def clean_text(text: Any) -> str:
    """remote text that's safe to print, write as XML, and encode as UTF-8"""
    if text is None:
        return ""
    return _UNSAFE_RE.sub("", text if isinstance(text, str) else str(text))


@dataclass
class Target:
    """Target watched, normalized from a config entry.

    ``key`` scopes dedupe and labels the item in reports
    """

    source: str
    key: str
    label: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    # Update tags this target does not want reported
    ignore: frozenset[str] = frozenset()

    @property
    def display(self) -> str:
        # sources fill the label from remote data
        return clean_text(self.label or self.key)

    def ignores(self, tags: tuple[str, ...]) -> bool:
        """
        If an update carrying ``tags`` should be dropped for this target
        only one matching tag needed
        """
        if not self.ignore:
            return False
        return any(tag.strip().lower() in self.ignore for tag in tags)


@dataclass(frozen=True)
class Update:
    source: str
    target: str
    uid: str
    title: str
    url: str
    published: datetime | None = None
    version: str | None = None
    body: str | None = None
    tags: tuple[str, ...] = ()
    # Older uids this item may already be recorded under; any of them counts as seen
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        clean = {
            "title": clean_text(self.title),
            "url": clean_text(self.url),
            "version": None if self.version is None else clean_text(self.version),
            "body": None if self.body is None else clean_text(self.body),
            "tags": tuple(clean_text(tag) for tag in self.tags),
            # only what SQLite can't store: a uid or alias with a control character may be recorded
            "uid": _SURROGATE_RE.sub("\ufffd", str(self.uid)),
            "aliases": tuple(_SURROGATE_RE.sub("\ufffd", str(alias)) for alias in self.aliases),
        }
        for name, value in clean.items():
            object.__setattr__(self, name, value)

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.uid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "uid": self.uid,
            "title": self.title,
            "url": self.url,
            "published": self.published.isoformat() if self.published else None,
            "version": self.version,
            "body": self.body,
            "tags": list(self.tags),
        }


@dataclass
class RunError:
    """A source failure, collected not raised"""

    source: str
    target: str
    message: str

    def __post_init__(self) -> None:
        self.target = clean_text(self.target)
        self.message = clean_text(self.message)
