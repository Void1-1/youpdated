"""Finds and removes everything from Youpdated."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_NAME,
    config_dir,
    data_dir,
    default_config_path,
    default_state_path,
)

APP_DIR_NAME = "youpdated"


@dataclass
class Trace:
    """Something Youpdated-based."""

    path: Path
    kind: str  # "config" | "state" | "directory"
    note: str

    @property
    def exists(self) -> bool:
        return self.path.exists()


def find_traces(
    config_override: str | Path | None = None,
    state_override: str | Path | None = None,
    *,
    keep_config: bool = False,
) -> list[Trace]:
    """Every file and directory this tool may have created, existing now."""
    traces: list[Trace] = []
    seen: set[Path] = set()

    def add(path: Path | None, kind: str, note: str) -> None:
        if path is None:
            return
        resolved = Path(path).expanduser()
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        traces.append(Trace(resolved, kind, note))

    if not keep_config:
        add(config_override, "config", "config file (--config)")
        add(Path.cwd() / "youpdated.yaml", "config", "project config file")
        add(Path.cwd() / "youpdated.yml", "config", "project config file")
        add(default_config_path(), "config", "config file")

    add(state_override, "state", "history database (--state)")
    add(default_state_path(), "state", "history database")

    # Dirs last
    for directory in (config_dir(), data_dir()):
        add(directory, "directory", "application directory (only if empty)")

    return traces


def remove_traces(traces: list[Trace]) -> tuple[list[Trace], list[tuple[Trace, str]]]:
    """Delete the given traces. Returns ``(removed, failed)``.

    Files first, then directories, any additions to dirs that are unexpected donn't let them delete
    """
    removed: list[Trace] = []
    failed: list[tuple[Trace, str]] = []

    files = [t for t in traces if t.kind != "directory"]
    directories = [t for t in traces if t.kind == "directory"]

    for trace in files:
        try:
            if trace.path.is_file():
                trace.path.unlink()
                removed.append(trace)
        except OSError as exc:
            failed.append((trace, str(exc)))

    for trace in directories:
        path = trace.path
        # it must be a youpdated directory and empty.
        if path.name != APP_DIR_NAME:
            failed.append((trace, "not a youpdated directory, aborted"))
            continue
        try:
            if not path.is_dir():
                continue
            leftovers = list(path.iterdir())
            if leftovers:
                failed.append(
                    (trace, f"not empty ({len(leftovers)} other file(s)), aborted")
                )
                continue
            path.rmdir()
            removed.append(trace)
        except OSError as exc:
            failed.append((trace, str(exc)))

    return removed, failed


def package_removal_command() -> str:
    """Command to remove the package."""
    
    return f"{sys.executable} -m pip uninstall youpdated"


def config_filename() -> str:
    return DEFAULT_CONFIG_NAME
