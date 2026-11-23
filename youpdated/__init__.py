"""Youpdated, a simple update tracker.

The names in ``__all__`` are the library API, see docs/library.md.
They follow semantic versioning from v1.0.0. (other modules and names are internal and may change in any release)
"""

from __future__ import annotations

__version__ = "0.6.0"

from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "newer": "library",
    "NoVersionsError": "library",
    "parse_config": "library",
    "load_config": "library",
    "Config": "config",
    "PrivacyConfig": "config",
    "ConfigError": "config",
    "State": "state",
    "StateError": "state",
    "StateBusy": "state",
    "Client": "http",
    "FetchError": "http",
    "ProxyUnavailable": "http",
    "run": "runner",
    "record": "runner",
    "RunResult": "runner",
    "ProgressReporter": "runner",
    "Update": "models",
    "Target": "models",
    "RunError": "models",
    "Source": "sources.base",
    "ConfigEntryError": "sources.base",
    "register": "registry",
    "all_sources": "registry",
    "get_source": "registry",
    "EncryptionError": "crypto",
    "WrongPassphrase": "crypto",
}

__all__ = [
    "__version__",
    "newer", "NoVersionsError", "parse_config", "load_config",
    "Config", "PrivacyConfig", "ConfigError",
    "State", "StateError", "StateBusy",
    "Client", "FetchError", "ProxyUnavailable",
    "run", "record", "RunResult", "ProgressReporter",
    "Update", "Target", "RunError",
    "Source", "ConfigEntryError",
    "register", "all_sources", "get_source",
    "EncryptionError", "WrongPassphrase",
]


# Hidden from type checkers
if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module 'youpdated' has no attribute {name!r}")
        from importlib import import_module

        value = getattr(import_module(f".{module}", __name__), name)
        # Later lookups skip this function
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


if TYPE_CHECKING:
    from .config import Config, ConfigError, PrivacyConfig
    from .crypto import EncryptionError, WrongPassphrase
    from .http import Client, FetchError, ProxyUnavailable
    from .library import NoVersionsError, load_config, newer, parse_config
    from .models import RunError, Target, Update
    from .registry import all_sources, get_source, register
    from .runner import ProgressReporter, RunResult, record, run
    from .sources.base import ConfigEntryError, Source
    from .state import State, StateBusy, StateError
