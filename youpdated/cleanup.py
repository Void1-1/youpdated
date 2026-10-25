"""Finds and removes everything from Youpdated"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_NAME,
    config_dir,
    data_dir,
    default_config_path,
    default_state_path,
    project_config_paths,
    tool_dir,
)

APP_DIR_NAME = "youpdated"
DIST_NAME = "youpdated"


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

    def add(path: str | Path | None, kind: str, note: str) -> None:
        if path is None:
            return
        resolved = Path(path).expanduser()
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        traces.append(Trace(resolved, kind, note))

    if not keep_config:
        add(config_override, "config", "config file (--config)")
        for path in project_config_paths():
            add(path, "config", "project config file")
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


@dataclass
class Package:
    """installed and running copy of Youpdated, and how to remove"""

    version: str
    installer: str # "pipx" | "uv tool" | "uv" | "pip"
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def display(self) -> str:
        join = subprocess.list2cmdline if os.name == "nt" else shlex.join
        return join(self.command)


def find_package() -> Package | None:
    """the installed package this copy runs from, or None for source"""
    try:
        dist = metadata.distribution(DIST_NAME)
    except metadata.PackageNotFoundError:
        return None
    installer = (dist.read_text("INSTALLER") or "").strip()
    if not installer or Path(str(dist.locate_file(""))).resolve() != tool_dir():
        return None

    prefix = Path(sys.prefix)
    if (prefix / "pipx_metadata.json").is_file():
        # pipx keeps apps at $PIPX_HOME/venvs/<name> (named with any --suffix)
        env = {"PIPX_HOME": str(prefix.parent.parent)} if prefix.parent.name == "venvs" else {}
        return Package(dist.version, "pipx", ["pipx", "uninstall", prefix.name], env)
    if (prefix / "uv-receipt.toml").is_file():
        return Package(
            dist.version, "uv tool", ["uv", "tool", "uninstall", prefix.name],
            {"UV_TOOL_DIR": str(prefix.parent)},
        )
    if installer == "uv":
        # environments uv made usually have no pip
        return Package(
            dist.version, "uv", ["uv", "pip", "uninstall", "--python", sys.executable, DIST_NAME]
        )
    return Package(
        dist.version, "pip", [sys.executable, "-m", "pip", "uninstall", "--yes", DIST_NAME]
    )


def remove_package(package: Package) -> str | None:
    """run the package's uninstaller. Returns why it failed, or None once this copy is gone"""
    program = shutil.which(package.command[0])
    if program is None:
        return f"{package.command[0]} not found on PATH"
    try:
        result = subprocess.run(
            [program, *package.command[1:]],
            env={**os.environ, **package.env},
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        return str(exc)
    if result.returncode != 0:
        return f"{package.installer} exited with status {result.returncode}"
    if Path(__file__).exists():
        return f"{package.installer} finished, but the package is still in {tool_dir()}"
    return None


def config_filename() -> str:
    return DEFAULT_CONFIG_NAME
