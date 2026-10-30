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
from .versions import ANY_BUMP, BUMPS, release

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


# top-level `ignore:` mapping applied to every source
IGNORE_ALL = "*"

# How long a seen item is kept after it last appeared in a fetch
DEFAULT_EXPIRY = timedelta(days=365)
# Shorter and conditional GETs stop paying off (see ``State.conditional_headers``)
MIN_EXPIRY = timedelta(days=1)

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdwy])$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
MAX_DURATION = timedelta(days=365 * 100)


def parse_duration(value: str) -> timedelta:
    """Parse a window like ``7d``, ``12h``, ``2w``, ``1y``, up to 100y"""
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise ValueError(f"`{value}` is not a duration like 30m, 12h, 7d, 2w, 1y")
    amount, unit = int(match.group(1)), match.group(2).lower()
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}[unit]
    if seconds > MAX_DURATION.total_seconds():
        raise ValueError(f"`{value}` is longer than 100y")
    if unit == "y":
        return timedelta(days=365 * amount)
    return timedelta(**{_UNITS[unit]: amount})


@dataclass
class Config:
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    sources: dict[str, list[Any]] = field(default_factory=dict)
    # Source name (or ``*``) -> update tags to drop
    ignore: dict[str, frozenset[str]] = field(default_factory=dict)
    # Source name (or ``*``) -> `versions:` rules, see :func:`parse_version_rules`
    versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Forget seen items absent from every fetch this long. ``None`` keeps them forever
    expiry: timedelta | None = DEFAULT_EXPIRY
    # Ask every source that can for its latest release, even at a request more per target
    track_latest: bool = False
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    # editor `youpdated edit` opens
    editor: str | None = None
    path: Path | None = None

    def ignored_tags(self, source: str) -> frozenset[str]:
        """The tags ignored for every target of ``source``, before entry rules"""
        return self.ignore.get(IGNORE_ALL, frozenset()) | self.ignore.get(
            source, frozenset()
        )

    def version_rules(self, *sources: str) -> dict[str, Any]:
        """`versions:` rules for every target of ``sources`` """
        merged: dict[str, Any] = {}
        for name in (IGNORE_ALL, *sources):
            merged.update(self.versions.get(name, {}))
        return merged


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


def require_config(explicit: str | Path | None = None) -> Path:
    """:func:`find_config`, or an error that says to run `init`"""
    path = find_config(explicit)
    if path is None:
        raise ConfigError(
            "no config file found. Run `youpdated init` to create one at "
            f"{default_config_path()}"
        )
    return path


def load_config(explicit: str | Path | None = None, passphrase: str | None = None) -> Config:
    path = require_config(explicit)
    return parse_config_text(read_config_text(path, passphrase), path)


def parse_config_text(text: str, path: Path | None = None) -> Config:
    """:func:`parse_config` for YAML text"""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        where = f"{path}: " if path else ""
        raise ConfigError(f"{where}invalid YAML: {exc}") from exc
    return parse_config(raw, path=path)


def read_config_text(path: Path, passphrase: str | None = None) -> str:
    """Config as YAML text, decrypting in memory first if it is encrypted."""
    blob = path.read_bytes()
    if crypto.is_encrypted(blob):
        if passphrase is None:
            raise ConfigError(f"{path} is encrypted; a passphrase is required")
        try:
            blob = crypto.decrypt(blob, passphrase)
        except crypto.EncryptionError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path}: not UTF-8 text (byte {exc.start}); save it as UTF-8"
        ) from exc


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
        raise ConfigError(f"{where}`sources` has no entries. Nothing to check")

    return Config(
        privacy=privacy,
        sources=sources,
        ignore=_parse_ignore(raw.get("ignore"), where),
        versions=_parse_versions(raw.get("versions"), where),
        expiry=_parse_expiry(raw.get("expiry", DEFAULT_EXPIRY), where),
        track_latest=_parse_track_latest(raw.get("track_latest", False), where),
        notify=_parse_notify(raw.get("notify"), where),
        editor=_parse_editor(raw.get("editor"), where),
        path=path,
    )


