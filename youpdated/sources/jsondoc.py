"""Version from a JSON or YAML document

    sources:
      json:
        - https://example.com/app/latest.json
        - url: https://example.com/api/releases
          name: Example
          items: releases[*]
          version: tag_name
          link: html_url
          published: published_at
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Iterable
from urllib.parse import urljoin, urlsplit

import yaml

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from ..versions import release
from .base import ConfigEntryError, accepted, entry_fields, parse_iso, require, url_parts
from .git import _is_prerelease

PATH_CACHE = "json_path"

# Keys read as version when no `version:` path is set
GUESS_KEYS = ("version", "tag_name", "latest")
# Newest items of a list read per check
MAX_ITEMS = 20
MAX_VERSION = 100
# Versions shown in an error
SHOWN = 5
SHOWN_KEYS = 8
# Most values looked through for a version
MAX_NODES = 200_000
_SUFFIXES = (".json", ".yml", ".yaml")
# Per-entry options with a path
PATH_OPTIONS = ("items", "version", "link", "published", "title")

# A step of a path: a key, a list index, or `EACH` entry
EACH = object()
Path = tuple[Any, ...]
_MISSING = object()

_STEP_RE = re.compile(
    r"""\.?(?:
        \[(?P<index>-?\d+)\]
      | \[(?P<each>\*)\]
      | \[(?P<quote>["'])(?P<quoted>.*?)(?P=quote)\]
      | (?P<key>[^.\[\]]+)
    )""",
    re.VERBOSE,
)
_PLAIN_KEY_RE = re.compile(r"[^.\[\]\"'\s][^.\[\]]*")
_EPOCH_RE = re.compile(r"(\d{9,14})(?:\.\d+)?")
# A commit or checksum under a `version` key
_HASH_RE = re.compile(r"(?=.*[a-f])[0-9a-f]{7,}", re.IGNORECASE)


def parse_path(text: str) -> Path:
    """The steps of a path: `releases[0].tag_name`, `.[0].name`, `a["odd.key"]`; `.` is the root"""
    text = text.strip().removeprefix("$")
    if text in ("", "."):
        return ()
    steps: list[Any] = []
    position = 0
    while position < len(text):
        match = _STEP_RE.match(text, position)
        if match is None:
            raise ValueError(f"can't read `{text[position:]}`")
        if match.group("index") is not None:
            steps.append(int(match.group("index")))
        elif match.group("each") is not None:
            steps.append(EACH)
        elif match.group("quote") is not None:
            steps.append(match.group("quoted"))
        else:
            steps.append(match.group("key").strip())
        position = match.end()
    return tuple(steps)


def show_path(path: Path) -> str:
    """``path`` as in the config"""
    shown = ""
    for step in path:
        if step is EACH:
            shown += "[*]"
        elif isinstance(step, int):
            shown += f"[{step}]"
        elif _PLAIN_KEY_RE.fullmatch(step):
            shown += f".{step}" if shown else step
        else:
            shown += f'["{step}"]'
    return "." + shown if shown.startswith("[") or not shown else shown


def _step(node: Any, step: Any) -> Any:
    if isinstance(step, int):
        if isinstance(node, list) and -len(node) <= step < len(node):
            return node[step]
        return _MISSING
    return node.get(step, _MISSING) if isinstance(node, dict) else _MISSING


def _at(node: Any, path: Path) -> Any:
    """What ``path`` leads to from ``node``, or ``_MISSING``"""
    for step in path:
        node = _step(node, step)
        if node is _MISSING:
            break
    return node


def _scalar(value: Any) -> str | None:
    """A string or number as text"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return " ".join(value.split()) or None
    return None


def _version(value: Any) -> str | None:
    text = _scalar(value)
    return text if text and len(text) <= MAX_VERSION else None


def _guessed(value: Any) -> str | None:
    version = _version(value)
    if not version or not any(char.isdigit() for char in version):
        return None
    return None if _HASH_RE.fullmatch(version) else version


def _when(value: Any) -> datetime | None:
    text = _scalar(value)
    if text is None:
        return None
    epoch = _EPOCH_RE.fullmatch(text)
    if epoch:
        seconds = float(text) / (1000 if len(epoch.group(1)) > 11 else 1)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    parsed = parse_iso(text)
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _describe(node: Any) -> str:
    if isinstance(node, dict):
        keys = [f"`{key}`" for key in node if isinstance(key, str)]
        listed = ", ".join(keys[:SHOWN_KEYS]) + (", …" if len(keys) > SHOWN_KEYS else "")
        return f"a mapping with the keys {listed}" if keys else "an empty mapping"
    if isinstance(node, list):
        return f"a list of {len(node)}"
    if node is None or node is _MISSING:
        return "nothing"
    return f"{str(node)[:40]!r}"


def read_document(content: bytes, url: str) -> Any:
    """``content`` as JSON, else YAML"""
    try:
        return json.loads(content, parse_float=str, parse_constant=lambda name: None)
    except (ValueError, RecursionError):
        pass

    head = content[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<"):
        if b"<rss" in head or b"<feed" in head or b"<rdf:rdf" in head:
            raise FetchError(f"the address is a feed, not JSON: youpdated add feed {url}")
        if b"<html" in head or b"<!doctype html" in head:
            raise FetchError(f"the address is a web page, not JSON: youpdated add webpage {url}")
        raise FetchError("the address returned markup, not JSON or YAML")

    try:
        # every YAML value as text
        doc = yaml.load(content.decode("utf-8-sig", errors="replace"), Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError, ValueError):
        doc = None
    # any line of text is YAML, so only count a document with structure
    if not isinstance(doc, (dict, list)):
        raise FetchError("the address returned neither JSON nor YAML")
    return doc


def candidates(doc: Any) -> list[tuple[Path, str]]:
    """Every `version`, `tag_name`, or `latest` key holding a version, in document order"""
    found: list[tuple[Path, str]] = []
    stack: list[tuple[Path, Any]] = [((), doc)]
    visited: set[int] = set()
    budget = MAX_NODES
    while stack and budget > 0:
        path, node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, dict):
            children = [(key, value) for key, value in node.items() if isinstance(key, str)]
        elif isinstance(node, list):
            children = list(enumerate(node))
        else:
            continue
        budget -= len(children)
        for key, value in children:
            if isinstance(key, str) and key.lower() in GUESS_KEYS:
                version = _guessed(value)
                if version:
                    found.append((path + (key,), version))
        stack.extend(
            (path + (key,), value)
            for key, value in reversed(children)
            if isinstance(value, (dict, list))
        )
    return found


def _distinct(found: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """``found`` with one entry per version, the first path kept"""
    by_version: dict[str, tuple[Path, str]] = {}
    for path, version in found:
        by_version.setdefault(version, (path, version))
    return list(by_version.values())


def _shown(found: list[tuple[Path, str]]) -> str:
    listed = ", ".join(f"`{show_path(path)}` is {version}" for path, version in found[:SHOWN])
    return listed + (", …" if len(found) > SHOWN else "")


def _shared_list(found: list[tuple[Path, str]]) -> Path | None:
    """The list two versions are entries of: `releases` for `releases[0].tag_name` and `[1]`"""
    (first, _), (second, _) = found[:2]
    if len(first) != len(second):
        return None
    differing = [i for i, (a, b) in enumerate(zip(first, second)) if a != b]
    if len(differing) == 1 and isinstance(first[differing[0]], int):
        return first[: differing[0]]
    return None


def guess(doc: Any, pinned: Path | None, url: str) -> tuple[Path, str]:
    """Where the document's version is and what it reads"""
    if pinned is not None:
        version = _guessed(_at(doc, pinned))
        if version:
            return pinned, version
    distinct = _distinct(candidates(doc))
    if pinned is not None:
        message = f"earlier checks read the version at `{show_path(pinned)}`, which has moved"
        if len(distinct) == 1:
            message += f", though `{show_path(distinct[0][0])}` is {distinct[0][1]}"
        raise FetchError(
            message
            + ". The document may have changed: set `version:` to the new path of the version, or "
            f"restart with `youpdated rm --wipe json {url}` and add it again"
        )
    if not distinct:
        raise FetchError(
            "no `version`, `tag_name`, or `latest` key has a version; set `version:` to the "
            f"location. The document is {_describe(doc)}"
        )
    if len(distinct) > 1:
        message = (
            f"found multiple versions ({_shown(distinct)}); set `version:` to the correct path"
        )
        shared = _shared_list(distinct)
        if shared is not None:
            message += f", or `items: {show_path(shared)}` to watch every entry of the list"
        raise FetchError(message)
    return distinct[0]


@dataclass(frozen=True)
class Item:
    """One version a document lists"""

    version: str
    title: str | None = None
    link: str | None = None
    published: datetime | None = None
    # where a guessed version was read
    note: str | None = None


def _each(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return list(node.values())
    return node if isinstance(node, list) else []


def _entries(doc: Any, path: Path) -> list[tuple[str | None, Any]]:
    """What `items:` leads to, as ``(key, entry)``: the key of a mapping's entry"""
    shown = show_path(path)
    nodes: list[Any] = [doc]
    # the entries of a list or mapping
    for step in path[:-1] if path and path[-1] is EACH else path:
        if step is EACH:
            nodes = [entry for node in nodes for entry in _each(node)]
        else:
            nodes = [value for node in nodes if (value := _step(node, step)) is not _MISSING]
    if not nodes:
        raise FetchError(
            f"`items: {shown}` isn't in the document, which is {_describe(doc)}"
        )
    entries: list[tuple[str | None, Any]] = []
    for node in nodes:
        if isinstance(node, dict):
            entries.extend((key, entry) for key, entry in node.items() if isinstance(key, str))
        elif isinstance(node, list):
            entries.extend((None, entry) for entry in node)
        else:
            raise FetchError(f"`items: {shown}` is {_describe(node)}, not a list")
    return entries


def _entry_version(key: str | None, entry: Any, shown: str) -> str | None:
    """An entry's version without `version:`: a lone version key, itself, or its key"""
    if isinstance(entry, (dict, list)):
        distinct = _distinct(candidates(entry))
        if len(distinct) > 1:
            raise FetchError(
                f"an entry of `items: {shown}` has multiple versions ({_shown(distinct)}). "
                "Set `version:` to the correct path"
            )
        if distinct:
            return distinct[0][1]
    # a mapping is read as version to details
    if key is not None:
        return _guessed(key)
    return None if isinstance(entry, (dict, list)) else _guessed(entry)


def _link(node: Any, option: Any, base: str) -> str | None:
    """The address `link:` returns"""
    if option is None:
        return None
    value = option if isinstance(option, str) else _scalar(_at(node, option))
    if not value:
        return None
    try:
        link = urljoin(base, value)
        parts = urlsplit(link)
    except ValueError:
        return None
    return link if parts.scheme in ("http", "https") and parts.netloc else None


def _item(
    node: Any, version: str, params: dict[str, Any], base: str, note: str | None = None
) -> Item:
    title = params["title"]
    published = params["published"]
    return Item(
        version=version,
        title=None if title is None else _scalar(_at(node, title)),
        link=_link(node, params["link"], base),
        published=None if published is None else _when(_at(node, published)),
        note=note,
    )


def listed(doc: Any, params: dict[str, Any], base: str) -> list[Item]:
    """The newest items of `items:` list"""
    shown = show_path(params["items"])
    entries = _entries(doc, params["items"])
    path = params["version"]
    items: dict[str, Item] = {}
    for key, entry in entries:
        if path is None:
            version = _entry_version(key, entry, shown)
        else:
            version = _version(_at(entry, path))
        if version:
            items.setdefault(version, _item(entry, version, params, base))
    if entries and not items:
        where = "no version" if path is None else f"nothing at `version: {show_path(path)}`"
        raise FetchError(
            f"found {where} in the {len(entries)} entries of `items: {shown}`. Set `version:` "
            f"to its path. The first entry is {_describe(entries[0][1])}"
        )

    found = list(items.values())
    if all(item.published for item in found):
        found.sort(key=lambda item: item.published or datetime.min, reverse=True)
    else:
        found.sort(key=lambda item: release(item.version) or (), reverse=True)
    return found[: params["limit"]]


@register
class JsonSource:
    name: ClassVar[str] = "json"
    summary: ClassVar[str] = "Versions from a JSON or YAML document"
    caches: ClassVar[tuple[str, ...]] = (PATH_CACHE,)

    def claim(self, value: str) -> str | None:
        """URL ending: `.json`, `.yml`, `.yaml`"""
        url = url_parts(value)
        if url and url[1].rstrip("/").lower().endswith(_SUFFIXES):
            return accepted(self, value)
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "url", self.name)
            url = str(require(fields, "url", self.name)).strip()
            try:
                parts = urlsplit(url)
            except ValueError:
                parts = None
            if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
                raise ConfigEntryError(
                    f"sources.{self.name}: `{url}` is not an http(s) document URL"
                )
            params: dict[str, Any] = {
                option: self._path(fields, option, url) for option in PATH_OPTIONS
            }
            try:
                params["limit"] = max(1, int(fields.get("limit") or MAX_ITEMS))
            except (TypeError, ValueError) as exc:
                raise ConfigEntryError(
                    f"sources.{self.name}: `limit` for {url} must be a number, "
                    f"got {fields.get('limit')!r}"
                ) from exc
            targets.append(
                Target(source=self.name, key=url, label=fields.get("name"), params=params)
            )
        return targets

    def _path(self, fields: dict[str, Any], option: str, url: str) -> Path | str | None:
        value = fields.get(option)
        if value is None:
            return None
        where = f"sources.{self.name}: `{option}` for {url}"
        if not isinstance(value, str) or not value.strip():
            raise ConfigEntryError(
                f"{where} must be a path like `releases[0].tag_name`, got {value!r}"
            )
        if option == "link" and url_parts(value.strip()):
            return value.strip()
        try:
            path = parse_path(value)
        except ValueError as exc:
            raise ConfigEntryError(f"{where} is not a path, {exc}: `{value}`") from exc
        if EACH in path and option != "items":
            raise ConfigEntryError(
                f"{where} can't hold `[*]`: `items:` names the list, and `{option}:` starts at "
                "its entry"
            )
        return path

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        fetched = client.get(
            target.key, headers={"Accept": "application/json, */*;q=0.8"}, conditional=True
        )
        if fetched is None:
            return []

        doc = read_document(fetched.content, target.key)
        params = target.params
        if params["items"] is not None:
            items = listed(doc, params, fetched.url)
        else:
            version, note = self._version(doc, target, client)
            items = [_item(doc, version, params, fetched.url, note)]

        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"version:{item.version}",
                title=item.title or item.version,
                url=item.link or target.key,
                published=item.published,
                version=item.version,
                body=item.note,
                tags=("release",) + (("prerelease",) if _is_prerelease(item.version) else ()),
            )
            for item in items
        ]

    def _version(self, doc: Any, target: Target, client: Client) -> tuple[str, str | None]:
        """The version of a document that is one item"""
        path = target.params["version"]
        if path is not None:
            value = _at(doc, path)
            version = _version(value)
            if not version:
                raise FetchError(
                    f"`version: {show_path(path)}` is {_describe(value)}, not a version. "
                    f"The document is {_describe(doc)}"
                )
            return version, None

        state = client.state
        cached = state.cache_get(PATH_CACHE, target.key) if state else None
        pinned = None
        if cached is not None:
            try:
                pinned = parse_path(cached)
            except ValueError:
                pinned = None
        found, version = guess(doc, pinned, target.key)
        if state is not None and show_path(found) != cached:
            state.cache_set(PATH_CACHE, target.key, show_path(found))
        return version, f"Read from `{show_path(found)}` in the document"
