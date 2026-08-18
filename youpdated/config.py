"""Config load validation.

A source entry either bare scalar or a mapping
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import PlatformDirs

# appauthor=False for Windows: left unset, platformdirs falls back to app name as the author and nests everything under
# AppData\Local\youpdated\youpdated. macOS and Linux are unaffected.
_DIRS = PlatformDirs("youpdated", appauthor=False)

DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(Exception):
    """Raised with a message meant to be shown to the user."""


@dataclass
class PrivacyConfig:
    proxy: str | None = None
    user_agent: str = "rotate"
    jitter: tuple[float, float] = (0.5, 3.0)
    concurrency: int = 4
    timeout: float = 20.0


@dataclass
class Config:
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    sources: dict[str, list[Any]] = field(default_factory=dict)
    path: Path | None = None


def config_dir() -> Path:
    return Path(_DIRS.user_config_dir)


def data_dir() -> Path:
    return Path(_DIRS.user_data_dir)


def default_config_path() -> Path:
    return config_dir() / DEFAULT_CONFIG_NAME


def default_state_path() -> Path:
    return data_dir() / "state.sqlite3"


def find_config(explicit: str | Path | None = None) -> Path | None:
    """Resolve the config file to use: --config, then ./youpdated.yaml, then XDG."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in (
        Path.cwd() / "youpdated.yaml",
        Path.cwd() / "youpdated.yml",
        default_config_path(),
    ):
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: str | Path | None = None) -> Config:
    path = find_config(explicit)
    if path is None:
        raise ConfigError(
            "no config file found. Run `youpdated init` to create one at "
            f"{default_config_path()}"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    return parse_config(raw, path=path)


def parse_config(raw: Any, path: Path | None = None) -> Config:
    where = f"{path}: " if path else ""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}top level must be a mapping")

    privacy = _parse_privacy(raw.get("privacy") or {}, where)

    sources_raw = raw.get("sources")
    if sources_raw is None:
        raise ConfigError(f"{where}missing required `sources:` section")
    if not isinstance(sources_raw, dict):
        raise ConfigError(f"{where}`sources` must be a mapping of source name -> list")

    sources: dict[str, list[Any]] = {}
    for name, entries in sources_raw.items():
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ConfigError(f"{where}`sources.{name}` must be a list")
        if entries:
            sources[str(name)] = entries

    if not sources:
        raise ConfigError(f"{where}`sources` has no entries — nothing to check")

    return Config(privacy=privacy, sources=sources, path=path)


def _parse_privacy(raw: Any, where: str) -> PrivacyConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}`privacy` must be a mapping")

    privacy = PrivacyConfig()

    proxy = raw.get("proxy")
    if proxy is not None:
        if not isinstance(proxy, str) or "://" not in proxy:
            raise ConfigError(
                f"{where}`privacy.proxy` must be a URL like socks5://127.0.0.1:9050"
                # tor as example across project btw
            )
        privacy.proxy = proxy

    ua = raw.get("user_agent")
    if ua is not None:
        if not isinstance(ua, str) or not ua.strip():
            raise ConfigError(f"{where}`privacy.user_agent` must be 'rotate' or a UA string")
        privacy.user_agent = ua

    jitter = raw.get("jitter")
    if jitter is not None:
        if (
            not isinstance(jitter, (list, tuple))
            or len(jitter) != 2
            or not all(isinstance(v, (int, float)) and v >= 0 for v in jitter)
        ):
            raise ConfigError(f"{where}`privacy.jitter` must be [min, max] non-negative seconds")
        low, high = float(jitter[0]), float(jitter[1])
        if low > high:
            raise ConfigError(f"{where}`privacy.jitter` min must not exceed max")
        privacy.jitter = (low, high)

    for name, cast, check in (
        ("concurrency", int, lambda v: v >= 1),
        ("timeout", float, lambda v: v > 0),
    ):
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not check(cast(value)):
            raise ConfigError(f"{where}`privacy.{name}` must be a positive number")
        setattr(privacy, name, cast(value))

    return privacy


EXAMPLE_CONFIG = """\
# youpdated config: every source entry is a bare value or a mapping.

privacy:
  # Route every request through a proxy. Works with Tor's default SOCKS port.
  # proxy: socks5://127.0.0.1:9050
  user_agent: rotate       # 'rotate' or a fixed UA string
  jitter: [0.5, 3.0]       # random delay, in seconds, between hits on one host
  concurrency: 4
  timeout: 20

sources:
  github:
    - python/cpython
    - repo: astral-sh/uv
      watch: [releases, commits]

  npm:
    - express
    - "@types/node"

  steam:
    - 440
    - appid: 730
      name: Counter-Strike 2

  itch:
    # Watches devlog posts and new builds; most games have one.
    - https://hempuli.itch.io/baba-is-you
    - url: https://aak581.itch.io/engineering-marvels-from-hell
      watch: [devlog, releases]

  youtube:
    - "@NASA"
    # - playlist: PLxxxxxxxxxxxxxxxx

  browser:
    - chrome
    - brave
    - firefox
    - browser: edge
      platform: windows
      channel: beta

  # Anything else with a RSS/Atom feed
  feed:
    - https://blog.rust-lang.org/feed.xml
    - url: https://github.com/obsidianmd/obsidian-releases/releases.atom
      name: Obsidian
"""
