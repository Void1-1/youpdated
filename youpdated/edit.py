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
from typing import Any, Callable, Generator, Sequence

import yaml

from . import crypto
from .config import SETTINGS_KEYS, ConfigError, PrivacyConfig
from .http import Client
from .models import Target
from .registry import all_aliases, all_sources, canonical_name
from .sources.base import Source, scalar_key
from .state import State

_NULL_TAG = "tag:yaml.org,2002:null"
# Indent of new sections and lists
_STEP = 2
# plain scalar is written only if it reads back the same in all of these
_CONTEXTS = ("- {}", "[{}]", "{}: x")
# a value removed by key, which equals no config entry
_NO_ENTRY = object()
# An entry's keys the source never sees, see runner._split_entry_rules
RULE_KEYS = ("ignore", "versions")


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
    text = _straighten(text, fixes)
    text, moved = move_to_settings(text)
    if moved:
        fixes.append(f"top-level {_named(moved)} --> under `settings:`")
    return text, fixes


def _named(keys: list[str]) -> str:
    return ", ".join(f"`{key}`" for key in keys)


def move_to_settings(text: str) -> tuple[str, list[str]]:
    """``text`` with any settings still at the top level moved under `settings:`"""
    body = text.replace("\r\n", "\n")
    try:
        root = yaml.compose(body, Loader=yaml.SafeLoader)
        data = yaml.safe_load(body)
    except yaml.YAMLError:
        return text, []
    if not isinstance(root, yaml.MappingNode) or root.flow_style or not isinstance(data, dict):
        return text, []
    try:
        moved = _move_settings(body, root, data)
    except _Unsupported:
        return text, []
    if moved is None:
        return text, []
    return _line_ends(moved[0], text), moved[1]


def _move_settings(
    body: str, root: yaml.MappingNode, data: dict[Any, Any]
) -> tuple[str, list[str]] | None:
    section = data.get("settings")
    already = set(section) if isinstance(section, dict) else set()
    # (first row, last row, key) of each top-level setting
    blocks: list[tuple[int, int, str]] = []
    lines = body.split("\n")
    col = root.start_mark.column
    previous_end = -1
    for key, value in root.value:
        end = _last_line(value, lines)
        if isinstance(key, yaml.ScalarNode) and key.value in SETTINGS_KEYS:
            if key.value in already:
                # config refuses a setting in both places; let the user pick
                raise _Unsupported(f"`{key.value}` is also under `settings:`")
            first = key.start_mark.line
            while first - 1 > previous_end and _is_comment(lines[first - 1], col, col):
                first -= 1
            while end + 1 < len(lines) and _is_comment(lines[end + 1], col + 1, None):
                end += 1
            blocks.append((first, end, key.value))
        previous_end = end
    if not blocks:
        return None

    found = _pair(root, "settings")
    if found is not None:
        key, node = found
        if _is_empty(node):
            _clear(lines, key, node)
            after, shift = key.start_mark.line, key.start_mark.column + _STEP - col
        elif isinstance(node, yaml.MappingNode) and not node.flow_style:
            after, shift = _last_line(node, lines), node.start_mark.column - col
        else:
            raise _Unsupported("`settings` is not written as an indented block")
        header: list[str] = []
        insert_at = None
    else:
        after, shift, header = None, _STEP, [" " * col + "settings:"]
        insert_at = blocks[0][0]

    moved: list[str] = []
    for index, (first, last, _) in enumerate(blocks):
        moved += [" " * shift + line if line.strip() else line for line in lines[first : last + 1]]
        spaced = last + 1 < len(lines) and not lines[last + 1].strip()
        if spaced and index < len(blocks) - 1:
            moved.append("")

    removed = {row for first, last, _ in blocks for row in range(first, last + 1)}
    for first, last, _ in blocks:
        if last + 1 in removed:  # not the end of a run of blocks
            continue
        run_start = first
        while run_start - 1 in removed:
            run_start -= 1
        if insert_at is not None and run_start <= insert_at <= last:
            continue  # replaced by `settings:`, not removed
        gap_after = last + 1 < len(lines) and not lines[last + 1].strip()
        gap_before = run_start == 0 or not lines[run_start - 1].strip()
        if gap_after and gap_before:
            removed.add(last + 1)

    out: list[str] = []
    for row, line in enumerate(lines):
        if row == insert_at:
            out += header + moved
        if row not in removed:
            out.append(line)
        if row == after:
            out += moved
    edited = "\n".join(out)

    names = [name for _, _, name in blocks]
    expected = {k: v for k, v in data.items() if k not in names}
    expected["settings"] = {**(section or {}), **{name: data[name] for name in names}}
    _verify(edited, expected)
    return edited, names


