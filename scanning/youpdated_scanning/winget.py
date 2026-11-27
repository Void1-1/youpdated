"""Windows programs winget knows, by package id, matched offline against winget's own index

``winget export`` would work, but it refreshes winget's source index.

Reads:
- The programs in Add or Remove Programs, from the registry's ``Uninstall`` keys (both views of HKLM, and HKCU)
- The MSIX packages registered for this user, by package family name
- winget's index of the ``winget`` source, ``Public\\index.db`` in the ``Microsoft.Winget.Source`` package (schema 2)
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Iterable, Protocol

from .base import Installed, ScanError
from .wingetnames import for_index

SOURCE_FAMILY = "Microsoft.Winget.Source_8wekyb3d8bbwe"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
APP_REPOSITORY = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
FRAMEWORK_RE = re.compile(r"^Microsoft\.(?:VCLibs|UI\.Xaml|WindowsAppRuntime|VCRedist|DotNet\.Framework)\.", re.IGNORECASE)
NO_INDEX = (
    "winget's package index isn't on this machine. Install App Installer from the Microsoft Store, "
    "or run `winget source update`"
)


@dataclass(frozen=True)
class Program:
    """An Add or Remove Programs entry"""

    key: str
    name: str
    publisher: str = ""
    version: str | None = None


@dataclass(frozen=True)
class Package:
    """A registered MSIX package"""

    family: str
    version: str | None = None


def parse_package_full_name(full: str) -> Package | None:
    """``Name_Version_Arch_ResourceId_PublisherId`` as its family name, ``Name_PublisherId``, and version"""
    parts = full.split("_")
    if len(parts) != 5 or not parts[0] or not parts[4]:
        return None
    return Package(f"{parts[0]}_{parts[4]}", parts[1] or None)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


class Index:
    """winget's ``index.db`` for the ``winget`` source, opened read-only"""

    def __init__(self, path: Path) -> None:
        try:
            self.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
            meta = dict(self.db.execute("SELECT name, value FROM metadata"))
        except sqlite3.Error as exc:
            raise ScanError(f"cannot read winget's index at {path}: {exc}") from None
        if meta.get("majorVersion") != "2":
            raise ScanError(
                f"winget's index at {path} is schema {meta.get('majorVersion', '?')}, expected 2. Update App Installer"
            )

    def close(self) -> None:
        self.db.close()

    def package(self, package_id: str) -> tuple[str, str] | None:
        """(id, name) of ``package_id``, matched case-insensitively as winget does"""
        row = self.db.execute("SELECT id, name FROM packages WHERE id = ? COLLATE NOCASE", (package_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def match_program(self, program: Program) -> tuple[str, str] | None:
        """The package ``program`` is, if exactly one is"""
        if program.key.endswith(f"_{SOURCE_FAMILY}"):
            # A portable winget installed, its key the package id and the source
            found = self.package(program.key.removesuffix(f"_{SOURCE_FAMILY}"))
            if found:
                return found
        normalized = for_index(program.name, program.publisher)
        names = [normalized.name_with_architecture()] if normalized.architecture else []
        names.append(normalized.name)
        for name in names:
            rows = self.db.execute(
                """
                SELECT p.rowid, p.id, p.name, p.arp_min_version, p.arp_max_version, 1 FROM productcodes2 c
                    JOIN packages p ON p.rowid = c.package WHERE c.productcode = ?
                UNION
                SELECT p.rowid, p.id, p.name, p.arp_min_version, p.arp_max_version, 0 FROM norm_names2 n
                    JOIN norm_publishers2 u ON u.package = n.package
                    JOIN packages p ON p.rowid = n.package
                    WHERE n.norm_name = ? AND u.norm_publisher = ?
                """,
                (program.key.casefold(), name, normalized.publisher),
            ).fetchall()
            if rows:
                return _pick(rows, program.version)
        return None

    def match_package(self, package: Package) -> tuple[str, str] | None:
        rows = self.db.execute(
            "SELECT p.id, p.name FROM pfns2 f JOIN packages p ON p.rowid = f.package WHERE f.pfn = ?",
            (package.family.casefold(),),
        ).fetchall()
        return (rows[0][0], rows[0][1]) if len(rows) == 1 else None


def _pick(rows: list[tuple], version: str | None) -> tuple[str, str] | None:
    """One of several matches: by product code, then by ``version`` in each one's range of ARP versions"""
    candidates = {row[1]: row for row in rows}
    if len(candidates) > 1:
        by_code = {pid: row for pid, row in candidates.items() if row[5]}
        candidates = by_code or candidates
    if len(candidates) > 1 and version:
        installed = _version_key(version)
        within = {
            pid: row for pid, row in candidates.items()
            if row[3] and row[4] and _version_key(row[3]) <= installed <= _version_key(row[4])
        }
        same_minor = {
            pid: row for pid, row in candidates.items() if row[3] and _version_key(row[3])[:2] == installed[:2]
        }
        candidates = within or same_minor or candidates
    if len(candidates) != 1:
        return None
    (row,) = candidates.values()
    return row[1], row[2]


def find_index(roots: Iterable[str]) -> Path | None:
    """The newest ``index.db`` among the source package's install folders"""
    found = []
    for root in roots:
        path = Path(root) / "Public" / "index.db"
        if path.is_file():
            found.append(path)
    return found[-1] if found else None


class Registry(Protocol):
    def programs(self) -> list[Program]: ...
    def package_full_names(self) -> list[str]: ...
    def source_roots(self) -> list[str]: ...


class _Registry:
    """What winget reads from this machine's registry. Windows only"""

    def __init__(self) -> None:
        import winreg

        self.winreg = winreg

    def programs(self) -> list[Program]:
        winreg = self.winreg
        programs: dict[tuple[str, str], Program] = {}
        for hive, view in (
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
            (winreg.HKEY_CURRENT_USER, 0),
        ):
            for key, values in self._subkeys(hive, UNINSTALL, view):
                name = _string(values.get("DisplayName"))
                if (
                    not name or values.get("SystemComponent") == 1 or _string(values.get("ParentKeyName"))
                    or _string(values.get("ReleaseType")) in ("Update", "Hotfix", "Security Update")
                ):
                    continue
                program = Program(
                    key, name, _string(values.get("Publisher")) or "", _string(values.get("DisplayVersion")) or None
                )
                programs.setdefault((key.casefold(), name), program)
        return list(programs.values())

    def package_full_names(self) -> list[str]:
        return [key for key, _ in self._subkeys(self.winreg.HKEY_CURRENT_USER, APP_REPOSITORY, 0, values=False)]

    def source_roots(self) -> list[str]:
        """The source package's install folders, oldest first"""
        roots = []
        for key, values in self._subkeys(self.winreg.HKEY_CURRENT_USER, APP_REPOSITORY, 0, prefix="Microsoft.Winget.Source_"):
            root = _string(values.get("PackageRootFolder"))
            if root:
                roots.append((_version_key(key.split("_")[1]) if key.count("_") >= 4 else (), root))
        return [root for _, root in sorted(roots)]

    def _subkeys(self, hive, path: str, view: int, values: bool = True, prefix: str = ""):
        winreg = self.winreg
        try:
            parent = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            return
        with parent:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                if not name.startswith(prefix):
                    continue
                if not values:
                    yield name, {}
                    continue
                try:
                    with winreg.OpenKey(parent, name) as child:
                        yield name, _values(winreg, child)
                except OSError:
                    continue


def _values(winreg, key) -> dict[str, object]:
    found = {}
    index = 0
    while True:
        try:
            name, value, _ = winreg.EnumValue(key, index)
        except OSError:
            return found
        found[name] = value
        index += 1


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


class WingetScanner:
    name: ClassVar[str] = "winget"
    summary: ClassVar[str] = "Windows programs winget can identify"
    sources: ClassVar[tuple[str, ...]] = ("winget",)

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry

    def available(self) -> bool:
        return sys.platform == "win32" and shutil.which("winget") is not None

    def scan(self) -> Iterable[Installed]:
        registry = self.registry or _Registry()
        path = find_index(registry.source_roots())
        if path is None:
            raise ScanError(NO_INDEX)
        programs, packages = registry.programs(), registry.package_full_names()

        index = Index(path)
        seen: set[str] = set()
        try:
            found: list[Installed] = []
            for program in programs:
                match = index.match_program(program)
                if match:
                    found.append(Installed("winget", match[0], match[1], program.version))
            for full in packages:
                package = parse_package_full_name(full)
                match = index.match_package(package) if package else None
                if match:
                    found.append(Installed("winget", match[0], match[1], package.version))
        finally:
            index.close()
        for item in found:
            if item.entry.casefold() in seen or FRAMEWORK_RE.match(item.entry):
                continue
            seen.add(item.entry.casefold())
            yield item
