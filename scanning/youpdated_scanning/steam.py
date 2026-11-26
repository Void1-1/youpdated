"""Installed Steam games, from each library's app manifests

Steam lists libraries in ``steamapps/libraryfolders.vdf`` under its install directory. Each has an ``appmanifest_<appid>.acf`` per app, with ``appid``, ``name``, ``buildid``, ``StateFlags``.
Both are Valve KeyValues text, parsed here rather than with a dependency.

Not every manifest is a game: redistributables, Proton, and the Steam Linux Runtime are apps too and ignored.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import ClassVar, Iterable

from .base import Installed

# Tools Steam installs alongside games
# TODO: Steamworks Common Redistributables (228980), Proton versions, Steam Linux Runtime, etc.
TOOL_APPIDS: frozenset[str] = frozenset({"228980"})


def steam_roots() -> list[Path]:
    """Where Steam may be installed on this OS"""
    home = Path.home()
    if sys.platform == "darwin":
        candidates = [home / "Library" / "Application Support" / "Steam"]
    elif sys.platform == "win32":
        # TODO: HKCU\Software\Valve\Steam `SteamPath` first, for installs off the default drive
        candidates = [Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam"]
    else:
        candidates = [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            # Flathub's Steam
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]
    return [path for path in candidates if (path / "steamapps").is_dir()]


def parse_keyvalues(text: str) -> dict:
    """Valve KeyValues text (``.vdf``/``.acf``) as nested dicts"""
    # TODO: quoted keys, values, nested `{ }`, `//` comments, `\"` escapes
    raise NotImplementedError


class SteamScanner:
    name: ClassVar[str] = "steam"
    summary: ClassVar[str] = "Steam games in every library"
    sources: ClassVar[tuple[str, ...]] = ("steam",)

    def available(self) -> bool:
        return bool(steam_roots())

    def scan(self) -> Iterable[Installed]:
        # TODO: for each root, read libraryfolders.vdf for library paths (root included), then each steamapps/appmanifest_*.acf.
        # TODO: Yield Installed("steam", appid, name, buildid, manifest path), skipping TOOL_APPIDS and duplicate appids
        raise NotImplementedError
