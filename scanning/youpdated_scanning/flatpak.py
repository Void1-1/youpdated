"""Flatpak apps installed from Flathub

``flatpak list --app --columns=application,version,origin`` reads the local installations (system and user).
Only ``origin`` == ``flathub`` is watchable; other remotes aren't on Flathub's API.
"""

from __future__ import annotations

import shutil
import sys
from typing import ClassVar, Iterable

from .base import Installed


class FlatpakScanner:
    name: ClassVar[str] = "flatpak"
    summary: ClassVar[str] = "Flatpak apps from Flathub"
    sources: ClassVar[tuple[str, ...]] = ("flathub",)

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("flatpak") is not None

    def scan(self) -> Iterable[Installed]:
        # TODO: tab-separated rows; Installed("flathub", application, version=version)
        raise NotImplementedError
