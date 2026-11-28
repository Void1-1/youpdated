"""Installed Steam games, from each library's app manifests

Steam lists libraries in ``steamapps/libraryfolders.vdf`` under its install directory. Each has an ``appmanifest_<appid>.acf`` per app, with ``appid``, ``name``, ``buildid``, ``installdir``.
Both are Valve KeyValues text, parsed here rather than with a dependency.

Manifests are read rather than ``steamapps/common``.
Not every manifest is a game: redistributables, Proton, and the Steam Linux Runtime are ignored.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator, Sequence

from .base import Installed

# Tools Steam installs alongside games
TOOL_APPIDS: frozenset[str] = frozenset({
    "228980",  # Steamworks Common Redistributables
    "1007",  # Steamworks SDK Redist
})
TOOL_NAME_RE = re.compile(r"^(?:Proton(?: |$)|Steam Linux Runtime|Steamworks (?:Common Redistributables|SDK Redist))")

_TOKEN_RE = re.compile(r'\s+|//[^\n]*|"((?:[^"\\]|\\.)*)"|([{}])|([^\s{}"]+)', re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def parse_keyvalues(text: str) -> dict[str, Any]:
    """Valve KeyValues text (``.vdf``/``.acf``) as nested dicts"""
    tokens = _tokens(text)
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for token, kind in tokens:
        if kind == "bare" and token.startswith("[") and token.endswith("]"):
            # A platform conditional, `"key" "value" [$WIN32]`
            continue
        if kind == "{":
            if key is None:
                raise ValueError("`{` without a key before it")
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif kind == "}":
            if key is not None or len(stack) == 1:
                raise ValueError("unexpected `}`")
            stack.pop()
        elif key is None:
            key = token
        else:
            stack[-1][key] = token
            key = None
    if key is not None or len(stack) != 1:
        raise ValueError("ended inside a section")
    return root


def _tokens(text: str) -> Iterator[tuple[str, str]]:
    """(text, kind) pairs, kind being ``{``, ``}``, ``quoted``, or ``bare``"""
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise ValueError(f"unterminated string at character {position}")
        position = match.end()
        quoted, brace, bare = match.groups()
        if brace:
            yield brace, brace
        elif quoted is not None:
            yield re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), quoted, flags=re.DOTALL), "quoted"
        elif bare is not None:
            yield bare, "bare"


def _get(section: Any, key: str) -> Any:
    """``section[key]``, ignoring case"""
    if not isinstance(section, dict):
        return None
    if key in section:
        return section[key]
    lower = key.lower()
    return next((value for name, value in section.items() if name.lower() == lower), None)


def _read_keyvalues(path: Path) -> dict[str, Any] | None:
    try:
        return parse_keyvalues(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def steam_roots() -> list[Path]:
    """Where Steam is installed on this OS"""
    home = Path.home()
    if sys.platform == "darwin":
        candidates = [home / "Library" / "Application Support" / "Steam"]
    elif sys.platform == "win32":
        candidates = [*_registry_roots(), Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam"]
    else:
        candidates = [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            # Flathub's and Snap's Steam
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
            home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
        ]
    roots: list[Path] = []
    for path in candidates:
        if _steamapps(path) is not None and path.resolve() not in roots:
            roots.append(path.resolve())
    return roots


def _registry_roots() -> list[Path]:
    """Steam's install directory as it recorded it, for installs off the default drive"""
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as opened:
                path, _ = winreg.QueryValueEx(opened, value)
        except OSError:
            continue
        if isinstance(path, str) and path:
            found.append(Path(path))
    return found


def _steamapps(library: Path) -> Path | None:
    for name in ("steamapps", "SteamApps"):
        if (library / name).is_dir():
            return library / name
    return None


def libraries(root: Path) -> list[Path]:
    """``root`` and the libraries its ``libraryfolders.vdf`` lists"""
    found = [root]
    steamapps = _steamapps(root)
    listed = _get(_read_keyvalues(steamapps / "libraryfolders.vdf"), "libraryfolders") if steamapps else None
    for key, value in (listed or {}).items() if isinstance(listed, dict) else ():
        if not key.isdigit():
            # `TimeNextStatsReport`, `ContentStatsID` in the old format
            continue
        # Each library is a section with a `path` now. It used to be the value itself
        path = _get(value, "path") if isinstance(value, dict) else value
        if isinstance(path, str) and path:
            library = Path(path)
            if library.resolve() not in found:
                found.append(library.resolve())
    return found


class SteamScanner:
    name: ClassVar[str] = "steam"
    summary: ClassVar[str] = "Steam games in every library"
    sources: ClassVar[tuple[str, ...]] = ("steam",)

    def __init__(self, roots: Sequence[Path] | None = None) -> None:
        self.roots = roots

    def available(self) -> bool:
        return bool(steam_roots() if self.roots is None else self.roots)

    def scan(self) -> Iterable[Installed]:
        seen: set[str] = set()
        libraries_seen: list[Path] = []
        for root in steam_roots() if self.roots is None else self.roots:
            for library in libraries(root):
                if library in libraries_seen:
                    continue
                libraries_seen.append(library)
                steamapps = _steamapps(library)
                if steamapps is None:
                    continue
                for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                    found = self.read(manifest, steamapps)
                    if found is not None and found.entry not in seen:
                        seen.add(found.entry)
                        yield found

    def read(self, manifest: Path, steamapps: Path) -> Installed | None:
        app = _get(_read_keyvalues(manifest), "AppState")
        appid, name = _get(app, "appid"), _get(app, "name")
        if not isinstance(appid, str) or not appid.isdigit():
            return None
        name = name.strip() if isinstance(name, str) and name.strip() else None
        if appid in TOOL_APPIDS or (name and TOOL_NAME_RE.match(name)):
            return None
        build = _get(app, "buildid")
        installdir = _get(app, "installdir")
        where = (steamapps / "common" / installdir).resolve() if isinstance(installdir, str) and installdir else None
        return Installed("steam", appid, name, build if isinstance(build, str) and build != "0" else None, where)
