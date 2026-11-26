"""Windows packages that winget knows, with ids

`winget list` prints a truncated table, it can't be parsed reliably.
``winget export --include-versions`` writes JSON: ``Sources[].Packages[].PackageIdentifier``, where only packages from the ``winget`` source are watchable.

Problems:
- winget refreshes source index on its own schedule, which is a network request this would cause. Check if ``--disable-interactivity`` & cached index avoids.
- Else, maybe read the cached ``index.db`` and match it against the registry's Uninstall keys. Or drop.
"""

from __future__ import annotations

import shutil
import sys
from typing import ClassVar, Iterable

from .base import Installed

# winget export can take a while
TIMEOUT = 120


class WingetScanner:
    name: ClassVar[str] = "winget"
    summary: ClassVar[str] = "Windows packages winget can identify"
    sources: ClassVar[tuple[str, ...]] = ("winget",)

    def available(self) -> bool:
        return sys.platform == "win32" and shutil.which("winget") is not None

    def scan(self) -> Iterable[Installed]:
        # TODO: export to a temp file, read it, delete it.
        raise NotImplementedError
