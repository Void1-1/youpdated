"""The winget scanner, against a Windows 11 machine's registry and winget's index"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from youpdated.importers import Found
from youpdated_scanning import ScanError
from youpdated_scanning.winget import (
    APP_REPOSITORY, UNINSTALL, Index, Program, WingetScanner, _pick, _Registry, find_index, parse_package_full_name,
)
from youpdated_scanning.wingetnames import for_index, normalize

FIXTURES = Path(__file__).parent / "fixtures" / "winget"


class FakeWinreg:
    """The parts of ``winreg`` the registry reader uses, over ``keys``: {(hive, path): {subkey: {value: data}}}.
    HKLM's 32-bit view is its WOW6432Node, as on 64-bit Windows"""

    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0x20019, 0x0100, 0x0200

    class Key:
        def __init__(self, children: dict, values: dict) -> None:
            self.children, self.values = children, values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self, keys: dict[tuple[str, str], dict[str, dict]]) -> None:
        self.keys = {(hive, path.casefold()): children for (hive, path), children in keys.items()}

    def OpenKey(self, key, sub_key, reserved=0, access=0):  # noqa: N802
        if isinstance(key, FakeWinreg.Key):
            if sub_key not in key.children:
                raise FileNotFoundError(sub_key)
            return FakeWinreg.Key({}, key.children[sub_key])
        if key == self.HKEY_LOCAL_MACHINE and access & self.KEY_WOW64_32KEY:
            sub_key = sub_key.replace("Software\\", "Software\\WOW6432Node\\", 1)
        children = self.keys.get((key, sub_key.casefold()))
        if children is None:
            raise FileNotFoundError(sub_key)
        return FakeWinreg.Key(children, {})

    def EnumKey(self, key, index):  # noqa: N802
        names = list(key.children)
        if index >= len(names):
            raise OSError("no more keys")
        return names[index]

    def EnumValue(self, key, index):  # noqa: N802
        items = list(key.values.items())
        if index >= len(items):
            raise OSError("no more values")
        name, value = items[index]
        return name, value, 1


SOURCE_KEY = "Microsoft.Winget.Source_2026.1126.2410.27_neutral__8wekyb3d8bbwe"


def registry(monkeypatch, root: Path | None, uninstall=None, packages=None) -> _Registry:
    """The machine in installed.json as the registry reader sees it, with winget's source package at ``root``"""
    doc = json.loads((FIXTURES / "installed.json").read_text(encoding="utf-8"))
    keys: dict[tuple[str, str], dict[str, dict]] = {}
    for entry in doc["uninstall"] if uninstall is None else uninstall:
        hive, path = entry["root"].split(":\\", 1)
        values = {
            name: entry[name] for name in ("DisplayName", "Publisher", "DisplayVersion", "SystemComponent",
                                          "ParentKeyName", "ReleaseType")
            if entry.get(name) is not None
        }
        keys.setdefault((hive, path), {})[entry["key"]] = values
    repository = keys.setdefault(("HKCU", APP_REPOSITORY), {})
    for full in doc["packages"] if packages is None else packages:
        repository[full] = {}
    if root is not None:
        repository[SOURCE_KEY] = {"PackageRootFolder": str(root)}
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg(keys))
    return _Registry()


@pytest.fixture
def root(tmp_path):
    """The source package's folder, as winget installs it"""
    root = tmp_path / "Microsoft.Winget.Source_2026.1126.2410.27_neutral__8wekyb3d8bbwe"
    (root / "Public").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "index.db", root / "Public" / "index.db")
    return root


@pytest.fixture
def scan(monkeypatch):
    def scan(root: Path | None, uninstall=None, packages=None) -> list:
        return list(WingetScanner(registry(monkeypatch, root, uninstall, packages)).scan())

    return scan


