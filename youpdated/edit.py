"""Add or remove config entries without rewriting the file"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generator

import yaml

from . import crypto
from .config import ConfigError
from .models import Target
from .registry import all_aliases, all_sources, canonical_name
from .sources.base import Source

_NULL_TAG = "tag:yaml.org,2002:null"
# Indent of new sections and lists
_STEP = 2
# plain scalar is written only if it reads back the same in all of these
_CONTEXTS = ("- {}", "[{}]", "{}: x")


class _Unsupported(Exception):
    """The file is shaped in a way that it cannot safely be edited"""


@dataclass
class Change:
    text: str
    # `sources:` key entries went under, or the source they came out of
    section: str
    applied: list[str] = field(default_factory=list)
    # Entries already watched (add) or not watched (remove), left alone
    skipped: list[str] = field(default_factory=list)
    # Targets in a remove that nothing in the config watches, for `--wipe`
    # a key can appear more than once, from entries with different options
    dropped: list[Target] = field(default_factory=list)

    @property
    def dropped_keys(self) -> list[str]:
        return sorted({t.key for t in self.dropped})


@dataclass
class _Parsed:
    source: str
    handler: Source
    # source's name, then aliases: every `sources:` key it is listed under
    names: list[str]
    # text with `\n` line ends
    body: str
    data: Any
    # `sources:` mapping or empty
    listed: dict[Any, Any]

    def sections(self) -> list[str]:
        return [n for n in self.names if isinstance(self.listed.get(n), list)]


def yaml_scalar(value: str) -> str:
    """``value`` as YAML: bare when read back unchanged, otherwise double quoted"""
    if all(_reads_back(context, value) for context in _CONTEXTS):
        return value
    # JSON strings are valid YAML double-quoted scalars
    return json.dumps(value, ensure_ascii=False)


# no break spaces yaml doesn't like and invisible ones
_INVISIBLE = str.maketrans(
    {" ": " ", "​": None, "‌": None, "‍": None, "⁠": None, "﻿": None}
)
# smart quotes: opener -> what it stands for, and closers
_CURLY = {"“": ('"', "”“"), "„": ('"', "”“"), "‘": ("'", "’‘")}
_BARE = re.compile(r"found character '([@`])' that cannot start any token")


def tidy_config(text: str) -> tuple[str, list[str]]:
    """``text`` with common config typos fixed"""
    fixes: list[str] = []
    lines = [n for n, line in enumerate(text.splitlines(), 1) if line.translate(_INVISIBLE) != line]
    if lines:
        text = text.translate(_INVISIBLE)
        where = "line" if len(lines) == 1 else "lines"
        fixes.append(f"{where} {', '.join(map(str, lines))}: no-break or invisible spaces")
    text = _quote_bare(text, fixes)
    return _straighten(text, fixes), fixes


def _quote_bare(text: str, fixes: list[str]) -> str:
    """quotes values YAML refuses to start with `@` or backtick"""
    for _ in range(text.count("@") + text.count("`")):
        try:
            yaml.safe_load(text)
            break
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark
            if mark is None or not _BARE.fullmatch(exc.problem or ""):
                break
        start = mark.index
        end = text.find("\n", start)
        line = text[start : len(text) if end < 0 else end].rstrip("\r")
        before = text[text.rfind("\n", 0, start) + 1 : start]
        flow = before.count("[") + before.count("{") > before.count("]") + before.count("}")
        stops = [" #", ": "] + ([",", "]", "}"] if flow else [])
        value = line[: min([i for i in map(line.find, stops) if i >= 0] or [len(line)])].rstrip()
        quoted = json.dumps(value, ensure_ascii=False)
        text = text[:start] + quoted + text[start + len(value) :]
        fixes.append(f"line {mark.line + 1}: {value} --> {quoted} (it needs quotes)")
    return text


def _straighten(text: str, fixes: list[str]) -> str:
    """smart-quoted values changed to the correct form"""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return text
    found: list[tuple[int, int, str, str, int]] = []
    stack, seen = [root], set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            stack.extend(part for pair in node.value for part in pair)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
        elif isinstance(node, yaml.ScalarNode) and node.style is None and len(node.value) > 1:
            start, end = node.start_mark.index, node.end_mark.index
            quote, closers = _CURLY.get(node.value[0], ("", ""))
            if node.value[-1] in closers and "\n" not in text[start:end]:
                inner = node.value[1:-1]
                fixed = (
                    json.dumps(inner, ensure_ascii=False) if quote == '"'
                    else "'" + inner.replace("'", "''") + "'"
                )
                found.append((start, end, text[start:end], fixed, node.start_mark.line + 1))
    if not found:
        return text
    fixed_text = text
    for start, end, _, fixed, _ in sorted(found, reverse=True):
        fixed_text = fixed_text[:start] + fixed + fixed_text[end:]
    try:
        yaml.safe_load(fixed_text)
    except yaml.YAMLError:
        return text
    for _, _, old, new, line in sorted(found):
        fixes.append(f"line {line}: {old} --> {new} (smart quotes)")
    return fixed_text


def guess_source(value: str, command: str = "add") -> tuple[str, str]:
    """The source a given value belongs to and it's entry

    Sources with ``claim()`` say which values can only be theirs: a GitHub URL is `git`
    """
    value = _clean(value)
    claims: dict[str, str] = {}
    for name, source in sorted(all_sources().items()):
        claim = getattr(source, "claim", None)
        try:
            entry = claim(value) if claim is not None else None
        except Exception: # a plugin's broken claim can't stop the rest
            entry = None
        if entry is not None:
            claims[name] = entry
    if len(claims) == 1:
        return next(iter(claims.items()))
    if claims:
        raise ConfigError(
            f"`{value}` could be {' or '.join(claims)}; name one: "
            f"youpdated {command} {next(iter(claims))} {value}"
        )
    raise ConfigError(
        f"source type could not be determined for `{value}`, please provide a source type: "
        f"youpdated {command} [source type] {value}. Run `youpdated sources` to see the list."
    )


def add_to_config(text: str, source: str, values: list[str]) -> Change:
    """``text`` with ``values`` appended to ``source``'s list"""
    parsed = _parse(text, source)
    # alias section is used if canonical is absent
    section = next((n for n in parsed.names if n in parsed.listed), parsed.source)

    watched: set[str] = set()
    for name in parsed.sections():
        for entry in parsed.listed[name]:
            watched |= _keys(parsed.handler, entry)

    change = Change(text=text, section=section)
    lines: list[str] = []
    entries: list[Any] = []
    for value in values:
        value = _clean(value)
        line = yaml_scalar(value)
        entry = _read(line)
        try:
            keys = {t.key for t in parsed.handler.targets([entry])}
        except Exception as exc:
            raise ConfigError(str(exc)) from exc
        if keys and keys <= watched:
            change.skipped.append(value)
            continue
        watched |= keys
        change.applied.append(value)
        lines.append(line)
        entries.append(entry)

    if not lines:
        return change

    expected = copy.deepcopy(parsed.data) if isinstance(parsed.data, dict) else {}
    if not isinstance(expected.get("sources"), dict):
        expected["sources"] = {}
    expected["sources"][section] = list(expected["sources"].get(section) or []) + entries

    by_hand = "\n".join(f"  - {line}" for line in lines)
    with _explained(f"Add under `sources: {section}:` by hand:\n{by_hand}"):
        edited = _insert(parsed.body, yaml.compose(parsed.body), section, lines)
        _verify(edited, expected)
    change.text = _line_ends(edited, text)
    return change