def _parse_editor(raw: Any, where: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{where}`editor` must be a command, a program's path, or an app's name")
    return raw.strip()


def _parse_expiry(raw: Any, where: str) -> timedelta | None:
    """Parse top-level ``expiry:``, a duration or ``never``"""
    if isinstance(raw, timedelta):
        return raw
    # YAML reads `off`/`no` as False
    if raw is False or (isinstance(raw, str) and raw.strip().lower() == "never"):
        return None
    message = f"{where}`expiry` must be a duration like 180d, 52w, 1y (100y max), or `never`"
    if not isinstance(raw, str):
        raise ConfigError(message)
    try:
        expiry = parse_duration(raw)
    except ValueError as exc:
        raise ConfigError(message) from exc
    if expiry < MIN_EXPIRY:
        raise ConfigError(f"{where}`expiry` must be at least 1d")
    return expiry


def _parse_track_latest(raw: Any, where: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"{where}`track_latest` must be true or false")
    return raw


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


VERSION_RULES = ("bump", "min")
_MIN_RE = re.compile(r"^v?\d+(?:\.\d+)*$", re.IGNORECASE)


def parse_version_rules(value: Any, where: str, what: str) -> dict[str, Any]:
    """Normalize a ``versions:`` mapping: ``bump`` a BUMPS name or ``any``, ``min`` a release"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}`{what}` must be a mapping with `bump` and/or `min`")
    unknown = set(value) - set(VERSION_RULES)
    if unknown:
        raise ConfigError(
            f"{where}unknown `{what}` option(s): {', '.join(sorted(map(str, unknown)))}; "
            f"expected {', '.join(VERSION_RULES)}"
        )
    rules: dict[str, Any] = {}
    bump = value.get("bump")
    if bump is not None:
        choices = (*BUMPS, ANY_BUMP)
        if not isinstance(bump, str) or bump.strip().lower() not in choices:
            raise ConfigError(f"{where}`{what}.bump` must be one of {', '.join(choices)}")
        rules["bump"] = bump.strip().lower()
    lowest = value.get("min")
    if lowest is not None:
        rules["min"] = _parse_min(lowest, where, f"{what}.min")
    return rules


def _parse_min(value: Any, where: str, what: str) -> tuple[int, ...]:
    if isinstance(value, float):
        # 3.10 has already become 3.1 by now
        raise ConfigError(
            f"{where}`{what}` must be in quotes, as YAML reads {value} as a number "
            f'and drops trailing zeros (3.10 becomes 3.1): "{value}"'
        )
    # YAML reads `yes` and `no` as booleans, which are ints too
    readable = isinstance(value, (str, int)) and not isinstance(value, bool)
    text = str(value).strip() if readable else ""
    numbers = release(text) if _MIN_RE.match(text) else None
    if numbers is None:
        raise ConfigError(f'{where}`{what}` must be a version number like 2, "3.13", or "1.4.2"')
    return numbers


def _parse_versions(raw: Any, where: str) -> dict[str, dict[str, Any]]:
    """Parse top-level ``versions:``"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{where}`versions` must be a mapping of rules, or of "
            f"source name -> rules (`{IGNORE_ALL}` for every source)"
        )
    rules = [str(key) for key in raw if key in VERSION_RULES]
    if rules:
        names = [str(key) for key in raw if key not in VERSION_RULES]
        if names:
            raise ConfigError(
                f"{where}`versions` mixes rules ({', '.join(rules)}) with source names "
                f"({', '.join(names)}); put the rules under `'{IGNORE_ALL}':`"
            )
        parsed = parse_version_rules(raw, where, "versions")
        return {IGNORE_ALL: parsed} if parsed else {}
    by_source: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        parsed = parse_version_rules(value, where, f"versions.{name}")
        if parsed:
            by_source[str(name)] = parsed
    return by_source


def split_command(command: Any, where: str = "", name: str = "`notify.command`") -> list[str]:
    """notify command as argv. A string is split like a shell would"""
    if isinstance(command, list):
        # YAML reads a `yes` or `off` as boolean, which would run as "True"/"False"
        if not command or not all(
            isinstance(part, (str, int, float)) and not isinstance(part, bool) and part != ""
            for part in command
        ):
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
    url = url.strip() if isinstance(url, str) else ""
    try:
        parts = urlsplit(url)
    except ValueError:  # e.g. an unclosed IPv6 bracket
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{where}`notify.webhooks` entry needs an http(s) `url`")

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

# Also ask git forges and Steam for the latest release (or update post) of each
# entry, in case busier ones pushed it out of the list. One more request per entry.
# track_latest: true

# Drop update types you don't want. A bare list applies
# everywhere; a mapping narrows it to one source ('*' means all).
# ignore:
#   '*': [prerelease]
#   git: [commit]

# Report only some versions: `bump: major` keeps 4.0.0 and drops 3.9.1 (or
# `minor`, `patch`), and `min` drops versions below it. Takes the same forms as
# ignore:, and an entry can have its own.
# versions:
#   npm: {bump: major}

# The editor `youpdated edit` opens: a command, a program's path, or app's name on
# macOS. Default: $VISUAL, $EDITOR, or the system's text editor.
# editor: code

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
