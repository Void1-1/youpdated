"""Flatpak apps installed from Flathub

``flatpak list --app --columns=application,version,origin,name`` reads the local installations (system and user) and makes no requests.

Only ``origin`` == ``flathub`` is watchable. Other remotes aren't on Flathub's API.
"""

from __future__ import annotations

import shutil
import sys
from typing import ClassVar, Iterable

from .base import Installed, run

ENV = {"FLATPAK_FANCY_OUTPUT": "0"}
TIMEOUT = 60
REMOTE = "flathub"


def parse_list(text: str) -> list[tuple[str, str | None, str, str | None]]:
    """(app id, version, origin, name) rows"""
    rows = []
    seen = set()
    for line in text.splitlines():
        cells = line.rstrip("\r").split("\t")
        if len(cells) < 3 or not cells[0].strip():
            continue
        app, version, origin = (cell.strip() for cell in cells[:3])
        name = cells[3].strip() if len(cells) > 3 else ""
        if (app, origin) in seen:
            continue
        seen.add((app, origin))
        rows.append((app, version or None, origin, name or None))
    return rows


class FlatpakScanner:
    name: ClassVar[str] = "flatpak"
    summary: ClassVar[str] = "Flatpak apps from Flathub"
    sources: ClassVar[tuple[str, ...]] = ("flathub",)

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("flatpak") is not None

    def flatpak(self, *args: str) -> str:
        return run("flatpak", *args, timeout=TIMEOUT, env=ENV)

    def scan(self) -> Iterable[Installed]:
        listed = self.flatpak("list", "--app", "--columns=application,version,origin,name")
        for app, version, origin, name in parse_list(listed):
            if origin == REMOTE:
                yield Installed("flathub", app, name or app, version)
