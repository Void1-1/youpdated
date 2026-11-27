"""What a scanner is and what it finds"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterable, Mapping, Protocol

from youpdated.errors import YoupdatedError


class ScanError(YoupdatedError):
    """A scanner that could run but failed"""


@dataclass(frozen=True)
class Installed:
    """An installed source on this machine, as a Youpdated config entry"""

    source: str
    entry: str
    name: str | None = None
    version: str | None = None
    where: Path | None = None


class Scanner(Protocol):
    name: ClassVar[str]
    summary: ClassVar[str]
    # Youpdated sources this scanner's finds go under
    sources: ClassVar[tuple[str, ...]]

    def available(self) -> bool:
        """If there's anything to scan here: right OS, program on PATH, directory present. No subprocesses"""
        ...

    def scan(self) -> Iterable[Installed]:
        """What's installed. Reads local files and runs local programs only"""
        ...


def run(program: str, *args: str, timeout: float, env: Mapping[str, str] | None = None) -> str:
    """``program args`` output, found on PATH and run with ``env`` added to the environment"""
    command = " ".join((program, *args))
    path = shutil.which(program)
    if path is None:
        raise ScanError(f"{program} is not on PATH")
    try:
        done = subprocess.run(
            [path, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        raise ScanError(f"`{command}` took longer than {timeout:g} seconds") from None
    except OSError as exc:
        raise ScanError(f"cannot run `{command}`: {exc.strerror or exc}") from None
    if done.returncode != 0:
        lines = (done.stderr or done.stdout or "").strip().splitlines()
        raise ScanError(f"`{command}` exited with {done.returncode}" + (f": {lines[-1].strip()}" if lines else ""))
    return done.stdout
