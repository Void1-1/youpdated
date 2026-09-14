"""Core data types shared by sources and renderer"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Target:
    """Target watched, normalized from a config entry.

    ``key`` scopes dedupe and labels the item in reports
    """

    source: str
    key: str
    label: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    #: Update tags this target does not want reported
    ignore: frozenset[str] = frozenset()

    @property
    def display(self) -> str:
        return self.label or self.key

    def ignores(self, tags: tuple[str, ...]) -> bool:
        """
        Whether an update carrying ``tags`` should be dropped for this target
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
