"""`youpdated list`: watched targets and history state without fetches"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..sources.base import parse_iso
from .terminal import SOURCE_STYLE, relative_age

# A clean fetch within this window disqualifies 'stale' marking
STALE_GRACE = timedelta(hours=12)
SORTS = ("name", "newest")
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)
NAME_MAX, VERSION_MAX = 40, 32
# A name or version shrinks to this before anything else does
SHRINK_FLOOR = 8
_GAP = 2


def is_stale(last_checked: str | None, failing: bool, now: datetime | None = None) -> bool:
    """If a target's last (& past grace) try failed"""
    if not failing:
        return False
    checked = parse_iso(last_checked)
    return checked is None or checked < (now or datetime.now(timezone.utc)) - STALE_GRACE


def shown(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("highest") or row["latest"] or {}


def published(row: dict[str, Any]) -> datetime | None:
    return parse_iso(shown(row).get("published"))


def sort_rows(rows: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
    """``name`` within each source, ``newest`` across all"""
    if sort == "name":
        order = list(dict.fromkeys(row["source"] for row in rows))
        return sorted(rows, key=lambda r: (order.index(r["source"]), r["display"].casefold()))
    if sort == "newest":
        # undated and never fetched last, in config order
        return sorted(rows, key=lambda r: published(r) or _EARLIEST, reverse=True)
    return rows


def _fit(wants: list[int], room: int, floors: list[int]) -> list[int]:
    """Shrink ``wants`` in proportion to fit ``room``, none below its ``floors``.

    Rich squeezes every no_wrap column evenly when a table is too wide, cutting
    ages and notes to "3…" before long names. Capping the wide columns first avoids that.
    """
    excess = sum(wants) - room
    spare = [max(0, want - floor) for want, floor in zip(wants, floors)]
    if excess <= 0 or not sum(spare):
        return wants
    cuts = [min(s, -(-excess * s // sum(spare))) for s in spare]
    return [want - cut for want, cut in zip(wants, cuts)]


def _cells(row: dict[str, Any]) -> tuple[Text, Text, Text]:
    """Version, age, note cells of ``row``"""
    note = Text()
    if row["stale"]:
        note.append("⚠ failing", style="yellow")
        if row["last_checked"]:
            note.append(f", last ok {relative_age(parse_iso(row['last_checked']))}", style="yellow")
    if row["last_checked"] is None:
        what = "never fetched" if row["stale"] else "not checked yet"
        return Text(what, style="dim"), Text(), note
    item = shown(row)
    if not item:
        return Text("nothing found yet", style="dim"), Text(), note
    if item.get("version"):
        cell = Text(item["version"], style="bold green")
    else:
        cell = Text(item.get("title") or "untitled", style="dim")
    return cell, Text(relative_age(published(row)), style="dim"), note


def render(
    rows: list[dict[str, Any]],
    console: Console,
    *,
    flat: bool,
    total: int,
    last_run: datetime | None,
    last_run_by: str | None,
) -> None:
    """``flat`` drops source groups"""
    if not rows:
        console.print(Text("No watched targets match.", style="dim"))
    else:
        lines: list[list[Text]] = []
        if flat:
            for row in rows:
                source = Text(row["source"], style=SOURCE_STYLE.get(row["source"], "cyan"))
                lines.append([source, Text(row["display"]), *_cells(row)])
        else:
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["source"]] = counts.get(row["source"], 0) + 1
            current = None
            for row in rows:
                if row["source"] != current:
                    current = row["source"]
                    heading = Text(current, style=f"bold {SOURCE_STYLE.get(current, 'cyan')}")
                    heading.append(f" ({counts[current]})", style="dim")
                    lines.append([heading])
                lines.append([Text("  ") + Text(row["display"]), *_cells(row)])

        widths = [
            max((line[i].cell_len for line in lines if i < len(line)), default=0)
            for i in range(5 if flat else 4)
        ]
        if not widths[-1]:
            # no notes, so no trailing gap
            widths.pop()
        name = 1 if flat else 0
        others = sum(widths) - widths[name] - widths[name + 1] + _GAP * (len(widths) - 1)
        # Source headings share the name column, keep them whole
        headings = max((line[0].cell_len for line in lines if len(line) == 1), default=0)
        name_width, version_width = _fit(
            [min(widths[name], NAME_MAX), min(widths[name + 1], VERSION_MAX)],
            console.width - others,
            [max(SHRINK_FLOOR, headings), SHRINK_FLOOR],
        )

        table = Table.grid(padding=(0, _GAP))
        if flat:
            table.add_column(no_wrap=True)
        table.add_column(no_wrap=True, overflow="ellipsis", max_width=name_width)
        table.add_column(no_wrap=True, overflow="ellipsis", max_width=version_width)
        table.add_column(no_wrap=True, justify="right")
        if len(widths) > name + 3:
            table.add_column(no_wrap=True)
        for line in lines:
            table.add_row(*line[: len(widths)])
        console.print(table)
        console.print()

    shown = f"{len(rows)} of {total}" if len(rows) != total else str(total)
    footer = Text(f"{shown} target{'s' if total != 1 else ''}", style="dim")
    if last_run is None:
        footer.append(" · never checked: run `youpdated check`", style="dim")
    else:
        footer.append(f" · last check {relative_age(last_run)}", style="dim")
        if last_run_by:
            footer.append(f" ({last_run_by})", style="dim")
    failing = sum(1 for row in rows if row["stale"])
    if failing:
        footer.append(f" · {failing} failing", style="yellow")
    console.print(footer)
