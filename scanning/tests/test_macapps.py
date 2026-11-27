"""The apps scanner, against Info.plist files copied from real apps"""

from __future__ import annotations

import plistlib
import shutil
from pathlib import Path

import pytest

from youpdated.importers import Found
from youpdated_scanning.macapps import MacAppsScanner, read_plist

FIXTURES = Path(__file__).parent / "fixtures" / "macapps"
PROTON_FEED = "https://protonvpn.com/download/macos/updates/v5/sparkle.xml"


def install(where: Path, name: str, plist: str, *, receipt: bool = False) -> Path:
    """``where/name.app`` with the fixture ``plist`` as its Info.plist"""
    app = where / f"{name}.app"
    (app / "Contents").mkdir(parents=True)
    shutil.copyfile(FIXTURES / plist, app / "Contents" / "Info.plist")
    if receipt:
        # Only its being there is read
        (app / "Contents" / "_MASReceipt").mkdir()
        (app / "Contents" / "_MASReceipt" / "receipt").write_bytes(b"receipt")
    return app


@pytest.fixture
def machine(tmp_path):
    """/Applications, ~/Applications, and ~/Library/Preferences"""
    for path in ("Applications", "home/Applications", "Preferences"):
        (tmp_path / path).mkdir(parents=True)
    return tmp_path


def scan(machine: Path) -> list:
    dirs = [machine / "Applications", machine / "home" / "Applications"]
    return list(MacAppsScanner(dirs, machine / "Preferences").scan())


def test_a_sparkle_feed_in_the_plist(machine):
    app = install(machine / "Applications", "ProtonVPN", "ProtonVPN.plist")
    (item,) = scan(machine)
    assert (item.source, item.entry, item.name, item.version) == ("sparkle", PROTON_FEED, "ProtonVPN", "6.5.2")
    assert item.where == app.resolve()


def test_an_app_store_app_by_bundle_id_one_folder_deep(machine):
    install(machine / "Applications" / "Developer", "Xcode", "Xcode.plist", receipt=True)
    (item,) = scan(machine)
    assert (item.source, item.entry, item.name, item.version) == ("appstore", "com.apple.dt.Xcode", "Xcode", "27.2")


def test_a_feed_from_the_apps_defaults(machine):
    install(machine / "Applications", "UTM", "UTM.plist")
    assert scan(machine) == []
    (machine / "Preferences" / "com.utmapp.UTM.plist").write_bytes(
        plistlib.dumps({"SUFeedURL": "https://example.com/appcast.xml"}, fmt=plistlib.FMT_BINARY)
    )
    (item,) = scan(machine)
    assert (item.source, item.entry, item.version) == ("sparkle", "https://example.com/appcast.xml", "5.0.9")


def test_a_receipt_wins_over_a_feed(machine):
    install(machine / "Applications", "ProtonVPN", "ProtonVPN.plist", receipt=True)
    (item,) = scan(machine)
    assert (item.source, item.entry) == ("appstore", "ch.protonvpn.mac")


def test_apps_with_neither_or_a_broken_plist_are_passed_over(machine):
    install(machine / "home" / "Applications", "No Man's Sky", "steam-shortcut.plist")
    install(machine / "Applications", "UTM", "UTM.plist")
    (machine / "Applications" / "Empty.app").mkdir()
    (machine / "Applications" / "notes.txt").write_text("not an app")
    assert read_plist(machine / "home" / "Applications" / "No Man's Sky.app" / "Contents" / "Info.plist") is None
    assert scan(machine) == []


def test_only_one_folder_deep_and_not_inside_apps(machine):
    install(machine / "Applications" / "a" / "b", "ProtonVPN", "ProtonVPN.plist")
    install(machine / "Applications" / "UTM.app" / "Contents" / "Library", "ProtonVPN", "ProtonVPN.plist")
    # a missing folder is nothing to scan
    assert list(MacAppsScanner([machine / "nope"], machine / "Preferences").scan()) == []
    assert scan(machine) == []


def test_every_find_is_one_its_source_reads(machine):
    install(machine / "Applications", "ProtonVPN", "ProtonVPN.plist")
    install(machine / "Applications", "Xcode", "Xcode.plist", receipt=True)
    found = Found()
    for item in scan(machine):
        found.add(item.source, item.entry)
    assert found.entries == {"appstore": ["com.apple.dt.Xcode"], "sparkle": [PROTON_FEED]}
    assert not found.skipped
