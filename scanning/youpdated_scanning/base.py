"""What a scanner is and what it finds"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterable, Protocol

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