def remove_from_config(text: str, source: str, values: list[str]) -> Change:
    """``text`` without ``source`` entries that watch ``values``"""
    parsed = _parse(text, source)
    wanted = []
    for value in values:
        value = _clean(value)
        entry = _read(yaml_scalar(value))
        wanted.append((value, entry, _keys(parsed.handler, entry)))

    def matches(entry: Any, value: str | None = None) -> bool:
        """if ``entry`` watches ``value``, or with no value"""
        watching = _keys(parsed.handler, entry)
        return any(
            (value is None or value == v) and (entry == raw or bool(keys & watching))
            for v, raw, keys in wanted
        )

    change = Change(text=text, section=parsed.source)
    sections = parsed.sections()
    for value, _, _ in wanted:
        found = any(matches(e, value) for name in sections for e in parsed.listed[name])
        (change.applied if found else change.skipped).append(value)

    # config's own entries first
    named: list[Target] = []
    kept: set[str] = set()
    for name in sections:
        for entry in parsed.listed[name]:
            targets = _targets(parsed.handler, entry)
            if matches(entry):
                named += targets
            else:
                kept.update(t.key for t in targets)
    for _, raw, _ in wanted:
        named += _targets(parsed.handler, raw)
    # something no longer watched can be wiped; something watched cannot
    change.dropped = [t for t in named if t.key not in kept]
    if not change.applied:
        return change

    expected = copy.deepcopy(parsed.data)
    for name in sections:
        expected["sources"][name] = [e for e in expected["sources"][name] if not matches(e)]

    with _explained("Remove from the config by hand."):
        edited = parsed.body
        # one item at a time, parsing after each
        while (hit := _first_match(edited, sections, matches)) is not None:
            shorter = _delete(edited, *hit)
            if len(shorter) >= len(edited):
                raise _Unsupported("an entry could not be taken out")
            edited = shorter
        _verify(edited, expected)
    change.text = _line_ends(edited, text)
    return change


