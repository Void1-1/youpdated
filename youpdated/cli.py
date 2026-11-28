from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.control import Control
from rich.live_render import LiveRender
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.segment import ControlType, Segment
from rich.table import Column

from . import __version__, apptraces, crypto, notify, preview
from .cleanup import Trace, find_package, find_traces, remove_package, remove_traces
from .configs import CONFIG_ENV, Selected, check_name, chosen, named, select
from .config import (
    INIT_CONFIG,
    FAST_MODE_WARNING,
    NAMED_UA_CONFIRMED,
    NAMED_UA_UNCONFIRMED,
    NAMED_UA_WARNING,
    Command,
    Config,
    ConfigError,
    DEFAULT_NAME,
    default_config_path,
    default_state_path,
    find_config,
    ignored_legacy_config,
    legacy_config_path,
    legacy_state_path,
    named_config_path,
    named_state_path,
    load_config,
    names_itself_privately,
    parse_config_text,
    read_config_text,
    require_config,
    split_command,
    tool_dir,
)
from .edit import (
    add_to_config,
    ambiguous,
    candidates,
    guess_source,
    is_bare,
    move_to_settings,
    remove_from_config,
    requested_urls,
    tidy_config,
    watching,
    write_config,
)
from .http import Client, ProxyUnavailable, probe_proxy
from .importers import READERS, Found, read_file
from .preview import Option
from .registry import all_aliases, all_sources, canonical_name, get_source
from .render import json_out, rss_out, terminal
from .render import targets as targets_out
from .runner import build_targets, parse_since, record, rule_warnings, run
from .sources.base import ConfigEntryError
from .state import State, StateError, lock_path, stamp, state_lock


