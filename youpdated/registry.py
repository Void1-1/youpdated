"""Source registry

Built-in sources register with the decorator; third-parties can ship a ``youpdated.sources`` entry point and appear without touching this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sources.base import Source

_SOURCES: dict[str, "Source"] = {}
#: Old or alternate config names -> the source that now handles
_ALIASES: dict[str, str] = {}
_ENTRY_POINTS_LOADED = False


def register(cls):
    """Class decorator: instantiate and register a source by ``name`` and any ``aliases``"""
    instance = cls()
    _SOURCES[cls.name] = instance
    for alias in getattr(cls, "aliases", ()):
        _ALIASES[alias] = cls.name
    return cls


def _load_entry_points() -> None:
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    from importlib.metadata import entry_points

    for ep in entry_points(group="youpdated.sources"):
        try:
            obj = ep.load()
        except Exception:  # a broken plugin can't stop all
            continue
        instance = obj() if isinstance(obj, type) else obj
        name = getattr(instance, "name", ep.name)
        _SOURCES.setdefault(name, instance)


def all_sources() -> dict[str, "Source"]:
    from . import sources  # noqa: F401  (imports register built-ins)

    _load_entry_points()
    return dict(_SOURCES)


def get_source(name: str) -> "Source | None":
    return all_sources().get(canonical_name(name))


def canonical_name(name: str) -> str:
    """The source a config or ``--source`` name refers to: itself, or what it is an alias of"""
    sources = all_sources()
    return name if name in sources else _ALIASES.get(name, name)


def all_aliases() -> dict[str, str]:
    all_sources()
    return dict(_ALIASES)
