"""Config load validation.

A source entry either bare scalar or a mapping
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from platformdirs import PlatformDirs

from . import crypto

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

# More to be added potentially
WEBHOOK_FORMATS = ("json", "discord", "slack", "ntfy")


@dataclass
class Webhook:
    url: str
    format: str = "json"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class NotifyConfig:
    webhooks: list[Webhook] = field(default_factory=list)
    # argv lists, run once per update
    commands: list[list[str]] = field(default_factory=list)
    timeout: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.webhooks or self.commands)


#: top-level `ignore:` mapping applied to every source
IGNORE_ALL = "*"

#: How long a seen item is kept after it last appeared in a fetch
DEFAULT_EXPIRY = timedelta(days=365)
#: Shorter and conditional GETs stop paying off (see ``State.conditional_headers``)
MIN_EXPIRY = timedelta(days=1)

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdwy])$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(value: str) -> timedelta:
    """Parse a window like ``7d``, ``12h``, ``2w``, ``1y``"""
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise ValueError(f"`{value}` is not a duration like 30m, 12h, 7d, 2w, 1y")
    amount, unit = int(match.group(1)), match.group(2).lower()
    if unit == "y":
        return timedelta(days=365 * amount)
    return timedelta(**{_UNITS[unit]: amount})


@dataclass
class Config:
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    sources: dict[str, list[Any]] = field(default_factory=dict)
    #: Source name (or ``*``) -> update tags to drop
    ignore: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Forget seen items absent from every fetch this long. ``None`` keeps them forever
    expiry: timedelta | None = DEFAULT_EXPIRY
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    path: Path | None = None

    def ignored_tags(self, source: str) -> frozenset[str]:
        """The tags ignored for every target of ``source``, before entry rules"""
        return self.ignore.get(IGNORE_ALL, frozenset()) | self.ignore.get(
            source, frozenset()
        )


def config_dir() -> Path:
    return Path(_DIRS.user_config_dir)


def data_dir() -> Path:
    return Path(_DIRS.user_data_dir)


def default_config_path() -> Path:
    return config_dir() / DEFAULT_CONFIG_NAME


def default_state_path() -> Path:
    return data_dir() / "state.sqlite3"


def tool_dir() -> Path:
    """The folder holding the youpdated package: a checkout's root when run from source."""
    return Path(__file__).resolve().parent.parent


def project_config_paths() -> list[Path]:
    """Config files kept with a project: in the working directory, then beside the tool.

    Beside the tool lets a checkout's config work when run from elsewhere. For a pip install
    that folder is site-packages, which never has one.
    """
    folders = dict.fromkeys([Path.cwd(), tool_dir()])
    return [folder / name for folder in folders for name in ("youpdated.yaml", "youpdated.yml")]