CONFIG_HELP = (
    "a config's name (see `youpdated configs`) or a path to a config file. "
    f"Default: ${CONFIG_ENV}, else the default config"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youpdated",
        description="Collect updates for the games, apps, and packages you follow.",
    )
    parser.add_argument("--version", action="version", version=f"youpdated {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="fetch sources and report what's new (default)")
    check.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
    check.add_argument(
        "-s", "--source", action="append", dest="sources",
        help="only check this source (repeatable)",
    )
    check.add_argument("--all", action="store_true", help="report everything, not just new items")
    check.add_argument("--since", help="only items newer than a window. (Eg: 7d, 12h)")
    check.add_argument("--json", action="store_true", help="emit JSON to stdout")
    check.add_argument("--rss", metavar="FILE", help="write an aggregated Atom feed")
    check.add_argument(
        "--no-save", action="store_true",
        help="do not record anything as seen (state untouched)",
    )
    check.add_argument(
        "--test", action="store_true",
        help="show what would have been requested",
    )
    check.add_argument(
        "--fast", action="store_true",
        help="fast mode for this run: shorter gaps between requests, less private "
        "(see settings.privacy.fast_mode)",
    )
    check.add_argument("--state", help="path to the state database")
    check.add_argument(
        "--all-configs", action="store_true",
        help="check the default config, then every named config, each with its own history",
    )
    check.add_argument("-v", "--verbose", action="store_true", help="show request detail and bodies")
    check.add_argument(
        "--fail-on-error", action="store_true",
        help="exit non-zero if any source failed",
    )
    check.add_argument(
        "--notify-cmd", action="append", metavar="CMD",
        help="run CMD once per new update, ex: 'notify-send {title} {url}' (repeatable)",
    )
    notify_mode = check.add_mutually_exclusive_group()
    notify_mode.add_argument(
        "--notify", action="store_true",
        help="send notifications even with --all or --no-save",
    )
    notify_mode.add_argument(
        "--no-notify", action="store_true", help="send no notifications this run"
    )

    sub.add_parser("sources", help="list available sources")

    listing = sub.add_parser(
        "list",
        help="list watched targets and their latest known version, without fetching",
    )
    listing.add_argument(
        "text", nargs="?", metavar="TEXT",
        help="targets names containing TEXT",
    )
    listing.add_argument(
        "-s", "--source", action="append", dest="sources",
        help="only list this source",
    )
    listing.add_argument(
        "--sort", choices=targets_out.SORTS,
        help="name, newest",
    )
    listing.add_argument("--json", action="store_true", help="emit JSON to stdout")
    listing.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
    listing.add_argument("--state", help="path to the state database")

    for name, aliases, help_text, verb, extra in (
        ("add", [], "start watching something", "watch", " [-y] [--no-check]"),
        ("remove", ["rm"], "stop watching something", "stop watching",
         " [--wipe] [--state STATE]"),
    ):
        edit = sub.add_parser(
            name, aliases=aliases, help=help_text,
            usage=f"%(prog)s [-h] [-c CONFIG] [--test]{extra} [SOURCE] ENTRY [ENTRY ...]",
        )
        edit.add_argument(
            "words", nargs="+", metavar="ENTRY",
            help=f"what to {verb}, ex: python/cpython or URL. Put the source first "
            "(git, npm, steam, ...) when the entry doesn't include it",
        )
        edit.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
        edit.add_argument("--test", action="store_true", help="show changes without writing")
        if name == "add":
            edit.add_argument(
                "-y", "--yes", action="store_true",
                help="look up a link's source without confirmation, and add the best match",
            )
            edit.add_argument(
                "--no-check", action="store_true",
                help="add a web page link as given, without fetching it's source or asking",
            )
        if name == "remove":
            edit.add_argument(
                "--wipe", action="store_true",
                help="also delete full history",
            )
            edit.add_argument("--state", help="path to the state database")

    imports = sub.add_parser(
        "import",
        help="watch dependencies from a package.json, requirements.txt, pyproject.toml, or Brewfile",
    )
    imports.add_argument(
        "files", nargs="+", metavar="FILE",
        help="package.json, requirements.txt, pyproject.toml, or Brewfile",
    )
    imports.add_argument(
        "--format", choices=list(READERS),
        help="read each FILE as this when not named",
    )
    imports.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
    imports.add_argument("--test", action="store_true", help="show changes without writing")

    scans = sub.add_parser(
        "scan",
        help="watch sources installed on this machine (needs youpdated[scanning])",
    )
    scans.add_argument(
        "--only", action="append", metavar="SCANNER",
        help="run only this scanner (steam, apps, winget, homebrew, flatpak)",
    )
    scans.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
    scans.add_argument("--test", action="store_true", help="show changes without writing")

    editor = sub.add_parser("edit", help="open the config in your editor")
    editor.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
    editor.add_argument("--state", help="path to the state database")

    init = sub.add_parser("init", help="write a starter config file, or a new named config")
    init.add_argument(
        "name", nargs="?", metavar="NAME",
        help="create a named config, separate from the others with its own history",
    )
    init.add_argument("-c", "--config", help="where to write it: a config's name, or a path")
    init.add_argument("-f", "--force", action="store_true", help="overwrite an existing file")
    init.add_argument(
        "-e", "--encrypt", action="store_true",
        help="write it encrypted",
    )

    for name, aliases, help_text in (
        ("encrypt", ["set-encrypted"], "encrypt the config and history with a passphrase"),
        ("decrypt", ["set-decrypted"], "convert an encrypted setup back to plain files"),
    ):
        crypt = sub.add_parser(name, aliases=aliases, help=help_text)
        crypt.add_argument("-c", "--config", metavar="CONFIG", help=CONFIG_HELP)
        crypt.add_argument("--state", help="path to the state database")

    configs = sub.add_parser(
        "configs",
        help="list, remove, or migrate configs",
        description="With no action, list every config. Pick one on any command with -c NAME.",
    )
    configs.add_argument("--json", action="store_true", help="emit JSON to stdout")
    actions = configs.add_subparsers(dest="action", metavar="ACTION")
    remove_config = actions.add_parser(
        "remove", aliases=["rm"], help="delete a named config and its history",
    )
    remove_config.add_argument("name", metavar="NAME", help="the config to delete")
    remove_config.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    remove_config.add_argument(
        "--test", action="store_true", help="list what would be deleted without deleting"
    )
    migrate = actions.add_parser(
        "migrate",
        help="move the default config and history from before named configs into configs/ and history/",
    )
    migrate.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    migrate.add_argument("--test", action="store_true", help="list moves without moving")

    uninstall = sub.add_parser(
        "uninstall",
        help="remove all Youpdated files and the package",
    )
    uninstall.add_argument("-c", "--config", help="also remove this config file")
    uninstall.add_argument("--state", help="also remove this state database")
    uninstall.add_argument(
        "--keep-config", action="store_true", help="delete files and package but keep config files"
    )
    uninstall.add_argument(
        "--keep-package", action="store_true", help="delete files but keep the package installed"
    )
    uninstall.add_argument(
        "--keep-app",
        action="store_true",
        help="keep desktop app settings, stored passphrase, and registrations",
    )
    uninstall.add_argument(
        "--test", action="store_true", help="list removals without deleting"
    )
    uninstall.add_argument(
        "-y", "--yes", action="store_true", help="skip confirmation"
    )

    api = sub.add_parser(
        "api",
        help="JSON interface for desktop apps",
        description="`serve` uses JSON lines on stdin and stdout",
    )
    api.add_argument("op", metavar="OP", help="serve, or an op, ex: status, inbox.list")
    api.add_argument("--json", action="store_true", help="print the reply as JSON (always on)")
    api.add_argument("--params", metavar="JSON", help="op's params, as JSON")
    api.add_argument("-c", "--config", help="path to a config file")
    api.add_argument("--state", help="path to the state database")

    return parser


def cmd_sources(console: Console) -> int:
    console.print("[bold]Available sources[/]\n")
    aliases = all_aliases()
    for name, source in sorted(all_sources().items()):
        console.print(f"  [cyan]{name:<10}[/] {getattr(source, 'summary', '')}")
        for alias in sorted(a for a, of in aliases.items() if of == name):
            console.print(f"  [dim]{alias:<10} same as `{name}`[/]")
    console.print(
        "\n[dim]Add with `youpdated add SOURCE ENTRY`, or under `sources:` in your config.[/]"
    )
    return 0


def _unknown_sources(names: list[str] | None) -> None:
    unknown = set(names or []) - set(all_sources()) - set(all_aliases())
    if unknown:
        raise ConfigError(
            f"unknown source(s): {', '.join(sorted(unknown))}. "
            "Run `youpdated sources` to see all."
        )


def cmd_list(args: argparse.Namespace, console: Console) -> int:
    from .api import target_rows

    _unknown_sources(args.sources)
    state_path = args.state
    passphrase = None
    if _setup_is_encrypted(find_config(args.config), state_path):
        passphrase = crypto.prompt_passphrase(name=args.config_name)
        crypto.derive_ahead(state_path, passphrase)
    config = load_config(args.config, passphrase=passphrase, allow_empty=True)
    targets, _ = build_targets(config)

    # Doesn't wait for the file lock! Reads the last saved history
    with State(state_path, passphrase=passphrase, read_only=True) as state:
        rows = target_rows(targets, state)
        failing = state.failing()
        # only while a `bump:` rule applies
        bumped = {(t.source, t.key) for t in targets if t.versions.bump}
        highest = {key: item for key, item in state.latest("top").items() if key in bumped}
        last_run = state.last_run()
        last_run_by = state.cache_get("app", "last_run_by")

    now = datetime.now(timezone.utc)
    for row in rows:
        key = (row["source"], row["key"])
        failure = failing.get(key)
        row["last_error"] = failure["message"] if failure else None
        row["stale"] = targets_out.is_stale(row["last_checked"], failure is not None, now)
        row["highest"] = highest.get(key)

    total = len(rows)
    if args.sources:
        wanted = {canonical_name(name) for name in args.sources}
        rows = [row for row in rows if row["source"] in wanted]
    if args.text:
        needle = args.text.casefold()
        rows = [
            row for row in rows
            if needle in row["display"].casefold() or needle in row["key"].casefold()
        ]
    rows = targets_out.sort_rows(rows, args.sort)
    by = last_run_by if last_run_by in ("app", "cli") else None

    if args.json:
        payload = {
            "targets": rows,
            "last_run": stamp(last_run) if last_run else None,
            "last_run_by": by,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not total:
        console.print(
            "[dim]Not watching anything yet. Start with `youpdated add`, "
            "or `youpdated import` for a dependency file.[/]"
        )
        return 0
    targets_out.render(
        rows,
        console,
        flat=args.sort == "newest",
        total=total,
        last_run=last_run,
        last_run_by=by,
    )
    return 0


def _edit_plan(command: str, words: list[str]) -> list[tuple[str, list[str]]]:
    """``[SOURCE] ENTRY...`` as (source, entries): source first, or one per entry"""
    if get_source(words[0]) is not None:
        if len(words) == 1:
            raise ConfigError(f"name what to change: youpdated {command} {words[0]} ENTRY")
        return [(words[0], words[1:])]
    plan: dict[str, list[str]] = {}
    for word in words:
        source, entry = guess_source(word, command)
        plan.setdefault(source, []).append(entry)
    return list(plan.items())


Plan = list[tuple[str, list[str]]]
# Picks what to add once the config is read: (text, path, passphrase) -> plan, or None to cancel
Picker = Callable[[str, Path, "str | None"], "Plan | None"]


class _Canceled(Exception):
    """Ctrl-C or the end of input at a prompt"""


def cmd_add(args: argparse.Namespace, console: Console) -> int:
    """`add`. A value only findable with a fetch is completed once confirmed. The rest is added as is, nothing sent"""
    if args.no_check:
        return cmd_edit(args, console, _edit_plan("add", args.words))
    items = _add_items(args.words)
    try:
        items = _confirm_detected(console, items, asking=sys.stdin.isatty() and not args.yes)
    except _Canceled:
        console.print("[dim]Canceled. Nothing was written.[/]")
        return 1
    if not items:
        return 0
    checked = [item for item in items if item.check]
    plain = [(i.source, i.entry) for i in items if not i.check]
    if not checked:
        return cmd_edit(args, console, _grouped(plain))
    interactive = sys.stdin.isatty()

    def pick(text: str, path: Path, passphrase: str | None) -> Plan | None:
        try:
            fetching = args.yes or (interactive and _fetch_allowed(console, checked))
        except _Canceled:
            console.print("[dim]Canceled. Nothing was sent.[/]")
            return None
        if fetching:
            choosing = interactive and not (args.yes or args.test)
            found = _looked_up(console, checked, text, path, args.state, passphrase, choosing)
        else:
            found = _as_given(console, checked)
            # Only videos, none of them added
            if not (plain or found):
                return None
        return None if found is None else _grouped([*plain, *found])

    return cmd_edit(args, console, pick=pick)


def _fetch_allowed(console: Console, items: list[_AddItem]) -> bool:
    """If the user lets ``items`` be fetched to look them up"""
    for item in items:
        why = "to find its channel" if item.entry is None else "to look for its feed or version"
        console.print(f"{escape(item.value)} [dim]would be fetched {why}[/]")
    noun = "it" if len(items) == 1 else "them"
    given = ", n adds as given" if any(item.entry is not None for item in items) else ""
    prompt = f"Fetch {noun}? [Y/n{given}] "
    while True:
        answer = _ask(prompt)
        if answer is None:
            raise _Canceled
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def _as_given(console: Console, items: list[_AddItem]) -> list[tuple[str, str]]:
    """``(source, entry)`` of ``items`` that can be added without a fetch"""
    found = []
    for item in items:
        if item.entry is not None:
            found.append((item.source, item.entry))
        else:
            console.print(
                f"[yellow]Not added[/] {escape(item.value)}: it's a video, and its channel is "
                "found on its page. Re-run with [bold]--yes[/] to fetch it, or name the channel."
            )
    return found


@dataclass
class _AddItem:
    value: str
    source: str
    # None until the source fetches it
    entry: str | None
    # Looked up and confirmed before it's added
    check: bool
    # A bare word's possible sources and entries
    choices: dict[str, str] = field(default_factory=dict)


def _add_items(words: list[str]) -> list[_AddItem]:
    """What each value is, checked before sending anything"""
    named = words[0] if get_source(words[0]) is not None else None
    if named is not None and len(words) == 1:
        raise ConfigError(f"name what to change: youpdated add {named} ENTRY")
    items = []
    for value in words[1:] if named is not None else words:
        resolving = preview.resolver(value.strip(), named)
        if resolving is not None:
            items.append(_AddItem(value.strip(), resolving, None, check=True))
            continue
        if named is None and is_bare(value):
            found = candidates(value)
            if not found:
                guess_source(value, "add")
            items.append(_AddItem(value.strip(), "", None, check=False, choices=found))
            continue
        source, entry = (named, value) if named is not None else guess_source(value, "add")
        try:
            preview.target(source, entry)
        except ConfigEntryError as exc:
            raise ConfigError(str(exc)) from exc
        fallback = named is None and getattr(get_source(source), "fallback", False)
        items.append(_AddItem(value, source, entry, check=fallback))
    return items


def _confirm_detected(console: Console, items: list[_AddItem], asking: bool) -> list[_AddItem]:
    """``items`` with each bare word's source settled"""
    if not asking:
        for item in items:
            if len(item.choices) > 1:
                raise ambiguous(item.value, list(item.choices))
    kept = []
    for item in items:
        if not item.choices:
            kept.append(item)
            continue
        picked = _detected(console, item.value, item.choices, asking)
        if picked is None:
            console.print(f"[dim]Left out {escape(item.value)}[/]")
            continue
        item.source, item.entry = picked
        kept.append(item)
    return kept


def _detected(
    console: Console, value: str, choices: dict[str, str], asking: bool
) -> tuple[str, str] | None:
    """``(source, entry)`` taken for the bare word ``value``"""
    names = list(choices)
    if len(names) == 1:
        console.print(f"{escape(value)} [dim]source detected:[/] {_source_line(names[0])}")
        if not asking:
            return names[0], choices[names[0]]
        while True:
            answer = _ask("Add it? [Y/n] ")
            if answer is None:
                raise _Canceled
            if answer in ("", "y", "yes"):
                return names[0], choices[names[0]]
            if answer in ("n", "no"):
                return None
    console.print(f"{escape(value)} [dim]could be:[/]")
    for number, name in enumerate(names, 1):
        console.print(f"  {number}. {_source_line(name)}")
    numbers = "/".join(str(n) for n in range(1, len(names) + 1))
    while True:
        answer = _ask(f"Add which? [{numbers}/n] ")
        if answer is None:
            raise _Canceled
        if answer in ("n", "no"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            name = names[int(answer) - 1]
            return name, choices[name]


def _source_line(name: str) -> str:
    summary = getattr(get_source(name), "summary", "")
    return f"[cyan]{escape(name)}[/]" + (f" [dim]({escape(summary)})[/]" if summary else "")


def _grouped(pairs: Iterable[tuple[str, str]]) -> Plan:
    plan: dict[str, list[str]] = {}
    for source, entry in pairs:
        plan.setdefault(source, []).append(entry)
    return list(plan.items())


def _looked_up(
    console: Console,
    items: list[_AddItem],
    text: str,
    path: Path,
    state_path: Path,
    passphrase: str | None,
    choosing: bool,
) -> list[tuple[str, str]] | None:
    """``(source, entry)`` to add"""
    config = parse_config_text(text, path)
    if names_itself_privately(config, passphrase is not None):
        if _named_ua_confirmed_at(state_path, passphrase):
            console.print(f"[yellow]warning:[/] {escape(NAMED_UA_WARNING)}")
        elif not sys.stdin.isatty():
            raise ConfigError(NAMED_UA_UNCONFIRMED)
        elif _confirm_named_ua(console):
            _record_named_ua(state_path, passphrase, console)
        else:
            console.print("[dim]Canceled. Nothing was sent.[/]")
            return None

    chosen: list[tuple[str, str]] = []
    failed = 0
    with Client(config.privacy) as client:
        for item in items:
            entry, why = item.entry, None
            if entry is None:
                try:
                    with console.status(f"Looking up {escape(item.value)}..."):
                        entry, why = preview.resolve(item.source, item.value, client)
                except Exception as exc:
                    console.print(
                        f"[red]Couldn't look up[/] {escape(item.value)}: {escape(str(exc))}"
                    )
                    failed += 1
                    continue
            change = add_to_config(text, item.source, [entry])
            if change.skipped:
                watched = change.watched_as.get(entry, entry)
                console.print(
                    f"[dim]Already watching {escape(watched)} under {escape(item.source)}[/]"
                )
                continue
            try:
                with console.status(f"Checking {escape(item.value)}..."):
                    found = preview.options(
                        item.source, entry, client, why=why, track_latest=config.track_latest
                    )
            except ConfigEntryError as exc:
                raise ConfigError(str(exc)) from exc
            try:
                option = _choose(console, item.value, found, choosing)
            except _Canceled:
                console.print("[dim]Canceled. Nothing was written.[/]")
                return None
            if option is None:
                console.print(f"[dim]Left out {escape(item.value)}[/]")
            else:
                chosen.append((option.source, option.entry))
    if failed == len(items):
        return None
    return chosen


def _choose(console: Console, value: str, found: list[Option], choosing: bool) -> Option | None:
    """The option to add for ``value``"""
    console.print(f"[bold]{escape(value)}[/]")
    several = len(found) > 1
    for number, option in enumerate(found, 1):
        lead = f"{number}. " if several else ""
        console.print(f"  {lead}{_option_line(option)}")
        console.print(f"  {' ' * len(lead)}  {_option_found(option)}")
    if not (choosing and several):
        return found[0]
    default = found[0] if found[0].error is None else None
    numbers = "/".join(str(n) for n in range(1, len(found) + 1))
    enter = "" if default is not None else ""
    while True:
        answer = _ask(f"Add which? [{numbers}/n{enter}] ")
        if answer is None:
            raise _Canceled
        if not answer:
            return default
        if answer in ("n", "no"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(found):
            return found[int(answer) - 1]


def _option_line(option: Option) -> str:
    line = f"[cyan]{escape(option.source)}[/] {escape(option.entry)}"
    return line + (f" [dim]({escape(option.why)})[/]" if option.why else "")


def _option_found(option: Option) -> str:
    if option.error is not None:
        return f"[yellow]couldn't check it:[/] {escape(option.error)}"
    name = escape(option.target.display)
    newest = option.newest
    if newest is None:
        return f"{name} [dim]lists nothing yet[/]"
    title = newest.title
    if newest.version and newest.version not in title:
        title = f"{title} ({newest.version})"
    when = f" [dim]{newest.published:%Y-%m-%d}[/]" if newest.published else ""
    return f"{name}, newest: {escape(title)}{when}"


def cmd_edit(
    args: argparse.Namespace,
    console: Console,
    plan: Plan | None = None,
    *,
    pick: Picker | None = None,
    labels: Mapping[tuple[str, str], str] | None = None,
) -> int:
    """`add`/`remove`, or `import`/`scan` with the ``plan`` it read. ``pick`` makes the plan once the config is read.
    ``labels`` names (source, entry) pairs whose entry doesn't say what they are"""
    adding = args.command in ("add", "import", "scan")
    wipe = getattr(args, "wipe", False)
    if plan is None and pick is None:
        plan = _edit_plan(args.command, args.words)
    path = require_config(args.config)
    state_path = args.state
    config_locked = crypto.is_encrypted_file(path)
    state_locked = wipe and state_path.is_file() and crypto.is_encrypted_file(state_path)
    passphrase = (
        crypto.prompt_passphrase(name=args.config_name) if config_locked or state_locked else None
    )

    text = edited = read_config_text(path, passphrase)
    if pick is not None:
        plan = pick(text, path, passphrase if config_locked else None)
        if plan is None:
            return 1
    assert plan is not None
    changes = []
    for source, entries in plan:
        change = (add_to_config if adding else remove_from_config)(edited, source, entries)
        edited = change.text
        changes.append(change)

    for change in changes:
        if adding and change.skipped:
            # one line per source, an import can skip a lot
            watched = dict.fromkeys(change.watched_as.get(s, s) for s in change.skipped)
            console.print(
                f"[dim]Already watching {escape(', '.join(watched))} "
                f"under {escape(change.section)}[/]"
            )
        elif not adding:
            for entry in change.skipped:
                console.print(
                    f"[yellow]Not watching {escape(entry)}[/] under {escape(change.section)}"
                )
    for change in changes if adding and labels else ():
        for entry in change.applied:
            name = labels.get((change.section, entry))
            if name:
                console.print(f"[dim]{escape(change.section)} {escape(entry)} is[/] {escape(name)}")
    applied = [c for c in changes if c.applied]
    wiped = [c for c in changes if c.dropped] if wipe else []
    if not applied and not wiped:
        return 0

    if args.test:
        diff = difflib.unified_diff(
            text.splitlines(), edited.splitlines(), str(path), str(path), n=1, lineterm=""
        )
        for line in diff:
            console.print(escape(line))
        for change in wiped:
            console.print(
                f"would forget the history of {escape(change.section)}: "
                f"{escape(', '.join(change.dropped_keys))}"
            )
        console.print("\n[dim]--test: nothing was written.[/]")
        return 0

    # history first
    if wiped and state_path.is_file():
        with State(state_path, passphrase=passphrase if state_locked else None) as state:
            for change in wiped:
                source = get_source(change.section)
                urls = requested_urls(source, change.dropped, state)
                seen, validators = state.forget(
                    change.section,
                    change.dropped_keys,
                    getattr(source, "caches", ()),
                    urls,
                    shared=getattr(source, "shared_caches", ()),
                )
                console.print(
                    f"Forgot the history of {escape(', '.join(change.dropped_keys))} "
                    f"[dim]({seen} seen item(s), {validators} cached address(es))[/]"
                )
    elif wiped:
        console.print("[dim]No history to forget.[/]")

    if applied:
        write_config(path, edited, passphrase if config_locked else None)
    verb, where = ("Added", "under") if adding else ("Removed", "from")
    for change in applied:
        console.print(
            f"{verb} {escape(', '.join(change.applied))} {where} "
            f"[cyan]{escape(change.section)}[/] in [bold]{escape(str(path))}[/]"
        )
    if adding and applied:
        console.print(
            "[dim]The next check records what exists, then what changes.[/]"
        )
    return 0


def cmd_import(args: argparse.Namespace, console: Console) -> int:
    found = Found()
    # files first
    for name in args.files:
        read_file(Path(name).expanduser(), found, args.format)
    return _add_found(args, console, found, "Nothing to watch in these files.")


SCANNING_HINT = (
    "Scanning this machine needs youpdated-scanning, which isn't part of the default install. "
    "Add it with `pipx install 'youpdated[scanning]'` or `pipx inject youpdated youpdated-scanning`."
)


def cmd_scan(args: argparse.Namespace, console: Console) -> int:
    # The scanners live in their own package. This hands their findings to the config editor, like `import`.
    try:
        import youpdated_scanning
    except ImportError:
        console.print(f"[red]Error:[/] {escape(SCANNING_HINT)}")
        return 1
    known = list(youpdated_scanning.SCANNERS)
    unknown = [name for name in args.only or () if name not in known]
    if unknown:
        console.print(
            f"[red]Error:[/] no scanner named {escape(', '.join(unknown))}; "
            f"expected one of {escape(', '.join(known))}"
        )
        return 1
    found = Found()
    installed = youpdated_scanning.scan(found, args.only)
    # A Steam app id doesn't say which game it is, and the config only gets the id
    labels = {
        (item.source, item.entry): item.name
        for item in installed if item.name and not _names(item.entry, item.name)
    }
    # An App Store app is found by its bundle id, and the config may list it by number
    aliases = {(item.source, item.entry): item.aliases for item in installed if item.aliases}
    return _add_found(args, console, found, "Found nothing to watch on this machine.", labels, aliases)


def _names(entry: str, name: str) -> bool:
    """``entry`` spells ``name`` out, ignoring case and punctuation: ``com.apple.dt.Xcode`` names Xcode"""
    letters = "".join(char for char in name.casefold() if char.isalnum())
    return bool(letters) and letters in "".join(char for char in entry.casefold() if char.isalnum())


def _add_found(
    args: argparse.Namespace, console: Console, found: Found, nothing: str,
    labels: Mapping[tuple[str, str], str] | None = None,
    aliases: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
) -> int:
    """report what ``found`` skipped, then add the rest to the config.
    ``aliases`` are other entries for a (source, entry) pair, which keep it out if the config has one"""
    for what, why in found.skipped:
        console.print(f"[yellow]Skipped[/] {escape(what)} [dim]: {escape(why)}[/]")
    for warning in found.warnings:
        console.print(f"[yellow]Warning:[/] {escape(warning)}")
    if not found.entries:
        console.print(f"[dim]{nothing}[/]")
        return 0
    plan = list(found.entries.items())
    if not aliases:
        return cmd_edit(args, console, plan, labels=labels)
    return cmd_edit(
        args, console, pick=lambda text, *_: _unaliased(console, text, plan, aliases), labels=labels
    )


def _unaliased(
    console: Console, text: str, plan: Plan, aliases: Mapping[tuple[str, str], tuple[str, ...]]
) -> Plan:
    """``plan`` less the entries the config in ``text`` already lists as one of their ``aliases``"""
    kept: Plan = []
    for source, entries in plan:
        handler = get_source(source)
        left = []
        for entry in entries:
            listed = None
            for alias in aliases.get((source, entry), ()) if handler else ():
                try:
                    keys = [target.key for target in handler.targets([alias])]
                except ConfigEntryError:
                    continue
                if any(watching(text, source, key) for key in keys):
                    listed = alias
                    break
            if listed is None:
                left.append(entry)
            else:
                console.print(
                    f"[dim]Already watching {escape(entry)} under {escape(source)}, as {escape(listed)}[/]"
                )
        if left:
            kept.append((source, left))
    return kept


# An editor returning before this with nothing changed didn't wait for the file
_QUICK_EDITOR = 2.0
_DISCARDED = "[dim]Discarded. Nothing was written.[/]"
_WHEN_SAVED = "Press Enter once you've saved, or Ctrl-C to discard: "
_SET_EDITOR = "Set `settings.editor` in the config, or $VISUAL or $EDITOR, to the one you use."


def _editor_command(configured: str | None = None) -> tuple[list[str], bool]:
    """`settings.editor`, $VISUAL, $EDITOR, then system default"""
    if configured:
        return _configured_editor(configured)
    for name in ("VISUAL", "EDITOR"):
        command = os.environ.get(name, "")
        if command.strip():
            return split_command(command, name=f"${name}"), False
    if sys.platform == "win32":
        return ["notepad"], False
    # a window over ssh would open on the remote screen
    if not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY")):
        if sys.platform == "darwin":
            return ["open", "-t"], True
        desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        if desktop and shutil.which("xdg-open"):
            return ["xdg-open"], True
    return ["nano" if shutil.which("nano") else "vi"], False


def _configured_editor(value: str) -> tuple[list[str], bool]:
    """a command, a program's path (spaces and all), or on macOS an app's name or path"""
    path = Path(value).expanduser()
    if sys.platform == "darwin" and path.suffix == ".app":
        return ["open", "-a", os.path.expanduser(value)], True
    if path.is_file():
        return [str(path)], False
    command = split_command(value, name="`settings.editor`")
    if sys.platform == "darwin" and shutil.which(command[0]) is None:
        # like TextEdit or Visual Studio Code
        return ["open", "-a", os.path.expanduser(value)], True
    return command, False


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _scratch_dir() -> str | None:
    """memory-backed where the system has one"""
    shm = Path("/dev/shm")
    return str(shm) if shm.is_dir() and os.access(shm, os.W_OK) else None


def _run_editor(command: list[str], path: Path) -> int:
    env = {k: v for k, v in os.environ.items() if k != crypto.PASSPHRASE_ENV}
    process = subprocess.Popen([*command, str(path)], env=env)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return process.wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def _ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None


def _after_problems(can_save: bool) -> str:
    """`e` to edit again, `s` to save anyway, `d` to discard"""
    allowed = "esd" if can_save else "ed"
    prompt = (
        "edit again, save anyway, or discard? [E/s/d] " if can_save
        else "edit again or discard? [E/d] "
    )
    while True:
        answer = _ask(prompt)
        if answer is None:
            return "d"
        if not answer:
            return "e"
        if answer[0] in allowed:
            return answer[0]


def _draft_problems(text: str, path: Path) -> tuple[list[str], list[str]]:
    try:
        config = parse_config_text(text, path, allow_empty=True)
    except ConfigError as exc:
        return [str(exc)], []
    _, errors = build_targets(config)
    return [f"{e.source}: {e.message}" for e in errors], rule_warnings(config)


def _loads(text: str, path: Path) -> Config | None:
    try:
        return parse_config_text(text, path, allow_empty=True)
    except ConfigError:
        return None


def _named_ua_confirmed(state: State) -> bool:
    return state.cache_get(*NAMED_UA_CONFIRMED) is not None


def _confirm_named_ua(console: Console) -> bool:
    """Warn, then ask"""
    console.print(f"[yellow]Warning:[/] {escape(NAMED_UA_WARNING)}")
    return _ask("Send requests as Youpdated anyway? [y/N] ") in ("y", "yes")


def _named_ua_confirmed_at(state_path: Path, passphrase: str | None) -> bool:
    """If the history records the confirmation"""
    if not state_path.is_file():
        return False
    try:
        with State(state_path, passphrase=passphrase) as state:
            return _named_ua_confirmed(state)
    except (StateError, crypto.EncryptionError, OSError):
        return False


def _record_named_ua(state_path: Path, passphrase: str | None, console: Console) -> None:
    try:
        with State(state_path, passphrase=passphrase) as state:
            state.cache_set(*NAMED_UA_CONFIRMED, datetime.now(timezone.utc).isoformat())
    except (StateError, crypto.EncryptionError, OSError) as exc:
        console.print(
            f"[yellow]Couldn't record that in the history ({escape(str(exc))}).[/] "
            "The next check will ask again."
        )


def cmd_editor(args: argparse.Namespace, console: Console) -> int:
    """`edit`: the config through a copy, checked, then written back"""
    path = require_config(args.config)
    state_path = args.state
    locked = crypto.is_encrypted_file(path)
    passphrase = crypto.prompt_passphrase(name=args.config_name) if locked else None
    before = path.read_bytes()
    named_confirmed = False
    original = read_config_text(path, passphrase)
    loaded = _loads(original, path)

    command, opens = _editor_command(loaded.editor if loaded else None)
    program = shutil.which(command[0])
    if program is None:
        console.print(f"[red]Editor not found:[/] {escape(command[0])}. {_SET_EDITOR}")
        return 1
    name = escape(Path(command[0]).name)

    scratch = _scratch_dir()

    # if proxy is set & encrypted, consider as potentially worried about leakage of config.
    # one that doesn't load might be
    if locked and (loaded is None or loaded.privacy.proxy):
        console.print(
            "[yellow]Warning:[/] this config is encrypted and sends requests through a proxy, "
            "while you edit it, a plaintext copy is kept in "
            f"{escape(scratch or tempfile.gettempdir())}. It is deleted afterward; "
            "your editor or device may keep backups or history elsewhere."
        )
        if _ask("Continue? [y/N] ") not in ("y", "yes"):
            console.print("[dim]Canceled. Nothing was decrypted to disk.[/]")
            return 1

    with tempfile.TemporaryDirectory(
        prefix="youpdated-", dir=scratch, ignore_cleanup_errors=True
    ) as folder:
        # config's name, so its highlighted as YAML
        draft = Path(folder) / path.name
        # saving keeps the move, even if nothing else changed
        opened, moved = move_to_settings(original)
        if moved:
            console.print(
                f"[dim]Moved {escape(', '.join(f'`{key}`' for key in moved))} under "
                "`settings:`, where they go now. Saving keeps the move.[/]"
            )
        crypto.write_private(draft, opened.encode("utf-8"))
        while True:
            shown = _read(draft)
            if not opens:
                console.print(
                    f"[dim]Waiting for {name} to close {escape(path.name)}...[/]"
                )
            started = time.monotonic()
            try:
                status = _run_editor([program, *command[1:]], draft)
            except OSError as exc:
                console.print(f"[red]Cannot run {name}:[/] {escape(str(exc))}")
                return 1
            if status != 0:
                console.print(
                    f"[red]{name} exited with status {status}.[/] "
                    "Nothing was written."
                )
                if opens:
                    console.print(f"[dim]{_SET_EDITOR}[/]")
                return 1

            # a window editor that's still open, so wait for the user instead
            early = time.monotonic() - started < _QUICK_EDITOR and _read(draft) == shown
            if opens or early:
                console.print(
                    f"[dim]Opened {escape(path.name)} in your editor.[/]" if opens
                    else f"[dim]{name} returned right away; if it's still open, edit there.[/]"
                )
                if _ask(_WHEN_SAVED) is None:
                    console.print(_DISCARDED)
                    return 1

            try:
                edited = read_config_text(draft)
            except (ConfigError, OSError) as exc:
                console.print(f"[red]Config error:[/] {escape(str(exc))}")
                if _after_problems(can_save=False) == "e":
                    continue
                console.print(_DISCARDED)
                return 1
            if edited != original:
                edited, fixes = tidy_config(edited)
                for fix in fixes:
                    console.print(f"[dim]Fixed {escape(fix)}[/]")
                if fixes:
                    # so editing again starts from them
                    crypto.write_private(draft, edited.encode("utf-8"))
            if edited == original:
                console.print("[dim]No changes.[/]")
                return 0

            problems, warnings = _draft_problems(edited, path)
            if problems:
                for problem in problems:
                    console.print(f"[red]Config error:[/] {escape(problem)}")
                choice = _after_problems(can_save=True)
                if choice == "d":
                    console.print(_DISCARDED)
                    return 1
                if choice == "e":
                    continue

            drafted = _loads(edited, path)
            if (
                drafted is not None
                and names_itself_privately(drafted, locked)
                and not _named_ua_confirmed_at(state_path, passphrase)
            ):
                if not _confirm_named_ua(console):
                    if _after_problems(can_save=False) == "e":
                        continue
                    console.print(_DISCARDED)
                    return 1
                named_confirmed = True
            break

    for warning in warnings:
        console.print(f"[yellow]Warning:[/] {escape(warning)}")
    try:
        current: bytes | None = path.read_bytes()
    except OSError:
        current = None
    if current != before:
        console.print(f"[yellow]{escape(str(path))} changed while you were editing it.[/]")
        if _ask("Overwrite it with your edits? [y/N] ") not in ("y", "yes"):
            console.print(_DISCARDED)
            return 1
    write_config(path, edited, passphrase)
    console.print(
        f"Saved {'encrypted ' if locked else ''}changes to [bold]{escape(str(path))}[/]"
    )
    if named_confirmed:
        _record_named_ua(state_path, passphrase, console)
    return 0


def cmd_init(args: argparse.Namespace, console: Console) -> int:
    if args.name and args.config:
        raise ConfigError("name the config or give -c, not both")
    # NAME is only ever a name, where -c can be a path
    selected = select(check_name(args.name) if args.name else args.config, create=True)
    path = selected.config or default_config_path()
    if path.exists() and not args.force:
        console.print(f"[yellow]{escape(str(path))} already exists.[/] Use --force to overwrite.")
        return 1
    # `-c NAME` on the next steps, unless it's the default
    flag = f" -c {selected.name}" if selected.name not in (None, DEFAULT_NAME) else ""
    if flag:
        console.print(
            f"[dim]`{escape(selected.name or '')}` has its own sources, settings, and history. "
            f"Use it with -c {escape(selected.name or '')} or ${CONFIG_ENV}.[/]"
        )

    if not args.encrypt:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(INIT_CONFIG, encoding="utf-8")
        console.print(f"Wrote starter config to [bold]{escape(str(path))}[/]")
        console.print(
            f"[dim]Change it with `youpdated edit{flag}` or `youpdated add{flag}`, "
            f"then run `youpdated check{flag}`.[/]"
        )
        return 0

    # Encrypted from the first byte:
    # there is no plaintext copy left in free space like `init` then `encrypt`
    crypto.require_backend()
    console.print(
        "[red]There is no recovery if you forget this passphrase.[/] "
        "The config and history will be the only copy.\n"
    )
    passphrase = crypto.prompt_passphrase("New passphrase: ", confirm=True, name=selected.name)
    crypto.write_private(path, crypto.encrypt(INIT_CONFIG.encode("utf-8"), passphrase))

    console.print(f"Wrote encrypted starter config to [bold]{escape(str(path))}[/]")
    console.print(
        f"[dim]Use `youpdated add{flag}` and `youpdated rm{flag}` to change what it watches, and "
        f"`youpdated edit{flag}` for anything else.[/]"
    )
    console.print(
        f"[dim]Every command now asks for the passphrase. Set ${_passphrase_variable(selected.name)} "
        "for unattended runs.[/]"
    )
    return 0


def _passphrase_variable(name: str | None) -> str:
    """The variable to suggest for unattended runs: a named config's own, else the shared one"""
    if name in (None, DEFAULT_NAME):
        return crypto.PASSPHRASE_ENV
    return crypto.passphrase_env(name)


def _crypt_paths(args: argparse.Namespace) -> tuple[Path | None, Path]:
    """The config and state files used"""
    return find_config(args.config), args.state


def _setup_is_encrypted(config_path: Path | None, state_path: Path) -> bool:
    """Whether this setup is locked. Either file being encrypted counts."""
    return any(
        path is not None and crypto.is_encrypted_file(path)
        for path in (config_path, state_path)
    )


def cmd_encrypt(args: argparse.Namespace, console: Console) -> int:
    crypto.require_backend()
    config_path, state_path = _crypt_paths(args)
    files = [p for p in (config_path, state_path) if p is not None and p.is_file()]
    if not files:
        console.print("[yellow]Nothing to encrypt.[/] Run `youpdated init` first.")
        return 1

    locked = [p for p in files if crypto.is_encrypted_file(p)]
    todo = [p for p in files if p not in locked]
    if not todo:
        console.print("[dim]Already encrypted:[/]")
        for path in locked:
            console.print(f"  {escape(str(path))}")
        return 0

    if config_path in todo:
        try:
            load_config(config_path)
        except ConfigError as exc:
            console.print(
                f"[yellow]Warning:[/] config does not currently parse: {escape(str(exc))}"
            )

    console.print("[bold]This will encrypt:[/]\n")
    for path in todo:
        console.print(f"  [yellow]{escape(str(path))}[/]")
    console.print(
        "\n[red]There is no recovery if you forget the passphrase.[/] "
        "The files are the only copy."
    )
    if locked:
        console.print("[dim]Enter the passphrase the rest of the setup already uses.[/]")
    console.print()

    passphrase = crypto.prompt_passphrase(
        "New passphrase: ", confirm=not locked, name=args.config_name
    )

    for path in locked:
        crypto.decrypt(path.read_bytes(), passphrase)

    # A running check would write its own copy over converted state
    with state_lock(state_path):
        for path in todo:
            crypto.write_private(path, crypto.encrypt(path.read_bytes(), passphrase))
            console.print(f"[green]encrypted[/] {escape(str(path))}")

    console.print(
        "\n[dim]Every command now asks for the passphrase. "
        f"Set ${_passphrase_variable(args.config_name)} for unattended runs.[/]"
    )
    console.print(
        "[dim]The previous plaintext contents may still be recoverable from free "
        "space on the disk[/]"
    )
    return 0


def cmd_decrypt(args: argparse.Namespace, console: Console) -> int:
    crypto.require_backend()
    config_path, state_path = _crypt_paths(args)
    todo = [
        p for p in (config_path, state_path)
        if p is not None and crypto.is_encrypted_file(p)
    ]
    if not todo:
        console.print("[dim]Nothing is encrypted.[/]")
        return 0

    console.print("[bold]This will write back in plain text:[/]\n")
    for path in todo:
        console.print(f"  [yellow]{escape(str(path))}[/]")
    console.print()

    passphrase = crypto.prompt_passphrase(name=args.config_name)
    with state_lock(state_path):
        # Decrypt everything before writing
        plain = [(path, crypto.decrypt(path.read_bytes(), passphrase)) for path in todo]
        for path, payload in plain:
            crypto.write_private(path, payload)
            console.print(f"[green]decrypted[/] {escape(str(path))}")
    return 0


def _config_row(name: str, path: Path | None, state_path: Path, selected: str | None) -> dict[str, Any]:
    """One config for `youpdated configs`. An encrypted config is counted only once unlocked"""
    exists = path is not None and path.is_file()
    encrypted = exists and crypto.is_encrypted_file(path)
    targets = error = None
    if exists and not encrypted:
        try:
            targets = len(build_targets(load_config(path, allow_empty=True))[0])
        except ConfigError as exc:
            error = str(exc)
    return {
        "name": name,
        "config": str(path) if path is not None else None,
        "history": str(state_path),
        "exists": exists,
        "encrypted": encrypted,
        "targets": targets,
        "error": error,
        "selected": name == selected,
    }


def _selected_name() -> tuple[str | None, str | None]:
    """The config $YOUPDATED_CONFIG picks"""
    value = chosen()
    if value is None:
        return DEFAULT_NAME, None
    try:
        return select(value).name, None
    except ConfigError as exc:
        return None, str(exc)


def cmd_configs(args: argparse.Namespace, console: Console) -> int:
    if args.action in ("remove", "rm"):
        return cmd_configs_remove(args, console)
    if args.action == "migrate":
        return cmd_configs_migrate(args, console)

    selected, unusable = _selected_name()
    rows = [_config_row(DEFAULT_NAME, find_config() or default_config_path(), default_state_path(), selected)]
    rows += [
        _config_row(name, named_config_path(name), named_state_path(name), selected)
        for name in named()
    ]
    ignored = ignored_legacy_config()
    legacy = legacy_config_path() if default_config_path() == legacy_config_path() else None
    if args.json:
        payload = {
            "configs": rows,
            "ignored_legacy_config": str(ignored) if ignored else None,
            "legacy_default": legacy is not None,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    width = max(len(row["name"]) for row in rows)
    for row in rows:
        mark = "[green]*[/]" if row["selected"] else " "
        if not row["exists"]:
            detail = "[dim]not created[/]"
        elif row["encrypted"]:
            detail = "[yellow]encrypted[/]"
        elif row["error"] is not None:
            detail = "[red]does not load[/]"
        else:
            detail = f"{row['targets']} target{'s' if row['targets'] != 1 else ''}"
        name = escape(row["name"].ljust(width))
        console.print(f"{mark} [cyan]{name}[/]  {detail}")
        console.print(f"    [dim]{escape(row['config'] or '')}[/]")
        if row["error"] is not None:
            console.print(f"    [red]{escape(row['error'])}[/]")
    console.print()
    if ignored is not None:
        console.print(
            f"[yellow]warning:[/] {escape(str(ignored))} is ignored: "
            f"{escape(str(named_config_path(DEFAULT_NAME)))} is the default config now."
        )
    elif legacy is not None:
        console.print(
            "[dim]The default config is located from before named configs. "
            "`youpdated configs migrate` moves it into configs/.[/]"
        )
    if unusable is not None:
        console.print(f"[yellow]warning:[/] ${CONFIG_ENV}: {escape(unusable)}")
    console.print(
        f"[dim]Use one with -c NAME, or set ${CONFIG_ENV}. "
        "Create one with `youpdated init NAME`.[/]"
    )
    return 0


def _confirmed(console: Console, args: argparse.Namespace, prompt: str) -> bool:
    """``args.yes``, or the user's answer"""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise ConfigError("there is no terminal to confirm on; pass --yes")
    if _ask(prompt) in ("y", "yes"):
        return True
    console.print("[dim]Canceled.[/]")
    return False


def cmd_configs_remove(args: argparse.Namespace, console: Console) -> int:
    name = check_name(args.name)
    if name == DEFAULT_NAME:
        raise ConfigError(
            "the default config is not removed here. Use `youpdated uninstall` to remove everything"
        )
    path = named_config_path(name)
    if not path.is_file():
        raise ConfigError(f"no config named `{name}`. See `youpdated configs`")
    state_path = named_state_path(name)
    traces = [
        Trace(path, "config", "config file"),
        Trace(state_path, "state", "history database"),
        Trace(lock_path(state_path), "state", "history database lock"),
    ]
    traces = [trace for trace in traces if trace.exists]
    console.print(f"[bold]This deletes config `{escape(name)}`:[/]\n")
    for trace in traces:
        console.print(f"  [yellow]{escape(str(trace.path))}[/] [dim]{trace.note}[/]")
    console.print()
    if args.test:
        console.print("[dim]--test: nothing was deleted.[/]")
        return 0
    if not _confirmed(console, args, f"Delete `{name}` and its history? [y/N] "):
        return 1

    lock = lock_path(state_path)
    # A running check would write its history back
    with state_lock(state_path) if state_path.is_file() else nullcontext():
        _, failed = remove_traces([t for t in traces if t.path != lock])
    # Once released: the lock is taken again as it's deleted
    _, failed_lock = remove_traces([Trace(lock, "state", "history database lock")])
    failed += failed_lock
    for trace, why in failed:
        console.print(f"[red]could not delete[/] {escape(str(trace.path))}: {escape(why)}")
    if failed:
        return 1
    console.print(f"[green]Deleted[/] config `{escape(name)}` and its history.")
    return 0


def cmd_configs_migrate(args: argparse.Namespace, console: Console) -> int:
    moves = [
        (legacy_config_path(), named_config_path(DEFAULT_NAME)),
        (legacy_state_path(), named_state_path(DEFAULT_NAME)),
    ]
    moves = [(old, new) for old, new in moves if old.is_file()]
    if not moves:
        console.print("[dim]Nothing to migrate: the default config already lives in configs/.[/]")
        return 0
    taken = [(old, new) for old, new in moves if new.exists()]
    if taken:
        for old, new in taken:
            console.print(
                f"[red]{escape(str(new))} already exists[/], so {escape(str(old))} can't move "
                "there. Remove or merge one of them first."
            )
        return 1

    console.print("[bold]This moves:[/]\n")
    for old, new in moves:
        console.print(f"  {escape(str(old))}\n  [dim]->[/] {escape(str(new))}")
    console.print(
        "\n[yellow]The desktop apps use the old config location until updated.[/] "
        "Wait for an app release with this version of youpdated before migrating."
    )
    console.print()
    if args.test:
        console.print("[dim]--test: nothing was moved.[/]")
        return 0
    if not _confirmed(console, args, "Move them? [y/N] "):
        return 1

    old_state = legacy_state_path()
    with state_lock(old_state) if old_state.is_file() else nullcontext():
        for old, new in moves:
            new.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, new)
            console.print(f"[green]moved[/] {escape(str(new))}")
    lock_path(old_state).unlink(missing_ok=True)
    return 0


def _error_line(exc: Exception) -> str | None:
    """How a command's failure is shown, or None if it isn't expected"""
    kinds = (
        (ConfigError, "Config error"),
        (crypto.EncryptionError, "Encryption error"),
        (StateError, "History error"),
        (ProxyUnavailable, "Proxy error"),
    )
    for kind, label in kinds:
        if isinstance(exc, kind):
            return f"[red]{label}:[/] {escape(str(exc))}"
    return None


def cmd_check_all(args: argparse.Namespace, console: Console) -> int:
    """`check --all-configs`: each config in order"""
    for flag, given in (
        ("-c", args.config), ("--state", args.state), ("--json", args.json), ("--rss", args.rss)
    ):
        if given:
            raise ConfigError(
                f"--all-configs can't take {flag}: it runs every config in turn. "
                "Pick one with -c NAME for that"
            )
    runs: list[Selected] = []
    if find_config() is not None:
        runs.append(Selected(None, default_state_path(), DEFAULT_NAME))
    runs += [select(name) for name in named()]
    if not runs:
        raise ConfigError(
            "no config file found. Run `youpdated init` to create one at "
            f"{default_config_path()}"
        )

    code = 0
    for selected in runs:
        console.rule(f"[bold]{escape(selected.name or '')}[/]", align="left", style="dim")
        each = argparse.Namespace(**{
            **vars(args),
            "config": selected.config,
            "state": selected.state,
            "config_name": selected.name,
        })
        try:
            code = max(code, cmd_check(each, console))
        except Exception as exc:
            line = _error_line(exc)
            if line is None:
                raise
            console.print(line)
            code = max(code, 1)
    return code


def cmd_uninstall(args: argparse.Namespace, console: Console) -> int:
    traces = find_traces(
        args.config, args.state, keep_config=args.keep_config, keep_app=args.keep_app
    )
    apps = [] if args.keep_app else apptraces.app_locations()
    package = None if args.keep_package else find_package()
    from_source = None
    if package is None and not args.keep_package:
        # no package to remove, explain
        from_source = f"[dim]This copy runs from source ({escape(str(tool_dir()))}), not a package.[/]"

    kept_apps = "\n".join(
        f"[dim]The app itself is kept, at {escape(str(app))}. "
        + ("Drag it to the Trash to remove it." if app.suffix == ".app" else "Delete that folder to remove it.")
        + "[/]"
        for app in apps
    )

    if not traces and package is None:
        console.print("[dim]No Youpdated files found.[/]")
        if from_source:
            console.print(from_source)
        if kept_apps:
            console.print(kept_apps)
        return 0

    console.print("[bold]This will permanently delete:[/]\n")
    for trace in traces:
        console.print(f"  [yellow]{escape(trace.shown)}[/]")
        console.print(f"    [dim]{trace.note}[/]")
    if package is not None:
        console.print(f"  [yellow]youpdated {escape(package.version)}[/]")
        console.print(f"    [dim]the package, with: {escape(package.display)}[/]")
    console.print()
    notes = [kept_apps] if kept_apps else []
    if any(trace.kind == "app" for trace in traces):
        notes.insert(0, "[dim]Quit the desktop app first.[/]")
    if notes:
        console.print("\n".join(notes) + "\n")

    if args.test:
        console.print("[dim]--test: nothing was deleted.[/]")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            console.print(
                "[red]Refusing to delete without confirmation.[/] "
                "Re-run with [bold]--yes[/] (or [bold]--test[/] to preview)."
            )
            return 1
        try:
            answer = input("Delete these? Type 'yes' to confirm: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("yes", "y"):
            console.print("[dim]Canceled. Nothing deleted.[/]")
            return 1

    failed: list[tuple[Trace, str]] = []
    if traces:
        removed, failed = remove_traces(traces)

        for trace in removed:
            console.print(f"[green]removed[/] {escape(trace.shown)}")
        for trace, reason in failed:
            console.print(f"[yellow]kept[/]    {escape(trace.shown)}: {escape(reason)}")

        console.print(
            f"\n[bold]{len(removed)}[/] item(s) removed"
            + (f", [bold]{len(failed)}[/] left in place." if failed else ".")
        )
    done = 1 if failed else 0

    if package is None:
        if from_source:
            console.print(from_source)
        return done

    console.print(f"\nRemoving the package with [bold]{escape(package.display)}[/]")
    error = remove_package(package)
    if error:
        console.print(
            f"[red]The package was not removed:[/] {escape(error)}\n"
            f"To remove it yourself:\n  [bold]{escape(package.display)}[/]"
        )
        return 1
    if package.after_exit:
        console.print(
            f"[green]removing[/] youpdated {escape(package.version)} once this command exits\n"
            f"[dim]If it's still installed after that: {escape(package.display)}[/]"
        )
        return done
    console.print(f"[green]removed[/] youpdated {escape(package.version)}")
    return done


class _Unpadded:
    """renderable without padding spaces"""

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        for index, line in enumerate(console.render_lines(self.renderable, options, pad=False)):
            if index:
                yield Segment.line()
            while line and not line[-1].text.rstrip(" "):
                line.pop()
            if line:
                text, style, control = line.pop()
                line.append(Segment(text.rstrip(" "), style, control))
            yield from line


def _from_line_start(sequence: str) -> Control:
    """carriage return, then an escape sequence Rich has no ControlType for"""
    control = Control()
    # a segment is only zero width when it lists a control code
    control.segment = Segment("\r" + sequence, None, [(ControlType.CARRIAGE_RETURN,)])
    return control


class _ResizeSafeLiveRender(LiveRender):
    """Live render that puts the cursor at the start of its frame"""

    _hidden_until = 0.0

    def hide(self, seconds: float) -> None:
        self._hidden_until = time.monotonic() + seconds

    def set_renderable(self, renderable: RenderableType) -> None:
        super().set_renderable(_Unpadded(renderable))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if time.monotonic() < self._hidden_until:
            return
        # room for about a font size step
        width = options.max_width - max(options.max_width // 8, 4)
        yield _from_line_start("\x1b[?7l")
        yield from super().__rich_console__(console, options.update_width(max(width, 1)))
        up = self.last_render_height - 1
        yield Control(ControlType.CARRIAGE_RETURN, *([(ControlType.CURSOR_UP, up)] if up else []))
        yield _from_line_start("\x1b[?7h")

    def position_cursor(self) -> Control:
        return Control() if self._shape is None else _from_line_start("\x1b[J")

    def restore_cursor(self) -> Control:
        # Live.stop moves down a line before this
        return Control() if self._shape is None else _from_line_start("\x1b[1A\x1b[J")


class _LiveProgress:
    """Progress view of a run"""

    # Targets named in the description before the rest become "+N more"
    _SHOWN = 2

    def __init__(self, console: Console) -> None:
        # Fixed width columns lead so the bar never moves; the target names
        # change length on every update, so they go last and are truncated.
        self._progress = Progress(
            SpinnerColumn(),
            BarColumn(bar_width=20),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[failed]}", style="red"),
            TextColumn(
                "{task.description}",
                style="dim",
                markup=False,
                table_column=Column(ratio=1, no_wrap=True, overflow="ellipsis"),
            ),
            console=console,
            transient=True,
            expand=True,
        )
        # legacy Windows consoles run Rich's control codes, not escape sequences
        self._render = None
        if not console.legacy_windows:
            live = self._progress.live
            self._render = live._live_render = _ResizeSafeLiveRender(live.get_renderable())
        self._resize_handler = None  # the SIGWINCH handler ours replaced
        self._task = None
        self._lock = threading.Lock()
        self._in_flight: dict[tuple[str, str], tuple[str, int]] = {}
        self._failed = 0

    def __enter__(self) -> "_LiveProgress":
        self._progress.start()
        # SIGWINCH is Unix only, and only the main thread may handle signals
        if (
            self._render is not None
            and hasattr(signal, "SIGWINCH")
            and threading.current_thread() is threading.main_thread()
        ):
            self._resize_handler = signal.signal(signal.SIGWINCH, self._resized)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._resize_handler is not None:
            signal.signal(signal.SIGWINCH, self._resize_handler)
        self._progress.stop()

    def _resized(self, *_: object) -> None:
        """Takes the frame down until the window stops changing size"""
        if self._render is not None:
            self._render.hide(0.3)
            self._progress.refresh()

    def _describe(self) -> str:
        """Caller holds the lock"""
        names = [label for label, _ in self._in_flight.values()]
        if not names:
            label = "finishing"
        else:
            label = ", ".join(names[: self._SHOWN])
            if len(names) > self._SHOWN:
                label += f" +{len(names) - self._SHOWN} more"
        return label

    def _failures(self) -> str:
        """Caller holds the lock"""
        return f"{self._failed} failed" if self._failed else ""

    # ProgressReporter

    def run_started(self, total: int) -> None:
        self._task = self._progress.add_task("starting", total=total, failed="")

    def target_started(self, target) -> None:
        entry = (target.source, target.key)
        with self._lock:
            label, count = self._in_flight.get(
                entry, (f"{target.source}:{target.display}", 0)
            )
            self._in_flight[entry] = (label, count + 1)
            description = self._describe()
            failed = self._failures()
        self._advance(description, failed, 0)

    def target_finished(self, target, error) -> None:
        entry = (target.source, target.key)
        with self._lock:
            label, count = self._in_flight.get(entry, ("", 1))
            if count > 1:
                self._in_flight[entry] = (label, count - 1)
            else:
                self._in_flight.pop(entry, None)
            if error is not None:
                self._failed += 1
            description = self._describe()
            failed = self._failures()
        self._advance(description, failed, 1)

    def _advance(self, description: str, failed: str, advance: int) -> None:
        if self._task is None:
            return
        self._progress.update(
            self._task, description=description, failed=failed, advance=advance
        )


def cmd_check(args: argparse.Namespace, console: Console) -> int:
    # JSON goes to stdout, status chatter to stderr.
    status = Console(stderr=True, highlight=False) if args.json else console

    state_path = args.state
    passphrase = None
    if _setup_is_encrypted(find_config(args.config), state_path):
        passphrase = crypto.prompt_passphrase(name=args.config_name)
        # derived while the config is decrypted
        crypto.derive_ahead(state_path, passphrase)

    config: Config = load_config(args.config, passphrase=passphrase)
    if args.fast:
        config.privacy = replace(config.privacy, fast_mode=True)
    if config.privacy.fast_mode:
        status.print(f"[yellow]warning:[/] {escape(FAST_MODE_WARNING)}")
    named = names_itself_privately(config, passphrase is not None)

    since = None
    if args.since:
        try:
            since = parse_since(args.since)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    notify_config = config.notify
    if args.notify_cmd:
        notify_config = replace(
            notify_config,
            commands=notify_config.commands
            + [Command(split_command(cmd, name="--notify-cmd")) for cmd in args.notify_cmd],
        )
    # --all and --no-save report things already seen, they will not send notification by default
    send_notifications = (
        notify_config.enabled
        and not args.no_notify
        and (args.notify or not (args.all or args.no_save))
    )

    _unknown_sources(args.sources)

    if config.privacy.proxy:
        try:
            probe_proxy(config.privacy.proxy, timeout=config.privacy.timeout)
        except ProxyUnavailable as exc:
            if not args.test:
                raise
            status.print(f"[yellow]proxy: unreachable[/]: {escape(str(exc).splitlines()[0])}")
        else:
            if args.verbose or args.test:
                status.print(f"[dim]proxy:  {escape(config.privacy.proxy)} reachable[/]")

    with State(state_path, passphrase=passphrase, expiry=config.expiry) as state:
        if named and not args.test and not _named_ua_confirmed(state):
            if args.json or not sys.stdin.isatty():
                raise ConfigError(NAMED_UA_UNCONFIRMED)
            if not _confirm_named_ua(status):
                status.print("[dim]Canceled. Nothing was sent.[/]")
                return 1
            state.cache_set(*NAMED_UA_CONFIRMED, datetime.now(timezone.utc).isoformat())
        elif named:
            status.print(f"[yellow]warning:[/] {escape(NAMED_UA_WARNING)}")
        with Client(
            config.privacy,
            state,
            test=args.test,
            verbose=args.verbose or args.test,
            use_conditional=not args.all,
            logger=lambda msg: status.print(escape(msg), style="dim"),
            # Probed above, before the history was opened
            proxy_probed=True,
        ) as client:
            if args.verbose or args.test:
                status.print(f"[dim]config: {escape(str(config.path))}[/]")
                status.print(f"[dim]state:  {escape(str(state_path))}[/]")
                status.print(f"[dim]client: {escape(client.describe())}[/]")

            # only for terminal
            show_bar = status.is_terminal and not (args.verbose or args.test)
            with ExitStack() as stack:
                reporter = None
                if show_bar:
                    reporter = stack.enter_context(_LiveProgress(status))
                result = run(
                    config,
                    state,
                    client,
                    only_sources=args.sources,
                    show_all=args.all,
                    since=since,
                    # Recorded below, once the report is out
                    save=False,
                    progress=reporter,
                )

            if args.test:
                if send_notifications:
                    for line in notify.describe(notify_config):
                        status.print(f"[dim]notify: {escape(line)}[/]")
                status.print(
                    f"\n[dim]{len(client.requested_urls)} request(s) planned; "
                    "none sent.[/]"
                )
                # nothing fetched: these are config problems
                if args.json:
                    print(json_out.render(result))
                else:
                    terminal.render_warnings(result.warnings, console)
                    terminal.render_errors(result.errors, console)
                return 2 if args.fail_on_error and result.errors else 0

            # report then record
            if not args.json:
                terminal.render(result, console=console, verbose=args.verbose)
            if args.rss:
                rss_path = Path(args.rss).expanduser()
                try:
                    rss_path.parent.mkdir(parents=True, exist_ok=True)
                    rss_path.write_text(rss_out.render(result), encoding="utf-8")
                except OSError as exc:
                    raise ConfigError(f"--rss: cannot write {rss_path}: {exc}") from exc
                status.print(
                    f"[dim]Wrote {len(result.updates)} entries to {escape(str(rss_path))}[/]"
                )
            if not args.no_save:
                record(state, result, by="cli")

            if send_notifications:
                # save first: a run killed while a slow webhook answers must not notify twice
                state.flush()
                labels = {(t.source, t.key): t.display for t in result.targets}
                # app priorities apply here too, digest doesn't
                failures = notify.send(
                    result.updates, labels, notify_config, client, state.priorities()
                )
                result.errors.extend(failures)
                if not args.json:
                    terminal.render_errors(failures, console)

    if args.json:
        print(json_out.render(result))

    if args.fail_on_error and result.errors:
        return 2
    return 0


# Commands that take -c NAME / $YOUPDATED_CONFIG, with that config's own history
_SELECTING = frozenset({
    "check", "list", "add", "remove", "rm", "import", "scan", "edit",
    "encrypt", "set-encrypted", "decrypt", "set-decrypted",
})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `youpdated` and `youpdated --json` = `check`.
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "check")

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["check"])

    if args.command == "api":
        from . import api

        return api.main(args)

    console = Console(highlight=False)
    try:
        if args.command in _SELECTING and not getattr(args, "all_configs", False):
            selected = select(args.config, getattr(args, "state", None))
            args.config, args.state = selected.config, selected.state
            args.config_name = selected.name
        if args.command == "sources":
            return cmd_sources(console)
        if args.command == "list":
            return cmd_list(args, console)
        if args.command == "add":
            return cmd_add(args, console)
        if args.command in ("remove", "rm"):
            return cmd_edit(args, console)
        if args.command == "import":
            return cmd_import(args, console)
        if args.command == "scan":
            return cmd_scan(args, console)
        if args.command == "edit":
            return cmd_editor(args, console)
        if args.command == "init":
            return cmd_init(args, console)
        if args.command == "uninstall":
            return cmd_uninstall(args, console)
        if args.command in ("encrypt", "set-encrypted"):
            return cmd_encrypt(args, console)
        if args.command in ("decrypt", "set-decrypted"):
            return cmd_decrypt(args, console)
        if args.command == "configs":
            return cmd_configs(args, console)
        if getattr(args, "all_configs", False):
            return cmd_check_all(args, console)
        return cmd_check(args, console)
    except (ConfigError, crypto.EncryptionError, StateError, ProxyUnavailable) as exc:
        console.print(_error_line(exc))
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        return 130
