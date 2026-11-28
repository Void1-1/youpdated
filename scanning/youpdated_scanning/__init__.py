"""Find what's installed on this machine, for `youpdated scan`"""

from __future__ import annotations

__version__ = "0.1.0"

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .base import Installed, ScanError, Scanner
from .flatpak import FlatpakScanner
from .homebrew import HomebrewScanner
from .macapps import MacAppsScanner
from .steam import SteamScanner
from .winget import WingetScanner

if TYPE_CHECKING:
    from youpdated.importers import Found

__all__ = ["Installed", "SCANNERS", "ScanError", "Scanner", "__version__", "scan"]

# In the order they run
SCANNERS: dict[str, Scanner] = {
    scanner.name: scanner
    for scanner in (
        SteamScanner(), HomebrewScanner(), WingetScanner(), FlatpakScanner(), MacAppsScanner(),
    )
}


def scan(found: Found, only: Iterable[str] | None = None) -> list[Installed]:
    """Run the available scanners (or ``only``) into ``found``. Returns everything found, versions included"""
    chosen = list(SCANNERS) if only is None else list(only)
    unknown = [name for name in chosen if name not in SCANNERS]
    if unknown:
        raise ValueError(f"no scanner named {', '.join(unknown)}; expected one of {', '.join(SCANNERS)}")
    # SCANNERS' order, whatever order --only gave
    names = [name for name in SCANNERS if name in chosen]

    installed: list[Installed] = []
    claimed: set[Path] = set()
    for name in names:
        scanner = SCANNERS[name]
        if not scanner.available():
            continue
        try:
            finds = list(scanner.scan())
        except ScanError as exc:
            found.warn(f"{name}: {exc}")
            continue
        except Exception as exc:
            found.warn(f"{name} failed: {type(exc).__name__}: {exc}")
            continue
        for item in finds:
            if item.where is not None:
                if item.where in claimed:
                    continue
                claimed.add(item.where)
            found.add(item.source, item.entry)
            installed.append(item)
    return installed
