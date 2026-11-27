"""The winget scanner, against a Windows 11 machine's registry and winget's index"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from youpdated.importers import Found
from youpdated_scanning import ScanError
from youpdated_scanning.winget import Index, Program, WingetScanner, find_index, parse_package_full_name
from youpdated_scanning.wingetnames import for_index, normalize

FIXTURES = Path(__file__).parent / "fixtures" / "winget"


def installed() -> tuple[list[Program], list[str]]:
    """The machine's programs, filtered as the registry reader filters them"""
    doc = json.loads((FIXTURES / "installed.json").read_text(encoding="utf-8"))
    programs = [
        Program(entry["key"], entry["DisplayName"], entry["Publisher"] or "", entry["DisplayVersion"])
        for entry in doc["uninstall"]
        if entry["DisplayName"] and entry["SystemComponent"] != 1 and not entry["ParentKeyName"]
    ]
    return programs, doc["packages"]


class Machine:
    """The registry as the scanner reads it, with the index installed under ``root``"""

    def __init__(self, root: Path, programs=None, packages=None) -> None:
        machine = installed()
        self.programs_, self.packages_ = machine[0] if programs is None else programs, machine[1] if packages is None else packages
        self.root = root

    def programs(self):
        return self.programs_

    def package_full_names(self):
        return self.packages_

    def source_roots(self):
        return [str(self.root)]


@pytest.fixture
def root(tmp_path):
    """The source package's folder, as winget installs it"""
    root = tmp_path / "Microsoft.Winget.Source_2026.1126.2410.27_neutral__8wekyb3d8bbwe"
    (root / "Public").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "index.db", root / "Public" / "index.db")
    return root


def scan(root: Path, programs=None, packages=None) -> list:
    return list(WingetScanner(Machine(root, programs, packages)).scan())


def test_what_winget_export_found_less_runtimes(root):
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


def test_every_find_is_one_the_winget_source_reads(root):
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


def test_package_families_and_full_names(root):
    package = parse_package_full_name("Microsoft.OutlookForWindows_1.2026.1026.100_arm64__8wekyb3d8bbwe")
    assert (package.family, package.version) == ("Microsoft.OutlookForWindows_8wekyb3d8bbwe", "1.2026.1026.100")
    assert parse_package_full_name("not a package") is None
    assert scan(root, [], ["Microsoft.Unknown_1.0.0.0_x64__8wekyb3d8bbwe", "junk"]) == []


def test_no_index_or_another_schema_is_a_scan_error(root, tmp_path):
    with pytest.raises(ScanError, match="winget source update"):
        scan(tmp_path / "nothing")
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
