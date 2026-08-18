"""Source plugin contract"""

from __future__ import annotations

from typing import Any, ClassVar, Iterable, Protocol, runtime_checkable

from ..http import Client
from ..models import Target, Update


class ConfigEntryError(Exception):
    """Unknown entry or formats"""


@runtime_checkable
class Source(Protocol):
    name: ClassVar[str]
    # shown by `youpdated sources`
    summary: ClassVar[str]

    def targets(self, entries: list[Any]) -> list[Target]:
        """Normalize raw config into targets"""

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        """Fetch current items for one target. May raise errs; collected."""


def entry_fields(entry: Any, scalar_key: str, source: str) -> dict[str, Any]:
    """Accept both config shapes: a scalar or mapping.

    ``- python/cpython`` and ``- {repo: python/cpython, watch: [...]}`` both arrive and leave as dict
    """
    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, (str, int, float)):
        return {scalar_key: entry}
    raise ConfigEntryError(f"sources.{source}: entry must be a value or a mapping, got {entry!r}")


def require(fields: dict[str, Any], key: str, source: str) -> Any:
    value = fields.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ConfigEntryError(f"sources.{source}: entry is missing required `{key}`")
    return value
