from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from . import __version__
from .cleanup import find_traces, package_removal_command, remove_traces
from .config import (
    EXAMPLE_CONFIG,
    Config,
    ConfigError,
    default_config_path,
    default_state_path,
    load_config,
)
from .http import Client
from .registry import all_sources
from .render import json_out, rss_out, terminal
from .runner import parse_since, run
from .state import State


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

    sub.add_parser("sources", help="list available sources")

    init = sub.add_parser("init", help="write a starter config file")
    init.add_argument("-c", "--config", help="where to write it")
    init.add_argument("-f", "--force", action="store_true", help="overwrite an existing file")

    uninstall = sub.add_parser(
        "uninstall",
        help="remove all Youpdated files",
    )
    uninstall.add_argument("-c", "--config", help="also remove this config file")
    uninstall.add_argument("--state", help="also remove this state database")
    uninstall.add_argument(
        "--keep-config", action="store_true", help="delete history but keep config files"
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
    for name, source in sorted(all_sources().items()):
        console.print(f"  [cyan]{name:<10}[/] {getattr(source, 'summary', '')}")
    console.print("\n[dim]Add entries under `sources:` in your config.[/]")
    return 0


def cmd_init(args: argparse.Namespace, console: Console) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    if path.exists() and not args.force:
        console.print(f"[yellow]{path} already exists.[/] Use --force to overwrite.")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    console.print(f"Wrote starter config to [bold]{path}[/]")
    console.print("[dim]Edit, then run `youpdated check`.[/]")
    return 0


def cmd_uninstall(args: argparse.Namespace, console: Console) -> int:
    traces = find_traces(args.config, args.state, keep_config=args.keep_config)

    if not traces:
        console.print("[dim]No Youpdated files found.[/]")
        console.print(f"\nTo remove the package itself:\n  [bold]{package_removal_command()}[/]")
        return 0

    console.print("[bold]This will permanently delete:[/]\n")
    for trace in traces:
        console.print(f"  [yellow]{escape(str(trace.path))}[/]")
        console.print(f"    [dim]{trace.note}[/]")
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

    removed, failed = remove_traces(traces)

    for trace in removed:
        console.print(f"[green]removed[/] {escape(str(trace.path))}")
    for trace, reason in failed:
        console.print(f"[yellow]kept[/]    {escape(str(trace.path))} — {escape(reason)}")

    console.print(
        f"\n[bold]{len(removed)}[/] item(s) removed"
        + (f", [bold]{len(failed)}[/] left in place." if failed else ".")
    )
    console.print(
        "\nRemoved all written files. To remove the package itself:\n"
        f"  [bold]{package_removal_command()}[/]"
    )
    return 0


def cmd_check(args: argparse.Namespace, console: Console) -> int:
    # JSON goes to stdout, status chatter to stderr.
    status = Console(stderr=True) if args.json else console

    config: Config = load_config(args.config)

    since = None
    if args.since:
        try:
            since = parse_since(args.since)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    unknown = set(args.sources or []) - set(all_sources())
    if unknown:
        raise ConfigError(
            f"unknown source(s): {', '.join(sorted(unknown))}. "
            "Run `youpdated sources` to see the list."
        )

    state_path = Path(args.state).expanduser() if args.state else default_state_path()

    with State(state_path) as state:
        with Client(
            config.privacy,
            state,
            test=args.test,
            verbose=args.verbose or args.test,
            use_conditional=not args.all,
            logger=lambda msg: status.print(escape(msg), style="dim", highlight=False),
        ) as client:
            if args.verbose or args.test:
                status.print(f"[dim]config: {config.path}[/]")
                status.print(f"[dim]state:  {state_path}[/]")
                status.print(f"[dim]client: {client.describe()}[/]")

            result = run(
                config,
                state,
                client,
                only_sources=args.sources,
                show_all=args.all,
                since=since,
                save=not (args.no_save or args.test),
            )

            if args.test:
                status.print(
                    f"\n[dim]{len(client.requested_urls)} request(s) planned; "
                    "none sent.[/]"
                )
                return 0

    if args.json:
        print(json_out.render(result))
    else:
        terminal.render(result, console=console, verbose=args.verbose)

    if args.rss:
        rss_path = Path(args.rss).expanduser()
        rss_path.parent.mkdir(parents=True, exist_ok=True)
        rss_path.write_text(rss_out.render(result), encoding="utf-8")
        status.print(f"[dim]Wrote {len(result.updates)} entries to {rss_path}[/]")

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

    console = Console()
    try:
        if args.command == "sources":
            return cmd_sources(console)
        if args.command == "init":
            return cmd_init(args, console)
        if args.command == "uninstall":
            return cmd_uninstall(args, console)
        return cmd_check(args, console)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        return 130