def write_config(path: Path, text: str, passphrase: str | None = None) -> None:
    """replace the config atomically, encrypted again if ``passphrase`` is given"""
    target = path.resolve()
    if passphrase is not None:
        crypto.write_private(target, crypto.encrypt(text.encode("utf-8"), passphrase))
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
            shutil.copymode(target, tmp)
            os.replace(tmp, target)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as exc:
        raise ConfigError(f"cannot write {target}: {exc}") from exc

# reading

def _parse(text: str, source: str) -> _Parsed:
    name = canonical_name(source)
    handler = all_sources().get(name)
    if handler is None:
        raise ConfigError(f"unknown source `{source}`. Run `youpdated sources` to see options.")
    body = text.replace("\r\n", "\n")
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    listed = data.get("sources") if isinstance(data, dict) else None
    return _Parsed(
        source=name,
        handler=handler,
        names=[name] + sorted(a for a, of in all_aliases().items() if of == name),
        body=body,
        data=data,
        listed=listed if isinstance(listed, dict) else {},
    )


def _clean(value: str) -> str:
    value = value.strip()
    if not value:
        raise ConfigError("an entry must not be empty")
    return value


def _read(line: str) -> Any:
    return yaml.safe_load(f"- {line}")[0]


def _reads_back(context: str, value: str) -> bool:
    try:
        loaded = yaml.safe_load(context.format(value))
        read = loaded[0] if isinstance(loaded, list) else next(iter(loaded))
    except (yaml.YAMLError, IndexError, StopIteration, TypeError):
        return False
    return read == value or type(read) is int and str(read) == value


def _targets(handler: Source, entry: Any) -> list[Target]:
    """targets an entry watches"""
    if isinstance(entry, dict):
        entry = {k: v for k, v in entry.items() if k not in ("ignore", "versions")}
    try:
        return handler.targets([entry])
    except Exception:
        return []


def _keys(handler: Source, entry: Any) -> set[str]:
    return {t.key for t in _targets(handler, entry)}


def _line_ends(edited: str, original: str) -> str:
    return edited.replace("\n", "\r\n") if "\r\n" in original else edited


@contextmanager
def _explained(by_hand: str) -> Generator[None, None, None]:
    """turn an edit refusal into a ConfigError"""
    try:
        yield
    except _Unsupported as exc:
        raise ConfigError(f"cannot edit this config safely: {exc}. {by_hand}") from exc
    except yaml.YAMLError as exc:
        # The file parsed before, so an edit broke it
        raise ConfigError(
            f"cannot edit this config safely: an edit would break the file. {by_hand}"
        ) from exc


def _verify(edited: str, expected: Any) -> None:
    """the edited text reads back as ``expected``"""
    try:
        after = yaml.safe_load(edited)
    except yaml.YAMLError:
        after = None
    if _empty_as_none(after) != _empty_as_none(expected):
        raise _Unsupported("the edited file would not read back as expected")


def _empty_as_none(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("sources"), dict):
        data = {**data, "sources": {k: v if v != [] else None for k, v in data["sources"].items()}}
    return data

# node positions

def _sources_node(root: yaml.Node | None) -> yaml.MappingNode | None:
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        return None
    found = _pair(root, "sources")
    if found is None or not isinstance(found[1], yaml.MappingNode) or found[1].flow_style:
        return None
    return found[1]


def _pair(mapping: yaml.MappingNode, name: str) -> tuple[yaml.Node, yaml.Node] | None:
    """key and value for ``name``"""
    found = None
    for key, value in mapping.value:
        if isinstance(key, yaml.ScalarNode) and key.value == name:
            found = (key, value)
    if found is not None and found[1].start_mark.index < found[0].start_mark.index:
        raise _Unsupported(f"`{name}` refers to an anchor")
    return found


def _is_empty(node: yaml.Node) -> bool:
    """``key:``, ``key: ~``, ``key: null``, ``key: []``"""
    if isinstance(node, yaml.ScalarNode):
        return node.tag == _NULL_TAG
    return not node.value


def _last_line(node: yaml.Node, lines: list[str]) -> int:
    """last line holding ``node``'s content"""
    while (
        isinstance(node, (yaml.MappingNode, yaml.SequenceNode))
        and not node.flow_style
        and node.value
    ):
        last = node.value[-1]
        node = last[1] if isinstance(node, yaml.MappingNode) else last
    end = node.end_mark
    row = end.line
    if end.column == 0 and row > node.start_mark.line:
        row -= 1
        while row > node.start_mark.line and not lines[row].strip():
            row -= 1
    return row


def _list_offset(sources: yaml.MappingNode) -> int:
    for key, value in sources.value:
        if isinstance(value, yaml.SequenceNode) and not value.flow_style and value.value:
            return value.start_mark.column - key.start_mark.column
    return _STEP


