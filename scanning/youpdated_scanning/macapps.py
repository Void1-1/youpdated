"""macOS apps: Sparkle feeds and App Store apps

Each ``.app`` in ``/Applications`` and ``~/Applications`` (and one folder deep) has a ``Contents/Info.plist``:

- ``SUFeedURL`` is the app's Sparkle appcast -> ``sparkle``
- ``Contents/_MASReceipt/receipt`` means it came from the Mac App Store -> ``appstore``, by ``CFBundleIdentifier``
- ``CFBundleShortVersionString`` (or ``CFBundleVersion``) is the installed version

Some apps set their feed outside the plist. Those are found when the app has written the feed to its defaults (``~/Library/Preferences/<bundle id>.plist``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar, Iterable

from .base import Installed


def app_dirs() -> list[Path]:
    return [path for path in (Path("/Applications"), Path.home() / "Applications") if path.is_dir()]


class MacAppsScanner:
    name: ClassVar[str] = "apps"
    summary: ClassVar[str] = "macOS apps with a Sparkle feed or from the App Store"
    sources: ClassVar[tuple[str, ...]] = ("sparkle", "appstore")

    def available(self) -> bool:
        return sys.platform == "darwin" and bool(app_dirs())

    def scan(self) -> Iterable[Installed]:
        # TODO: plistlib each Info.plist (binary or XML both load). Skip `com.apple.*`.
        raise NotImplementedError
