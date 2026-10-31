from __future__ import annotations

import argparse
import difflib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

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

from . import __version__, crypto, notify
from .cleanup import Trace, find_package, find_traces, remove_package, remove_traces
from .config import (
    EXAMPLE_CONFIG,
    Config,
    ConfigError,
    PrivacyConfig,
    default_config_path,
    default_state_path,
    find_config,
    load_config,
    parse_config_text,
    read_config_text,
    require_config,
    split_command,
    tool_dir,
)
from .edit import add_to_config, guess_source, remove_from_config, tidy_config, write_config
from .http import Client, ProxyUnavailable, probe_proxy
from .importers import READERS, Found, read_file
from .models import Target
from .registry import all_aliases, all_sources, get_source
from .render import json_out, rss_out, terminal
from .runner import build_targets, parse_since, record, rule_warnings, run
from .sources.base import Source
from .state import State, StateError, state_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youpdated",
        description="Collect updates for the games, apps, and packages you follow.",
    )
    parser.add_argument("--version", action="version", version=f"youpdated {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="fetch sources and report what's new (default)")
    check.add_argument("-c", "--config", help="path to a config file")
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
    check.add_argument("--state", help="path to the state database")
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

    for name, aliases, help_text, verb, extra in (
        ("add", [], "start watching something", "watch", ""),
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
        edit.add_argument("-c", "--config", help="path to a config file")
        edit.add_argument("--test", action="store_true", help="show changes without writing")
        if name == "remove":
            edit.add_argument(
                "--wipe", action="store_true",
                help="also delete full history",
            )
            edit.add_argument("--state", help="path to the state database")

    imports = sub.add_parser(
        "import", help="watch dependencies from a package.json, requirements.txt, or Brewfile",
    )
    imports.add_argument(
        "files", nargs="+", metavar="FILE",
        help="package.json, requirements.txt, or Brewfile",
    )
    imports.add_argument(
        "--format", choices=list(READERS),
        help="read each FILE as this when not named",
    )
    imports.add_argument("-c", "--config", help="path to a config file")
    imports.add_argument("--test", action="store_true", help="show changes without writing")

    editor = sub.add_parser("edit", help="open the config in your editor")
    editor.add_argument("-c", "--config", help="path to a config file")

    init = sub.add_parser("init", help="write a starter config file")
    init.add_argument("-c", "--config", help="where to write it")
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
        crypt.add_argument("-c", "--config", help="path to a config file")
        crypt.add_argument("--state", help="path to the state database")

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
        "--test", action="store_true", help="list removals without deleting"
    )
    uninstall.add_argument(
        "-y", "--yes", action="store_true", help="skip confirmation"
    )

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


def _probe_urls(source: Source | None, targets: list[Target], state: State) -> set[str]:
    if source is None:
        return set()
    with Client(PrivacyConfig(), state, test=True) as client:
        for target in targets:
            try:
                list(source.fetch(target, client))
            except Exception: # a source that needs an answer to continue stops early
                pass
        return set(client.requested_urls)


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


def cmd_edit(
    args: argparse.Namespace, console: Console, plan: list[tuple[str, list[str]]] | None = None
) -> int:
    """`add`/`remove`, or `import` with the ``plan`` it read"""
    adding = args.command in ("add", "import")
    wipe = getattr(args, "wipe", False)
    if plan is None:
        plan = _edit_plan(args.command, args.words)
    path = require_config(args.config)
    state_path = Path(args.state).expanduser() if wipe and args.state else default_state_path()
    config_locked = crypto.is_encrypted_file(path)
    state_locked = wipe and state_path.is_file() and crypto.is_encrypted_file(state_path)
    passphrase = crypto.prompt_passphrase() if config_locked or state_locked else None

    text = edited = read_config_text(path, passphrase)
    changes = []
    for source, entries in plan:
        change = (add_to_config if adding else remove_from_config)(edited, source, entries)
        edited = change.text
        changes.append(change)

    for change in changes:
        if adding and change.skipped:
            # one line per source, an import can skip a lot
            console.print(
                f"[dim]Already watching {escape(', '.join(change.skipped))} "
                f"under {escape(change.section)}[/]"
            )
        elif not adding:
            for entry in change.skipped:
                console.print(
                    f"[yellow]Not watching {escape(entry)}[/] under {escape(change.section)}"
                )
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
                urls = _probe_urls(source, change.dropped, state)
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

    for what, why in found.skipped:
        console.print(f"[yellow]Skipped[/] {escape(what)} [dim]: {escape(why)}[/]")
    for warning in found.warnings:
        console.print(f"[yellow]Warning:[/] {escape(warning)}")
    if not found.entries:
        console.print("[dim]Nothing to watch in these files.[/]")
        return 0
    return cmd_edit(args, console, list(found.entries.items()))


# An editor returning before this with nothing changed didn't wait for the file
_QUICK_EDITOR = 2.0
_DISCARDED = "[dim]Discarded. Nothing was written.[/]"
_WHEN_SAVED = "Press Enter once you've saved, or Ctrl-C to discard: "
_SET_EDITOR = "Set `editor:` in the config, or $VISUAL or $EDITOR, to the one you use."


def _editor_command(configured: str | None = None) -> tuple[list[str], bool]:
    """`editor:`, $VISUAL, $EDITOR, then system default"""
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
    command = split_command(value, name="`editor`")
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
        config = parse_config_text(text, path)
    except ConfigError as exc:
        return [str(exc)], []
    _, errors = build_targets(config)
    return [f"{e.source}: {e.message}" for e in errors], rule_warnings(config)


def _loads(text: str, path: Path) -> Config | None:
    try:
        return parse_config_text(text, path)
    except ConfigError:
        return None


def cmd_editor(args: argparse.Namespace, console: Console) -> int:
    """`edit`: the config through a copy, checked, then written back"""
    path = require_config(args.config)
    locked = crypto.is_encrypted_file(path)
    passphrase = crypto.prompt_passphrase() if locked else None
    before = path.read_bytes()
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
            console.print("[dim]Cancelled. Nothing was decrypted to disk.[/]")
            return 1

    with tempfile.TemporaryDirectory(
        prefix="youpdated-", dir=scratch, ignore_cleanup_errors=True
    ) as folder:
        # config's name, so its highlighted as YAML
        draft = Path(folder) / path.name
        crypto.write_private(draft, original.encode("utf-8"))
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
            if not problems:
                break
            for problem in problems:
                console.print(f"[red]Config error:[/] {escape(problem)}")
            choice = _after_problems(can_save=True)
            if choice == "s":
                break
            if choice == "d":
                console.print(_DISCARDED)
                return 1

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
    return 0


def cmd_init(args: argparse.Namespace, console: Console) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    if path.exists() and not args.force:
        console.print(f"[yellow]{escape(str(path))} already exists.[/] Use --force to overwrite.")
        return 1

    if not args.encrypt:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        console.print(f"Wrote starter config to [bold]{escape(str(path))}[/]")
        console.print("[dim]Change it with `youpdated edit` or `youpdated add`, then run `youpdated check`.[/]")
        return 0

    # Encrypted from the first byte:
    # there is no plaintext copy left in free space like `init` then `encrypt`
    crypto.require_backend()
    console.print(
        "[red]There is no recovery if you forget this passphrase.[/] "
        "The config and history will be the only copy.\n"
    )
    passphrase = crypto.prompt_passphrase("New passphrase: ", confirm=True)
    crypto.write_private(path, crypto.encrypt(EXAMPLE_CONFIG.encode("utf-8"), passphrase))

    console.print(f"Wrote encrypted starter config to [bold]{escape(str(path))}[/]")
    console.print(
        "[dim]Use `youpdated add` and `youpdated rm` to change what it watches, and "
        "`youpdated edit` for anything else.[/]"
    )
    console.print(
        f"[dim]Every command now asks for the passphrase. Set ${crypto.PASSPHRASE_ENV} "
        "for unattended runs.[/]"
    )
    return 0


def _crypt_paths(args: argparse.Namespace) -> tuple[Path | None, Path]:
    """The config and state files used"""
    config_path = find_config(args.config)
    state_path = Path(args.state).expanduser() if args.state else default_state_path()
    return config_path, state_path


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

    passphrase = crypto.prompt_passphrase("New passphrase: ", confirm=not locked)

    for path in locked:
        crypto.decrypt(path.read_bytes(), passphrase)

    # A running check would write its own copy over converted state
    with state_lock(state_path):
        for path in todo:
            crypto.write_private(path, crypto.encrypt(path.read_bytes(), passphrase))
            console.print(f"[green]encrypted[/] {escape(str(path))}")

    console.print(
        "\n[dim]Every command now asks for the passphrase. "
        f"Set ${crypto.PASSPHRASE_ENV} for unattended runs.[/]"
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

    passphrase = crypto.prompt_passphrase()
    with state_lock(state_path):
        # Decrypt everything before writing
        plain = [(path, crypto.decrypt(path.read_bytes(), passphrase)) for path in todo]
        for path, payload in plain:
            crypto.write_private(path, payload)
            console.print(f"[green]decrypted[/] {escape(str(path))}")
    return 0


def cmd_uninstall(args: argparse.Namespace, console: Console) -> int:
    traces = find_traces(args.config, args.state, keep_config=args.keep_config)
    package = None if args.keep_package else find_package()
    from_source = None
    if package is None and not args.keep_package:
        # no package to remove, explain
        from_source = f"[dim]This copy runs from source ({escape(str(tool_dir()))}), not a package.[/]"

    if not traces and package is None:
        console.print("[dim]No Youpdated files found.[/]")
        if from_source:
            console.print(from_source)
        return 0

    console.print("[bold]This will permanently delete:[/]\n")
    for trace in traces:
        console.print(f"  [yellow]{escape(str(trace.path))}[/]")
        console.print(f"    [dim]{trace.note}[/]")
    if package is not None:
        console.print(f"  [yellow]youpdated {escape(package.version)}[/]")
        console.print(f"    [dim]the package, with: {escape(package.display)}[/]")
    console.print()

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
            console.print("[dim]Cancelled. Nothing deleted.[/]")
            return 1

    failed: list[tuple[Trace, str]] = []
    if traces:
        removed, failed = remove_traces(traces)

        for trace in removed:
            console.print(f"[green]removed[/] {escape(str(trace.path))}")
        for trace, reason in failed:
            console.print(f"[yellow]kept[/]    {escape(str(trace.path))}: {escape(reason)}")

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

    state_path = Path(args.state).expanduser() if args.state else default_state_path()
    passphrase = None
    if _setup_is_encrypted(find_config(args.config), state_path):
        passphrase = crypto.prompt_passphrase()

    config: Config = load_config(args.config, passphrase=passphrase)

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
            + [split_command(cmd, name="--notify-cmd") for cmd in args.notify_cmd],
        )
    # --all and --no-save report things already seen, they will not send notification by default
    send_notifications = (
        notify_config.enabled
        and not args.no_notify
        and (args.notify or not (args.all or args.no_save))
    )

    unknown = set(args.sources or []) - set(all_sources()) - set(all_aliases())
    if unknown:
        raise ConfigError(
            f"unknown source(s): {', '.join(sorted(unknown))}. "
            "Run `youpdated sources` to see the list."
        )

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
        with Client(
            config.privacy,
            state,
            test=args.test,
            verbose=args.verbose or args.test,
            use_conditional=not args.all,
            logger=lambda msg: status.print(escape(msg), style="dim"),
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
                record(state, result)

            if send_notifications:
                # save first: a run killed while a slow webhook answers must not notify twice
                state.flush()
                labels = {(t.source, t.key): t.display for t in result.targets}
                failures = notify.send(result.updates, labels, notify_config, client)
                result.errors.extend(failures)
                if not args.json:
                    terminal.render_errors(failures, console)

    if args.json:
        print(json_out.render(result))

    if args.fail_on_error and result.errors:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `youpdated` and `youpdated --json` = `check`.
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "check")

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["check"])

    console = Console(highlight=False)
    try:
        if args.command == "sources":
            return cmd_sources(console)
        if args.command in ("add", "remove", "rm"):
            return cmd_edit(args, console)
        if args.command == "import":
            return cmd_import(args, console)
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
        return cmd_check(args, console)
    except ConfigError as exc:
        # escape(): messages carry paths and `pip install youpdated[extra]` hints
        console.print(f"[red]Config error:[/] {escape(str(exc))}")
        return 1
    except crypto.EncryptionError as exc:
        console.print(f"[red]Encryption error:[/] {escape(str(exc))}")
        return 1
    except StateError as exc:
        console.print(f"[red]History error:[/] {escape(str(exc))}")
        return 1
    except ProxyUnavailable as exc:
        console.print(f"[red]Proxy error:[/] {escape(str(exc))}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        return 130