def _is_comment(line: str, at_least: int, at_most: int | None) -> bool:
    """``line`` is a comment indented ``at_least`` up to ``at_most`` columns"""
    indent = len(line) - len(line.lstrip(" "))
    return (
        line.lstrip(" ").startswith("#")
        and indent >= at_least
        and (at_most is None or indent <= at_most)
    )


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


def claims(value: str) -> dict[str, str]:
    """Every source that says ``value`` could be theirs, with the entry it would be

    Sources with ``claim()`` say which values can only be theirs: a GitHub URL is `git`.
    A ``fallback`` source's claim counts only if no other source claims the value
    """
    value = _clean(value)
    found: dict[str, str] = {}
    fallbacks: dict[str, str] = {}
    for name, source in sorted(all_sources().items()):
        claim = getattr(source, "claim", None)
        try:
            entry = claim(value) if claim is not None else None
        except Exception: # a plugin's broken claim can't stop the rest
            entry = None
        if entry is not None:
            (fallbacks if getattr(source, "fallback", False) else found)[name] = entry
    return found or fallbacks


def guess_source(value: str, command: str = "add") -> tuple[str, str]:
    """source a value belongs to and entry"""
    value = _clean(value)
    found = claims(value)
    if len(found) == 1:
        return next(iter(found.items()))
    if found:
        raise ConfigError(
            f"`{value}` could be {' or '.join(found)}. Name one: "
            f"youpdated {command} {next(iter(found))} {value}"
        )
    raise ConfigError(
        f"source type could not be determined for `{value}`, please provide a source type: "
        f"youpdated {command} [source type] {value}. Run `youpdated sources` to see the list."
    )


def add_to_config(text: str, source: str, values: Sequence[str | dict[str, Any]]) -> Change:
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
        if isinstance(value, dict):
            entry = value
            line = value = yaml.safe_dump(
                value, default_flow_style=True, sort_keys=False, width=1 << 16
            ).strip()
            # the source never sees the rules, see runner._split_entry_rules
            fields = {k: v for k, v in entry.items() if k not in ("ignore", "versions")}
        else:
            value = _clean(value)
            line = yaml_scalar(value)
            entry = fields = _read(line)
        try:
            keys = {t.key for t in parsed.handler.targets([fields])}
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


def remove_from_config(
    text: str, source: str, values: list[str], *, by_key: bool = False
) -> Change:
    """``text`` without ``source`` entries that watch ``values``

    ``by_key``: the values are target keys
    """
    parsed = _parse(text, source)
    wanted = []
    for value in values:
        value = _clean(value)
        if by_key:
            wanted.append((value, _NO_ENTRY, {value}))
            continue
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
        if raw is not _NO_ENTRY:
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


@dataclass
class Watching:
    """A config entry that watches a target"""

    # `sources:` key it is listed under
    section: str
    index: int
    entry: Any
    # every target the entry watches
    keys: set[str]


def watching(text: str, source: str, key: str) -> list[Watching]:
    """``source``'s entries in ``text`` that watch ``key``"""
    return _watching(_parse(text, source), key)


def _watching(parsed: _Parsed, key: str) -> list[Watching]:
    return [
        Watching(name, index, entry, keys)
        for name in parsed.sections()
        for index, entry in enumerate(parsed.listed[name])
        if key in (keys := _keys(parsed.handler, entry))
    ]


def set_entry_rules(text: str, source: str, key: str, rules: dict[str, Any]) -> Change:
    """``text`` with the entry watching ``key`` given ``rules``"""
    parsed = _parse(text, source)
    change = Change(text=text, section=parsed.source)
    found = _watching(parsed, key)
    if not found:
        change.skipped.append(key)
        return change
    if len(found) > 1:
        raise ConfigError(
            f"{key} is listed {len(found)} times under `sources: {parsed.source}:`. "
            "Edit its rules manually."
        )
    hit = found[0]
    change.section = hit.section
    by_hand = f"Edit the rules of {key} under `sources: {hit.section}:` manually."
    with _explained(by_hand):
        if isinstance(hit.entry, dict):
            fields = dict(hit.entry)
        else:
            name = scalar_key(parsed.handler, hit.entry)
            if name is None or _keys(parsed.handler, {name: hit.entry}) != hit.keys:
                raise _Unsupported("its entry can't be written as a mapping")
            fields = {name: hit.entry}
        new = {k: v for k, v in fields.items() if k not in RULE_KEYS}
        new.update({k: rules[k] for k in RULE_KEYS if rules.get(k)})
        if new == fields or (not isinstance(hit.entry, dict) and len(new) == 1):
            return change
        expected = copy.deepcopy(parsed.data)
        expected["sources"][hit.section][hit.index] = new
        edited = _rewrite(parsed.body, hit, new)
        _verify(edited, expected)
    change.applied.append(key)
    change.text = _line_ends(edited, text)
    return change


