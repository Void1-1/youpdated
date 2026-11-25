"""Finds and removes everything from Youpdated"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from .config import (
    config_dir,
    configs_dir,
    data_dir,
    default_config_path,
    default_state_path,
    history_dir,
    legacy_config_path,
    legacy_state_path,
    project_config_paths,
    tool_dir,
)
from .state import lock_path

APP_DIR_NAME = "youpdated"
# Folders inside the app folder that hold configs and history files
SUB_DIR_NAMES = ("configs", "history")
DIST_NAME = "youpdated"


@dataclass
class Trace:
    """Something Youpdated-based."""

    path: Path | None  # None for a keychain item or registry key
    kind: str  # "config", "state", "directory" or "app"
    note: str
    # For "app": shown instead of the path, and how it's removed
    label: str | None = None
    remover: Callable[[], None] | None = None

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.exists()

    @property
    def shown(self) -> str:
        return self.label or str(self.path)


def find_traces(
    config_override: str | Path | None = None,
    state_override: str | Path | None = None,
    *,
    keep_config: bool = False,
    keep_app: bool = False,
) -> list[Trace]:
    """Every file and directory this tool and the desktop apps may have created, existing now."""
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
        add(legacy_config_path(), "config", "config file (before named configs)")
        for path in _sorted_glob(configs_dir(), "*.yaml"):
            add(path, "config", f"config file `{path.stem}`")

    states = [
        (state_override, "history database (--state)"),
        (default_state_path(), "history database"),
        (legacy_state_path(), "history database (before named configs)"),
        *(
            (path, f"history database `{path.name.removesuffix('-state.sqlite3')}`")
            for path in _sorted_glob(history_dir(), "*-state.sqlite3")
        ),
    ]
    for state, note in states:
        add(state, "state", note)
    for state, _ in states:
        if state is not None:
            add(lock_path(Path(state).expanduser()), "state", "history database lock")

    if not keep_app:
        from . import apptraces

        traces += apptraces.app_traces()

    # Dirs last, the folders inside before the app folders holding them
    for directory in (configs_dir(), history_dir(), config_dir(), data_dir()):
        add(directory, "directory", "application directory (only if empty)")

    return traces


def _sorted_glob(directory: Path, pattern: str) -> list[Path]:
    return sorted(directory.glob(pattern)) if directory.is_dir() else []


def _is_app_dir(path: Path) -> bool:
    """An app folder, or the `configs`/`history` folder inside one"""
    if path.name == APP_DIR_NAME:
        return True
    return path.name in SUB_DIR_NAMES and path.parent.name == APP_DIR_NAME


def remove_traces(traces: list[Trace]) -> tuple[list[Trace], list[tuple[Trace, str]]]:
    """Delete the given traces. Returns ``(removed, failed)``.

    Files first, app traces, then directories, any additions to dirs that are unexpected don't let them delete
    """
    removed: list[Trace] = []
    failed: list[tuple[Trace, str]] = []

    files = [t for t in traces if t.kind in ("config", "state")]
    apps = [t for t in traces if t.kind == "app"]
    directories = [t for t in traces if t.kind == "directory"]

    for trace in files:
        try:
            if trace.path is not None and trace.path.is_file():
                trace.path.unlink()
                removed.append(trace)
        except OSError as exc:
            failed.append((trace, str(exc)))

    for trace in apps:
        if trace.remover is None:
            continue
        try:
            trace.remover()
            removed.append(trace)
        except OSError as exc:
            failed.append((trace, str(exc) or type(exc).__name__))

    for trace in directories:
        path = trace.path
        if path is None:
            continue
        # it must be a youpdated directory and empty.
        if not _is_app_dir(path):
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
    after_exit: bool = False  # run the uninstaller once this process has exited

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
        package = Package(dist.version, "pipx", ["pipx", "uninstall", prefix.name], env)
    elif (prefix / "uv-receipt.toml").is_file():
        package = Package(
            dist.version, "uv tool", ["uv", "tool", "uninstall", prefix.name],
            {"UV_TOOL_DIR": str(prefix.parent)},
        )
    elif installer == "uv":
        # environments uv made usually have no pip
        package = Package(
            dist.version, "uv", ["uv", "pip", "uninstall", "--python", sys.executable, DIST_NAME]
        )
    else:
        package = Package(
            dist.version, "pip", [sys.executable, "-m", "pip", "uninstall", "--yes", DIST_NAME]
        )
    package.after_exit = os.name == "nt"
    return package


# Waits for the process that started it to exit, which closes its stdin, then runs the
# uninstaller in its arguments. Retries give the launcher exe a moment to exit too.
_AFTER_EXIT = """\
import subprocess, sys, time
sys.stdin.read()
for _ in range(10):
    time.sleep(1)
    if subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL) == 0:
        break
"""

_waiting: list[subprocess.Popen[bytes]] = []


def remove_package(package: Package) -> str | None:
    """run the package's uninstaller. Returns why it failed, or None once this copy is gone,
    or for after_exit, once the uninstaller is waiting for this process to exit"""
    program = shutil.which(package.command[0])
    if program is None:
        return f"{package.command[0]} not found on PATH"
    command = [program, *package.command[1:]]
    env = {**os.environ, **package.env}
    if package.after_exit:
        return _remove_after_exit(command, env)
    try:
        result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, check=False)
    except OSError as exc:
        return str(exc)
    if result.returncode != 0:
        return f"{package.installer} exited with status {result.returncode}"
    if Path(__file__).exists():
        return f"{package.installer} finished, but the package is still in {tool_dir()}"
    return None


def _remove_after_exit(command: list[str], env: dict[str, str]) -> str | None:
    # the base interpreter, as pipx and uv tool delete the venv this one runs from
    python = getattr(sys, "_base_executable", None) or sys.executable
    try:
        helper = subprocess.Popen(
            [python, "-I", "-c", _AFTER_EXIT, *command],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            # own hidden console, so closing this window doesn't stop it
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        return str(exc)
    _waiting.append(helper)
    return None