def test_what_winget_export_found_less_runtimes(root, scan):
    finds = {item.entry: item for item in scan(root)}
    assert set(finds) == {
        # by product code
        "7zip.7zip", "Microsoft.EdgeWebView2Runtime", "Microsoft.OneDrive", "Python.Python.3.14",
        # by name and publisher
        "Git.Git", "Spice.SpiceWebDAVd", "SoftwareFreedomConservancy.QEMUGuestAgent",
        # a portable winget installed
        "jqlang.jq",
        # by package family
        "Microsoft.AppInstaller", "Microsoft.Outlook",
    }
    assert (finds["7zip.7zip"].name, finds["7zip.7zip"].version) == ("7-Zip", "26.06")
    assert finds["Microsoft.Outlook"].version == "1.2026.1026.100"


def test_every_find_is_one_the_winget_source_reads(root, scan):
    found = Found()
    for item in scan(root):
        found.add(item.source, item.entry)
    assert len(found.entries["winget"]) == 10
    assert not found.skipped


def test_the_name_with_its_architecture_first():
    index = Index(FIXTURES / "index.db")
    # winget indexes 7-Zip as `7zip` and `7zip(X64)`. This is the arm64 build, so only the plain name matches
    assert index.match_program(Program("unknown", "7-Zip 26.06 (arm64)", "Igor Pavlov")) == ("7zip.7zip", "7-Zip")
    assert index.match_program(Program("unknown", "Some Other Tool (x64)", "Igor Pavlov")) is None
    index.close()


def test_pythons_share_a_name_so_the_version_decides():
    index = Index(FIXTURES / "index.db")
    python = lambda version: index.match_program(Program("unknown", f"Python {version} (64-bit)", "Python Software Foundation", f"{version}150.0"))
    assert python("3.13.17") == ("Python.Python.3.13", "Python 3.13")
    # older than the index's latest 3.12, so the same major.minor
    assert python("3.12.4") == ("Python.Python.3.12", "Python 3.12")
    assert index.match_program(Program("unknown", "Python", "Python Software Foundation")) is None
    index.close()


def test_package_families_and_full_names(root, scan):
    package = parse_package_full_name("Microsoft.OutlookForWindows_1.2026.1026.100_arm64__8wekyb3d8bbwe")
    assert (package.family, package.version) == ("Microsoft.OutlookForWindows_8wekyb3d8bbwe", "1.2026.1026.100")
    assert parse_package_full_name("not a package") is None
    assert scan(root, [], ["Microsoft.Unknown_1.0.0.0_x64__8wekyb3d8bbwe", "junk"]) == []


def test_no_index_or_another_schema_is_a_scan_error(root, tmp_path, scan):
    with pytest.raises(ScanError, match="winget source update"):
        scan(tmp_path / "nothing")
    with pytest.raises(ScanError, match="winget source update"):
        scan(None)
    with sqlite3.connect(root / "Public" / "index.db") as db:
        db.execute("UPDATE metadata SET value = '1' WHERE name = 'majorVersion'")
    db.close()
    with pytest.raises(ScanError, match="schema 1, expected 2"):
        scan(root)


def test_the_newest_index_found(tmp_path):
    for version in ("2026.1125.1", "2026.1126.1"):
        (tmp_path / version / "Public").mkdir(parents=True)
        (tmp_path / version / "Public" / "index.db").write_bytes(b"")
    assert find_index([str(tmp_path / "2026.1125.1"), str(tmp_path / "2026.1126.1")]) == (
        tmp_path / "2026.1126.1" / "Public" / "index.db"
    )


# winget's name normalizer


def corpus() -> list[tuple[str, str, str]]:
    lines = {
        name: (FIXTURES / "normalization" / f"{name}.txt").read_text(encoding="utf-8").split("\n")
        for name in ("InputNames", "InputPublishers", "NormalizationInitialIds")
    }
    return [
        case for case in zip(lines["InputNames"], lines["InputPublishers"], lines["NormalizationInitialIds"])
        if any(case)
    ]


def test_names_normalize_as_winget_normalizes_its_corpus():
    cases = corpus()
    assert len(cases) > 1000
    wrong = [
        (name, publisher, expected, f"{got.publisher}.{got.name}")
        for name, publisher, expected in cases
        for got in [normalize(name, publisher)]
        if f"{got.publisher}.{got.name}" != expected
    ]
    assert wrong == []