def requested_urls(source: Source | None, targets: list[Target], state: State) -> set[str]:
    """URLs ``targets`` ask for"""
    if source is None:
        return set()
    with Client(PrivacyConfig(), state, test=True) as client:
        for target in targets:
            try:
                list(source.fetch(target, client))
            except Exception: # a source that needs an answer to continue stops early
                pass
        return set(client.requested_urls)


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

# privacy options

# what the apps Settings can change
PRIVACY_EDITABLE = ("proxy", "fast_mode")


def set_privacy(text: str, changes: dict[str, Any]) -> str:
    """``text`` with privacy options set, or commented out where ``None``"""
    unknown = set(changes) - set(PRIVACY_EDITABLE)
    if unknown:
        raise ValueError(f"not editable here: {', '.join(sorted(unknown))}")
    body = text.replace("\r\n", "\n")
    by_hand = "Set it under `settings: privacy:` by hand: " + ", ".join(
        f"{name}: {_privacy_value(value)}" if value is not None else f"remove {name}"
        for name, value in changes.items()
    )
    with _explained(by_hand):
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise _Unsupported("it doesn't parse") from exc
        for name, value in changes.items():
            body = _set_privacy_option(body, name, value)
        _verify_privacy(body, data, changes)
    return _line_ends(body, text)


def _privacy_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return yaml_scalar(str(value))


def _privacy_node(root: yaml.MappingNode) -> tuple[yaml.Node, yaml.Node] | None:
    """`privacy:`'s key and value, at the top level or under `settings:`"""
    found = _pair(root, "privacy")
    if found is not None:
        return found
    settings = _pair(root, "settings")
    if settings is None or not isinstance(settings[1], yaml.MappingNode) or settings[1].flow_style:
        return None
    return _pair(settings[1], "privacy")


def _set_privacy_option(body: str, name: str, value: Any) -> str:
    lines = body.split("\n")
    root = yaml.compose(body, Loader=yaml.SafeLoader)
    if root is None:  # empty or only comments
        if value is None:
            return body
        while lines and not lines[-1].strip():
            lines.pop()
        head = lines + [""] if lines else []
        new = ["settings:", " " * _STEP + "privacy:", " " * 2 * _STEP + f"{name}: {_privacy_value(value)}"]
        return "\n".join(head + new + [""])
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise _Unsupported("its top level is not a mapping")

    found = _privacy_node(root)
    if found is None:
        return body if value is None else _add_privacy(lines, root, name, value)
    key, node = found
    if _is_empty(node):
        _clear(lines, key, node)
        col, last = key.start_mark.column + _STEP, key.start_mark.line
    elif isinstance(node, yaml.MappingNode) and not node.flow_style:
        col, last = node.start_mark.column, _last_line(node, lines)
        live = _pair(node, name)
        if live is not None:
            return _edit_live(lines, live, name, value)
    else:
        raise _Unsupported("`privacy` is not written as an indented block")

    if value is None:
        return "\n".join(lines)
    line = " " * col + f"{name}: {_privacy_value(value)}"
    # a `# name: ...` line
    end = last
    while end + 1 < len(lines) and _is_comment(lines[end + 1], key.start_mark.column + 1, None):
        end += 1
    pattern = re.compile(rf"^ {{{col}}}#\s?{re.escape(name)}:[^#]*?(\s+#.*)?$")
    for row in range(key.start_mark.line + 1, end + 1):
        match = pattern.match(lines[row])
        if match:
            lines[row] = line + (match.group(1) or "")
            return "\n".join(lines)
    return _insert_after(lines, last, [line])


def _edit_live(lines: list[str], live: tuple[yaml.Node, yaml.Node], name: str, value: Any) -> str:
    key, node = live
    row = key.start_mark.line
    if not (isinstance(node, yaml.ScalarNode) and node.start_mark.line == node.end_mark.line == row):
        raise _Unsupported(f"`{name}` is not a value on its own line")
    line = lines[row]
    if value is None:
        col = key.start_mark.column
        lines[row] = line[:col] + "# " + line[col:]
    else:
        lines[row] = line[: node.start_mark.column] + _privacy_value(value) + line[node.end_mark.column :]
    return "\n".join(lines)


