"""Homebrew formulae and casks

``brew leaves --installed-on-request`` gives the formulae asked. ``brew list --cask --versions`` gives the casks. Both only read the local Cellar and Caskroom.
Run with ``HOMEBREW_NO_AUTO_UPDATE=1``.

Formulae from third-party taps (``user/tap/name``) are skipped by the homebrew source
"""

from __future__ import annotations

import shutil
from typing import ClassVar, Iterable

from .base import Installed

ENV = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1"}


class HomebrewScanner:
    name: ClassVar[str] = "homebrew"
    summary: ClassVar[str] = "Homebrew formulae and casks"
    sources: ClassVar[tuple[str, ...]] = ("homebrew",)

    def available(self) -> bool:
        return shutil.which("brew") is not None

    def scan(self) -> Iterable[Installed]:
        # TODO: Installed("homebrew", name) for formulae and Installed("homebrew", f"cask:{name}", version=...)
        # for casks. A cask app also shows up in /Applications.
        raise NotImplementedError