@pytest.mark.parametrize(("name", "architecture", "normalized"), [
    ("Name", None, "Name"),
    ("Name x86", "X86", "Name"),
    ("Name x86_64", "X64", "Name"),
    ("Name (64 bit)", "X64", "Name"),
    ("Name arm32", "Arm", "Name"),
    ("Name arm64", "Arm64", "Name"),
    ("Name aarch64", "Arm64", "Name"),
    ("Name armadillo", None, "Namearmadillo"),
    ("ARM Compiler (x64)", "X64", "ARMCompiler"),
    ("Name 32/64 bit", None, "Name"),
    ("Fox86", None, "Fox86"),
])
def test_architectures_as_wingets_tests_have_them(name, architecture, normalized):
    result = normalize(name, "")
    assert (result.architecture, result.name) == (architecture, normalized)


def test_locales_and_kb_numbers_as_wingets_tests_have_them():
    assert normalize("Name en-US", "").locale == "en-us"
    assert normalize("Name (es-mx)", "").locale == "es-mx"
    assert normalize("Names-mx", "").locale == ""
    assert normalize("Fix for (KB42)", "").name == "FixforKB42"


def test_folded_for_the_index_with_architecture():
    result = for_index("Python 3.14.10 (ARM64)", "Python Software Foundation")
    assert (result.name_with_architecture(), result.publisher) == ("python(Arm64)", "pythonsoftwarefoundation")


def test_the_registry_reader_filters_as_add_or_remove_programs_does(monkeypatch, tmp_path):
    hklm = r"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    entry = lambda key, name, **values: {"root": values.pop("root", hklm), "key": key, "DisplayName": name, **values}
    programs = registry(monkeypatch, tmp_path, uninstall=[
        entry("App", "App", Publisher="Someone", DisplayVersion="1.0"),
        entry("Wow", "Wow App", root=r"HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        entry("Mine", "Mine", root=r"HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        entry("Hidden", "Hidden", SystemComponent=1),
        entry("Patch", "App Patch", ParentKeyName="App"),
        entry("KB1", "Security Update for App", ReleaseType="Security Update"),
        entry("Nameless", None),
        # the same program listed in both views
        entry("App", "App", root=r"HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ], packages=[]).programs()
    assert programs == [
        Program("App", "App", "Someone", "1.0"), Program("Wow", "Wow App"), Program("Mine", "Mine"),
    ]


def test_the_registry_reader_finds_the_source_packages_newest_first_last(monkeypatch, tmp_path):
    reader = registry(monkeypatch, None, uninstall=[], packages=[])
    repository = sys.modules["winreg"].keys[("HKCU", APP_REPOSITORY.casefold())]
    for version in ("2026.1126.1.0", "2026.1125.9.0"):
        repository[f"Microsoft.Winget.Source_{version}_neutral__8wekyb3d8bbwe"] = {
            "PackageRootFolder": str(tmp_path / version)
        }
    assert reader.source_roots() == [str(tmp_path / "2026.1125.9.0"), str(tmp_path / "2026.1126.1.0")]
    assert SOURCE_KEY not in reader.package_full_names() and len(reader.package_full_names()) == 2


def test_a_product_code_match_wins_whichever_row_comes_first():
    by_name, by_code = (1, "A.Name", "A", None, None, 0), (1, "A.Name", "A", None, None, 1)
    other = (2, "B.Name", "B", None, None, 0)
    for rows in ([by_name, by_code, other], [by_code, by_name, other], [other, by_name, by_code]):
        assert _pick(rows, None) == ("A.Name", "A")


def test_an_index_missing_a_table_is_a_scan_error(root, scan):
    with sqlite3.connect(root / "Public" / "index.db") as db:
        db.execute("DROP TABLE norm_names2")
    db.close()
    with pytest.raises(ScanError, match="no such table: norm_names2"):
        scan(root)


def test_a_path_in_a_name_doesnt_backtrack_forever():
    started = time.perf_counter()
    for opening in ("(c:\\", '"c:\\', "(change #1 to c:\\", "installed at c:\\"):
        normalize(opening + "a\\" * 26 + " x", "")
    assert time.perf_counter() - started < 2
    assert normalize("Tool (C:\\Program Files\\Tool\\)", "").name == "Tool"