def _add_privacy(lines: list[str], root: yaml.MappingNode, name: str, value: Any) -> str:
    """a new `privacy:` under `settings:`, which is added if missing"""
    col = root.start_mark.column
    found = _pair(root, "settings")
    if found is None:
        # above the first section
        first = root.value[0][0].start_mark.line
        while first > 0 and _is_comment(lines[first - 1], col, col):
            first -= 1
        block = [
            " " * col + "settings:",
            " " * (col + _STEP) + "privacy:",
            " " * (col + 2 * _STEP) + f"{name}: {_privacy_value(value)}",
            "",
        ]
        return "\n".join(lines[:first] + block + lines[first:])
    key, settings = found
    if _is_empty(settings):
        _clear(lines, key, settings)
        inner = key.start_mark.column + _STEP
    elif isinstance(settings, yaml.MappingNode) and not settings.flow_style:
        inner = settings.start_mark.column
    else:
        raise _Unsupported("`settings` is not written as an indented block")
    block = [" " * inner + "privacy:", " " * (inner + _STEP) + f"{name}: {_privacy_value(value)}"]
    return _insert_after(lines, key.start_mark.line, block)


def _verify_privacy(edited: str, before: Any, changes: dict[str, Any]) -> None:
    """only the changed privacy options differ"""
    try:
        after = yaml.safe_load(edited)
    except yaml.YAMLError:
        after = None
    if not isinstance(after, dict) and not (after is None and not changes):
        raise _Unsupported("the edited file would not read back as expected")

    def split(data: Any) -> tuple[dict[Any, Any], Any]:
        """(privacy options, everything else), empty sections as missing"""
        rest = copy.deepcopy(data) if isinstance(data, dict) else {}
        privacy = rest.pop("privacy", None)
        settings = rest.get("settings")
        if isinstance(settings, dict):
            if privacy is None:
                privacy = settings.pop("privacy", None)
            if not settings:
                rest.pop("settings")
        elif "settings" in rest and settings is None:
            rest.pop("settings")
        return dict(privacy or {}), _empty_as_none(rest)

    privacy, rest = split(before)
    for name, value in changes.items():
        if value is None:
            privacy.pop(name, None)
        else:
            privacy[name] = value
    if split(after) != (privacy, rest):
        raise _Unsupported("the edited file would not read back as expected")

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

# rules

def _flow(value: Any) -> str:
    return yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=1 << 16).strip()


def _rewrite(body: str, hit: Watching, new: dict[str, Any]) -> str:
    """``body`` with the entry at ``hit`` reading as ``new``"""
    sources = _sources_node(yaml.compose(body))
    if sources is None:
        raise _Unsupported("`sources` is not written as an indented block")
    found = _pair(sources, hit.section)
    if found is None or not isinstance(found[1], yaml.SequenceNode):
        raise _Unsupported(f"`sources.{hit.section}` is not a list")
    listing = found[1]
    item = listing.value[hit.index]
    start, end = item.start_mark, item.end_mark
    lines = body.split("\n")
    rules = [f"{name}: {_flow(new[name])}" for name in RULE_KEYS if name in new]

    if isinstance(item, yaml.ScalarNode):
        if listing.flow_style or start.line != end.line:
            return body[: start.index] + _flow(new) + body[end.index :]
        # `- a/b  # note` becomes `- repo: a/b  # note`, with its rules under it
        name = next(k for k in new if k not in RULE_KEYS)
        row = lines[start.line]
        lines[start.line] = f"{row[: start.column]}{yaml_scalar(name)}: {row[start.column :]}"
        return _insert_after(lines, start.line, [" " * start.column + rule for rule in rules])

    if not isinstance(item, yaml.MappingNode):
        raise _Unsupported("an entry is neither a value nor a mapping")
    if item.flow_style:
        return body[: start.index] + _flow(new) + body[end.index :]

    written: list[tuple[int, int, int, str]] = []
    for key, value in item.value:
        if isinstance(key, yaml.ScalarNode) and key.value in RULE_KEYS:
            first = key.start_mark.line
            written.append((first, max(first, _last_line(value, lines)), key.start_mark.column, key.value))
    have = {name for _, _, _, name in written}
    added = [" " * start.column + rule for rule in rules if rule.split(":")[0] not in have]
    lines = _insert_after(lines, _last_line(item, lines), added).split("\n")
    for first, last, column, name in sorted(written, reverse=True):
        lead = lines[first][:column]
        if name in new:
            lines[first : last + 1] = [f"{lead}{name}: {_flow(new[name])}"]
            continue
        del lines[first : last + 1]
        if lead.strip():
            # it was on the entry's `- `: the next key moves up to it
            lines[first] = lead + lines[first].lstrip()
    return "\n".join(lines)

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