def find_config(explicit: str | Path | None = None) -> Path | None:
    """Resolve the config file to use: --config, then a project config, then XDG."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in (*project_config_paths(), default_config_path()):
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: str | Path | None = None, passphrase: str | None = None) -> Config:
    path = find_config(explicit)
    if path is None:
        raise ConfigError(
            "no config file found. Run `youpdated init` to create one at "
            f"{default_config_path()}"
        )
    text = read_config_text(path, passphrase)
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    return parse_config(raw, path=path)


def read_config_text(path: Path, passphrase: str | None = None) -> str:
    """Config as YAML text, decrypting in memory first if it is encrypted."""
    blob = path.read_bytes()
    if not crypto.is_encrypted(blob):
        return blob.decode("utf-8")
    if passphrase is None:
        raise ConfigError(f"{path} is encrypted; a passphrase is required")
    try:
        return crypto.decrypt(blob, passphrase).decode("utf-8")
    except crypto.EncryptionError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


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

    return Config(
        privacy=privacy,
        sources=sources,
        ignore=_parse_ignore(raw.get("ignore"), where),
        expiry=_parse_expiry(raw.get("expiry", DEFAULT_EXPIRY), where),
        notify=_parse_notify(raw.get("notify"), where),
        path=path,
    )


def _parse_expiry(raw: Any, where: str) -> timedelta | None:
    """Parse top-level ``expiry:``, a duration or ``never``"""
    if isinstance(raw, timedelta):
        return raw
    # YAML reads `off`/`no` as False
    if raw is False or (isinstance(raw, str) and raw.strip().lower() == "never"):
        return None
    message = f"{where}`expiry` must be a duration like 180d, 52w, 1y, or `never`"
    if not isinstance(raw, str):
        raise ConfigError(message)
    try:
        expiry = parse_duration(raw)
    except ValueError as exc:
        raise ConfigError(message) from exc
    if expiry < MIN_EXPIRY:
        raise ConfigError(f"{where}`expiry` must be at least 1d")
    return expiry


def parse_ignore_list(value: Any, where: str, what: str) -> frozenset[str]:
    """Normalize an ``ignore:`` value"""
    if value is None:
        return frozenset()
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        raise ConfigError(f"{where}`{what}` must be a tag or a list of tags")
    tags = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where}`{what}` entries must be non-empty tag names")
        tags.add(item.strip().lower())
    return frozenset(tags)


def _parse_ignore(raw: Any, where: str) -> dict[str, frozenset[str]]:
    """Parse top-level ``ignore:``"""
    if raw is None:
        return {}
    if isinstance(raw, (str, list, tuple)):
        tags = parse_ignore_list(raw, where, "ignore")
        return {IGNORE_ALL: tags} if tags else {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{where}`ignore` must be a list of tags, or a mapping of "
            f"source name -> tags (`{IGNORE_ALL}` for every source)"
        )
    parsed: dict[str, frozenset[str]] = {}
    for name, value in raw.items():
        tags = parse_ignore_list(value, where, f"ignore.{name}")
        if tags:
            parsed[str(name)] = tags
    return parsed


def split_command(command: Any, where: str = "", name: str = "`notify.command`") -> list[str]:
    """notify command as argv. A string is split like a shell would"""
    if isinstance(command, list):
        if not command or not all(isinstance(part, (str, int, float)) for part in command):
            raise ConfigError(f"{where}{name} list must hold non-empty strings")
        return [str(part) for part in command]
    if not isinstance(command, str) or not command.strip():
        raise ConfigError(f"{where}{name} must be a command string or a list")
    try:
        if os.name == "nt":
            # posix=False keeps backslashes in Windows paths, but also keeps the quotes
            parts = shlex.split(command, posix=False)
            return [
                p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts
            ]
        return shlex.split(command)
    except ValueError as exc:
        raise ConfigError(f"{where}{name}: {exc}: {command}") from exc


# best guess of a format
def guess_webhook_format(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.removeprefix("ptb.").removeprefix("canary.") in ("discord.com", "discordapp.com"):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    if host == "ntfy.sh":
        return "ntfy"
    return "json"


# RFC 9110 token characters. httpx accepted in a header name
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_WEBHOOK_KEYS = {"url", "format", "headers"}


def _parse_webhook(raw: Any, where: str) -> Webhook:
    fields = {"url": raw} if isinstance(raw, str) else raw
    if not isinstance(fields, dict):
        raise ConfigError(f"{where}`notify.webhooks` entries must be a URL or a mapping")
    unknown = set(fields) - _WEBHOOK_KEYS
    if unknown:
        raise ConfigError(
            f"{where}unknown `notify.webhooks` option(s): "
            f"{', '.join(sorted(map(str, unknown)))}; expected {sorted(_WEBHOOK_KEYS)}"
        )
    url = fields.get("url")
    try:
        parts = urlsplit(url.strip()) if isinstance(url, str) else None
    except ValueError:  # e.g. an unclosed IPv6 bracket
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{where}`notify.webhooks` entry needs an http(s) `url`")
    url = url.strip()

    fmt = fields.get("format")
    if fmt is None:
        fmt = guess_webhook_format(url)
    elif not isinstance(fmt, str) or fmt.strip().lower() not in WEBHOOK_FORMATS:
        raise ConfigError(
            f"{where}`notify.webhooks` format must be one of {list(WEBHOOK_FORMATS)}"
        )
    fmt = fmt.strip().lower()
    if fmt == "ntfy" and not parts.path.strip("/"):
        raise ConfigError(
            f"{where}`notify.webhooks` ntfy URL needs a topic: https://ntfy.sh/my-topic"
        )

    headers = fields.get("headers") or {}
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, (str, int)) and not isinstance(v, bool)
        for k, v in headers.items()
    ):
        raise ConfigError(f"{where}`notify.webhooks` headers must map names to values")
    for name, value in headers.items():
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ConfigError(f"{where}`notify.webhooks` header name is not valid: {name!r}")
        if any(c in str(value) for c in "\r\n\0"):
            raise ConfigError(
                f"{where}`notify.webhooks` header {name} has a line break in its value"
            )
    return Webhook(url=url, format=fmt, headers={k: str(v) for k, v in headers.items()})


def _parse_notify(raw: Any, where: str) -> NotifyConfig:
    """Parse top-level ``notify:``"""
    if raw is None:
        return NotifyConfig()
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}`notify` must be a mapping with `webhooks` and/or `command`")

    notify = NotifyConfig()
    hooks = raw.get("webhooks")
    if hooks is not None:
        for hook in hooks if isinstance(hooks, list) else [hooks]:
            notify.webhooks.append(_parse_webhook(hook, where))

    command = raw.get("command")
    if command is not None:
        notify.commands.append(split_command(command, where))

    timeout = raw.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ConfigError(f"{where}`notify.timeout` must be a positive number of seconds")
        notify.timeout = float(timeout)

    unknown = set(raw) - {"webhooks", "command", "timeout"}
    if unknown:
        raise ConfigError(
            f"{where}unknown `notify` option(s): {', '.join(sorted(map(str, unknown)))}"
        )
    return notify


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

# Forget items that haven't appeared in any fetch for this long. Default 1y
# expiry: 1y

# Drop update types you don't want. A bare list applies
# everywhere; a mapping narrows it to one source ('*' means all).
# ignore:
#   '*': [prerelease]
#   git: [commit]

# Push each run's new updates to a webhook and/or a command run once per update.
# notify:
#   webhooks:
#     - https://ntfy.sh/my-private-topic
#   command: notify-send "{label}" "{title}"

sources:
  # GitHub, GitLab, Codeberg, or any Forgejo/Gitea instance
  git:
    - python/cpython                     # owner/repo means GitHub
    - repo: astral-sh/uv
      watch: [releases, commits]
      # Per-entry rules add to the ones above.
      ignore: [prerelease]
    - https://gitlab.com/gitlab-org/cli
    - codeberg.org/forgejo/forgejo

  npm:
    - express
    - "@types/node"

  homebrew:
    - wget
    - cask:firefox

  pypi:
    - httpx
    - youpdated

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
