"""default terminal report"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from ..models import Target, Update
from ..runner import RunResult

SOURCE_STYLE = {
    "github": "bright_magenta",
    "npm": "bright_red",
    "steam": "bright_blue",
    "itch": "bright_yellow",
    "youtube": "red",
    "browser": "bright_green",
    "feed": "bright_cyan",
}


def relative_age(when: datetime | None) -> str:
    if when is None:
        return "undated"
    delta = datetime.now(timezone.utc) - when
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    for limit, div, unit in (
        (90, 1, "s"),
        (5400, 60, "m"),
        (172800, 3600, "h"),
        (1209600, 86400, "d"),
    ):
        if seconds < limit:
            return f"{seconds // div}{unit} ago"
    return f"{seconds // 604800}w ago"


def _labels(targets: list[Target]) -> dict[tuple[str, str], str]:
    return {(t.source, t.key): t.display for t in targets}


def render(result: RunResult, console: Console | None = None, verbose: bool = False) -> None:
    console = console or Console()

    if result.baseline:
        console.print(
            Panel(
                Text.from_markup(
                    f"Baseline recorded for [bold]{len(result.targets)}[/] target(s), "
                    f"[bold]{result.total_fetched}[/] existing item(s).\n"
                    "Future runs report only what's new. Use [bold]--all[/] to see "
                    "everything now.",
                ),
                title="first run",
                border_style="cyan",
            )
        )
        _render_errors(result, console)
        return

    if not result.updates:
        console.print(
            Text.from_markup(
                f"[dim]No new updates across {len(result.targets)} target(s) "
                f"({result.total_fetched} item(s) checked).[/]"
            )
        )
        _render_errors(result, console)
        return

    labels = _labels(result.targets)
    grouped: dict[str, dict[str, list[Update]]] = defaultdict(lambda: defaultdict(list))
    for update in result.updates:
        grouped[update.source][update.target].append(update)

    for source in sorted(grouped):
        style = SOURCE_STYLE.get(source, "cyan")
        blocks = []
        for target in sorted(grouped[source]):
            label = labels.get((source, target), target)
            heading = Text(label, style="bold")
            if label != target:
                heading.append(f"  ({target})", style="dim")
            blocks.append(heading)

            for update in grouped[source][target]:
                line = Text("  • ")
                # GitHub releases title with tag
                if update.version and update.version != update.title:
                    line.append(f"{update.version}  ", style="bold green")
                    line.append(update.title)
                elif update.version:
                    line.append(update.version, style="bold green")
                else:
                    line.append(update.title)
                line.append(f"   {relative_age(update.published)}", style="dim")
                blocks.append(line)

                if update.body and verbose:
                    body = update.body.replace("\n", " ")
                    blocks.append(Text(f"    {body}", style="dim italic"))
                if update.url:
                    blocks.append(Text(f"    {update.url}", style="dim blue"))
            blocks.append(Text(""))

        if blocks and not blocks[-1].plain:
            blocks.pop()

        console.print(
            Panel(
                Group(*blocks),
                title=f"[{style}]{source}[/]",
                title_align="left",
                border_style=style,
            )
        )

    console.print(
        Text.from_markup(
            f"[bold]{len(result.updates)}[/] new update(s) across "
            f"{len(grouped)} source(s)."
        )
    )
    _render_errors(result, console)


def _render_errors(result: RunResult, console: Console) -> None:
    if not result.errors:
        return
    lines = [
        Text.from_markup(f"[yellow]{e.source}[/] {e.target}: {e.message}")
        for e in result.errors
    ]
    console.print(
        Panel(Group(*lines), title="[yellow]problems[/]", title_align="left", border_style="yellow")
    )
