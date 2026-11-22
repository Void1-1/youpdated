"""Named configs: which config and history a command uses"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEFAULT_NAME,
    ConfigError,
    configs_dir,
    default_state_path,
    named_config_path,
    named_state_path,
)

CONFIG_ENV = "YOUPDATED_CONFIG"

MAX_NAME = 32
# Lowercase only
NAME_RE = re.compile(rf"^[a-z0-9][a-z0-9_-]{{0,{MAX_NAME - 1}}}$")
# What `-c` takes as a name rather than a path
_NAME_LIKE = re.compile(r"^[^/\\.]+$")


@dataclass(frozen=True)
class Selected:
    """The config and history a command uses"""

    # None for a config given by path, or the default found by the normal lookup
    config: Path | None
    state: Path
    # "default", a config's name, or None for a path
    name: str | None


def looks_like_name(value: str) -> bool:
    return bool(_NAME_LIKE.match(value))


def check_name(value: str) -> str:
    """``value`` as a config name, lowercase"""
    name = value.strip().lower()
    if not NAME_RE.match(name):
        raise ConfigError(
            f"`{value}` is not a config name: use up to {MAX_NAME} letters, digits, `-`, "
            "or `_`, starting with a letter or digit"
        )
    return name


def named() -> list[str]:
    """Every named config, without the default"""
    folder = configs_dir()
    if not folder.is_dir():
        return []
    return sorted(
        path.stem for path in folder.glob("*.yaml")
        if path.is_file() and path.stem != DEFAULT_NAME and NAME_RE.match(path.stem)
    )


def chosen() -> str | None:
    """$YOUPDATED_CONFIG, when set"""
    return os.environ.get(CONFIG_ENV) or None


def select(value: str | None, state: str | Path | None = None, *, create: bool = False) -> Selected:
    """The config and history for ``-c VALUE`` (or $YOUPDATED_CONFIG) and ``--state``"""
    if value is None:
        value = chosen()
    state_path = Path(state).expanduser() if state else None

    if value is None:
        return Selected(None, state_path or default_state_path(), DEFAULT_NAME)

    if looks_like_name(value):
        literal = Path(value).expanduser()
        try:
            name = check_name(value)
        except ConfigError:
            if not create and literal.is_file():
                return Selected(literal, state_path or default_state_path(), None)
            raise
        if name == DEFAULT_NAME:
            return Selected(None, state_path or default_state_path(), DEFAULT_NAME)
        path = named_config_path(name)
        if create or path.is_file():
            return Selected(path, state_path or named_state_path(name), name)
        if literal.is_file():
            return Selected(literal, state_path or default_state_path(), None)
        raise ConfigError(
            f"no config named `{name}`. Create it with `youpdated init {name}`, "
            "or see `youpdated configs`"
        )

    return Selected(Path(value).expanduser(), state_path or default_state_path(), None)
