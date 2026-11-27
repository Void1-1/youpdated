"""macOS apps: Sparkle feeds and App Store apps

Each ``.app`` in ``/Applications`` and ``~/Applications`` (and one folder deep) has a ``Contents/Info.plist``:

- ``Contents/_MASReceipt/receipt`` means it came from the Mac App Store -> ``appstore``, by ``CFBundleIdentifier``
- ``SUFeedURL`` is the app's Sparkle appcast -> ``sparkle``
- ``CFBundleShortVersionString`` (or ``CFBundleVersion``) is the installed version

Some apps set their feed outside the plist. Those are found when the app has written the feed to its defaults
(``~/Library/Preferences/<bundle id>.plist``). A sandboxed app's defaults are in its container, which macOS asks for confirmation before reading, so those are ignored.

iPhone/iPad apps installed on a Mac have a ``Wrapper/`` instead of ``Contents/``, and aren't read yet.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator, Sequence
from xml.parsers.expat import ExpatError

from .base import Installed


def app_dirs() -> list[Path]:
    return [path for path in (Path("/Applications"), Path.home() / "Applications") if path.is_dir()]


def bundles(directory: Path) -> Iterator[Path]:
    """The ``.app``s in ``directory`` and in its folders (``Utilities``)"""
    for child in _children(directory):
        if child.suffix == ".app":
            yield child
        elif child.is_dir() and not child.is_symlink():
            yield from (grandchild for grandchild in _children(child) if grandchild.suffix == ".app")


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def read_plist(path: Path) -> dict[str, Any] | None:
    """A plist file (XML or binary) as a dict"""
    try:
        loaded = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, ExpatError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _text(info: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class MacAppsScanner:
    name: ClassVar[str] = "apps"
    summary: ClassVar[str] = "macOS apps with a Sparkle feed or from the App Store"
    sources: ClassVar[tuple[str, ...]] = ("sparkle", "appstore")

    def __init__(self, dirs: Sequence[Path] | None = None, preferences: Path | None = None) -> None:
        self.dirs, self.preferences = dirs, preferences

    def available(self) -> bool:
        return sys.platform == "darwin" and bool(app_dirs())

    def scan(self) -> Iterable[Installed]:
        preferences = self.preferences or Path.home() / "Library" / "Preferences"
        for directory in app_dirs() if self.dirs is None else self.dirs:
            for app in bundles(directory):
                found = self.read(app, preferences)
                if found is not None:
                    yield found

    def read(self, app: Path, preferences: Path) -> Installed | None:
        info = read_plist(app / "Contents" / "Info.plist")
        if info is None:
            return None
        bundle_id = _text(info, "CFBundleIdentifier")
        name = _text(info, "CFBundleDisplayName", "CFBundleName") or app.stem
        version = _text(info, "CFBundleShortVersionString", "CFBundleVersion")
        where = app.resolve()

        if bundle_id and (app / "Contents" / "_MASReceipt" / "receipt").is_file():
            return Installed("appstore", bundle_id, name, version, where)
        feed = _text(info, "SUFeedURL")
        if feed is None and bundle_id and "/" not in bundle_id:
            defaults = read_plist(preferences / f"{bundle_id}.plist")
            feed = _text(defaults, "SUFeedURL") if defaults else None
        if feed is not None:
            return Installed("sparkle", feed, name, version, where)
        return None