def _spaced(sources: yaml.MappingNode, lines: list[str]) -> bool:
    if len(sources.value) < 2:
        return False
    row = sources.value[-1][0].start_mark.line - 1
    while row >= 0 and lines[row].lstrip().startswith("#"):
        row -= 1
    return row >= 0 and not lines[row].strip()

# adding

def _insert(body: str, root: yaml.Node | None, section: str, items: list[str]) -> str:
    lines = body.split("\n")

    if root is None: # empty or only comments
        while lines and not lines[-1].strip():
            lines.pop()
        head = lines + [""] if lines else []
        return "\n".join(head + ["sources:", *_section(section, items, _STEP, _STEP), ""])

    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise _Unsupported("its top level is not a mapping")

    found = _pair(root, "sources")
    if found is None:
        col = root.start_mark.column
        new = ["", " " * col + "sources:", *_section(section, items, col + _STEP, _STEP)]
        return _insert_after(lines, _last_line(root, lines), new)

    key, sources = found
    if _is_empty(sources):
        _clear(lines, key, sources)
        col = key.start_mark.column + _STEP
        return _insert_after(lines, key.start_mark.line, _section(section, items, col, _STEP))
    if not isinstance(sources, yaml.MappingNode) or sources.flow_style:
        raise _Unsupported("`sources` is not written as an indented block")

    offset = _list_offset(sources)
    found = _pair(sources, section)
    if found is None:
        new = _section(section, items, sources.start_mark.column, offset)
        if _spaced(sources, lines):
            new.insert(0, "")
        return _insert_after(lines, _last_line(sources, lines), new)

    key, listing = found
    if _is_empty(listing):
        _clear(lines, key, listing)
        return _insert_after(
            lines, key.start_mark.line, _items(items, key.start_mark.column + offset)
        )
    if not isinstance(listing, yaml.SequenceNode):
        raise _Unsupported(f"`sources.{section}` is not a list")
    if listing.flow_style:
        end = listing.value[-1].end_mark
        row = lines[end.line]
        lines[end.line] = row[: end.column] + "".join(f", {i}" for i in items) + row[end.column :]
        return "\n".join(lines)
    new = _items(items, listing.start_mark.column)
    return _insert_after(lines, _last_line(listing, lines), new)


def _clear(lines: list[str], key: yaml.Node, node: yaml.Node) -> None:
    """remove an empty value from its key's line"""
    start, end = node.start_mark, node.end_mark
    if start.index == end.index:
        return
    if not start.line == end.line == key.start_mark.line:
        raise _Unsupported("an empty section's value is not on the same line as its name")
    row = lines[start.line]
    head, tail = row[: start.column].rstrip(), row[end.column :].strip()
    lines[start.line] = f"{head} {tail}" if tail else head


def _section(name: str, items: list[str], col: int, offset: int) -> list[str]:
    return [" " * col + f"{yaml_scalar(name)}:", *_items(items, col + offset)]


def _items(items: list[str], col: int) -> list[str]:
    return [" " * col + f"- {item}" for item in items]


def _insert_after(lines: list[str], row: int, new: list[str]) -> str:
    return "\n".join(lines[: row + 1] + new + lines[row + 1 :])

# removing

def _first_match(
    body: str, sections: list[str], matches: Callable[[Any], bool]
) -> tuple[yaml.SequenceNode, int] | None:
    """the list and index of the first entry to remove"""
    data = yaml.safe_load(body)
    sources = _sources_node(yaml.compose(body))
    if sources is None:
        raise _Unsupported("`sources` is not written as an indented block")
    for name in sections:
        found = _pair(sources, name)
        entries = data["sources"].get(name)
        if found is None or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if matches(entry):
                if not isinstance(found[1], yaml.SequenceNode):
                    raise _Unsupported(f"`sources.{name}` is not a list")
                return found[1], index
    return None


def _delete(body: str, listing: yaml.SequenceNode, index: int) -> str:
    """``body`` without item ``index`` of ``listing``, with its comments"""
    items = listing.value
    item = items[index]
    if listing.flow_style:
        if len(items) == 1:
            start, end = item.start_mark.index, item.end_mark.index
        elif index < len(items) - 1:
            start, end = item.start_mark.index, items[index + 1].start_mark.index
        else:
            start, end = items[index - 1].end_mark.index, item.end_mark.index
        return body[:start] + body[end:]

    lines = body.split("\n")
    col = listing.start_mark.column
    first = item.start_mark.line
    while first > listing.start_mark.line and lines[first][col : col + 1] != "-":
        first -= 1
    last = _last_line(item, lines)
    return "\n".join(lines[:first] + lines[last + 1 :])
