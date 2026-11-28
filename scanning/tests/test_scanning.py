"""youpdated_scanning's dispatch. Each scanner gets its own file, with captured fixtures, as it's written"""

from __future__ import annotations

from typing import ClassVar

import pytest

import youpdated_scanning
from youpdated.importers import Found
from youpdated_scanning import SCANNERS, Installed, ScanError, scan


class Fake:
    name: ClassVar[str] = "fake"
    summary: ClassVar[str] = "test scanner"
    sources: ClassVar[tuple[str, ...]] = ("steam",)

    def __init__(self, finds=(), *, available=True, error=None):
        self.finds, self._available, self.error = list(finds), available, error

    def available(self):
        return self._available

    def scan(self):
        if self.error:
            raise self.error
        return self.finds


@pytest.fixture
def scanners(monkeypatch):
    replaced: dict = {}
    monkeypatch.setattr(youpdated_scanning, "SCANNERS", replaced)
    return replaced


def test_every_scanner_names_sources_youpdated_has():
    from youpdated.registry import all_sources

    known = all_sources()
    for scanner in SCANNERS.values():
        assert set(scanner.sources) <= set(known), scanner.name


def test_finds_go_into_found_and_come_back(scanners):
    scanners["fake"] = Fake([Installed("steam", "440", "Team Fortress 2", "123")])
    found = Found()
    installed = scan(found)
    assert found.entries == {"steam": ["440"]}
    assert installed[0].version == "123"


def test_a_find_the_source_rejects_is_skipped(scanners):
    scanners["fake"] = Fake([Installed("steam", "not an app")])
    found = Found()
    scan(found)
    assert found.entries == {}
    assert found.skipped


def test_unavailable_scanners_are_not_run(scanners):
    scanners["fake"] = Fake(error=AssertionError("ran"), available=False)
    scan(Found())


def test_a_failing_scanner_is_a_warning(scanners):
    scanners["fake"] = Fake(error=ScanError("winget export exited with 1"))
    found = Found()
    scan(found)
    assert found.warnings == ["fake: winget export exited with 1"]


def test_only_names_known_scanners(scanners):
    scanners["fake"] = Fake()
    with pytest.raises(ValueError, match="no scanner named nope"):
        scan(Found(), ["nope"])


def test_a_package_managers_app_is_not_also_watched_by_its_feed(scanners, tmp_path):
    app = tmp_path / "Applications" / "UTM.app"
    scanners["homebrew"] = Fake([Installed("homebrew", "cask:utm", where=app)])
    scanners["apps"] = Fake([
        Installed("sparkle", "https://example.com/utm/appcast.xml", where=app),
        Installed("sparkle", "https://example.com/other/appcast.xml", where=tmp_path / "Other.app"),
    ])
    found = Found()
    installed = scan(found)
    assert found.entries == {"homebrew": ["cask:utm"], "sparkle": ["https://example.com/other/appcast.xml"]}
    assert [item.entry for item in installed] == ["cask:utm", "https://example.com/other/appcast.xml"]


def test_only_runs_in_scanner_order(scanners, tmp_path):
    app = tmp_path / "UTM.app"
    scanners["homebrew"] = Fake([Installed("homebrew", "cask:utm", where=app)])
    scanners["apps"] = Fake([Installed("sparkle", "https://example.com/utm/appcast.xml", where=app)])
    found = Found()
    scan(found, ["apps", "homebrew"])
    assert found.entries == {"homebrew": ["cask:utm"]}
    # with apps alone, the feed is how it's watched
    found = Found()
    scan(found, ["apps"])
    assert found.entries == {"sparkle": ["https://example.com/utm/appcast.xml"]}


def test_package_managers_run_before_apps():
    order = list(SCANNERS)
    assert order.index("homebrew") < order.index("apps")


def test_an_unexpected_error_is_a_warning_and_the_rest_still_run(scanners):
    scanners["broken"] = Fake(error=KeyError("CFBundleIdentifier"))
    scanners["fake"] = Fake([Installed("steam", "440")])
    found = Found()
    assert [item.entry for item in scan(found)] == ["440"]
    assert found.warnings == ["broken failed: KeyError: 'CFBundleIdentifier'"]
    assert found.entries == {"steam": ["440"]}
