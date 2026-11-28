"""Homebrew formulae and casks

``brew list --formula --installed-on-request`` gives the formulae asked for. ``brew list --cask`` gives the casks.
``--full-name`` and ``--versions`` can't be combined, names and versions are separate calls.

Formulae and casks from third-party taps (``user/tap/name``) are passed on as they are, and the homebrew source skips them.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import ClassVar, Iterable

from .base import Installed, run

ENV = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "HOMEBREW_NO_ENV_HINTS": "1"}
TIMEOUT = 60


def parse_names(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_versions(text: str) -> dict[str, str]:
    """``name 1.0 1.1`` lines, as name to the last version listed"""
    versions = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) > 1:
            versions[words[0]] = words[-1]
    return versions


def cask_app(caskroom: Path, token: str, version: str | None) -> Path | None:
    """The app a cask installed from Caskroom symlink"""
    if version is None:
        return None
    try:
        links = sorted((caskroom / token / version).glob("*.app"))
    except OSError:
        return None
    for link in links:
        if link.is_symlink():
            return link.resolve()
    return None


class HomebrewScanner:
    name: ClassVar[str] = "homebrew"
    summary: ClassVar[str] = "Homebrew formulae and casks"
    sources: ClassVar[tuple[str, ...]] = ("homebrew",)

    def available(self) -> bool:
        return shutil.which("brew") is not None

    def brew(self, *args: str) -> str:
        """``brew args`` output"""
        return run("brew", *args, timeout=TIMEOUT, env=ENV)

    def scan(self) -> Iterable[Installed]:
        formulae = parse_names(self.brew("list", "--formula", "--installed-on-request", "--full-name"))
        formula_versions = parse_versions(self.brew("list", "--formula", "--versions"))
        # Casks are macOS only
        casks = parse_names(self.brew("list", "--cask", "--full-name")) if sys.platform == "darwin" else []
        cask_versions = parse_versions(self.brew("list", "--cask", "--versions")) if casks else {}
        caskroom = Path(self.brew("--caskroom").strip()) if casks else None

        for full in formulae:
            # `--versions` names a tap's formula without its tap
            name = full.rsplit("/", 1)[-1]
            yield Installed("homebrew", full, name, formula_versions.get(name))
        for full in casks:
            token = full.rsplit("/", 1)[-1]
            version = cask_versions.get(token)
            where = cask_app(caskroom, token, version) if caskroom and "/" not in full else None
            yield Installed("homebrew", f"cask:{full}", token, version, where)
