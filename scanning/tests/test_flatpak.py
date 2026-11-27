"""The flatpak scanner"""

from __future__ import annotations

from pathlib import Path

from youpdated.importers import Found
from youpdated_scanning import flatpak
from youpdated_scanning.flatpak import FlatpakScanner, parse_list

FIXTURES = Path(__file__).parent / "fixtures" / "flatpak"


class Flatpak(FlatpakScanner):
    def __init__(self, listed: str | None = None) -> None:
        self.listed, self.calls = listed, []

    def flatpak(self, *args: str) -> str:
        self.calls.append(args)
        return self.listed if self.listed is not None else (FIXTURES / "list.txt").read_text(encoding="utf-8")


def test_apps_from_flathub_once_each():
    finds = list(Flatpak().scan())
    assert [(item.entry, item.name, item.version) for item in finds] == [
        ("org.mozilla.firefox", "Firefox", "146.0"),
        ("com.valvesoftware.Steam", "Steam", "1.0.0.85"),
        # no AppStream version
        ("io.github.Hand.Made", "Hand Made", None),
    ]
    assert all(item.source == "flathub" for item in finds)


def test_lists_apps_only_without_fancy_output():
    scanner = Flatpak()
    list(scanner.scan())
    assert scanner.calls == [("list", "--app", "--columns=application,version,origin,name")]
    assert flatpak.ENV == {"FLATPAK_FANCY_OUTPUT": "0"}


def test_no_apps_and_short_rows():
    assert list(Flatpak("").scan()) == []
    assert parse_list("org.a.App\t1.0\n\t1.0\tflathub\norg.b.App\t2.0\tflathub\r\n") == [
        ("org.b.App", "2.0", "flathub", None)
    ]


def test_every_find_is_one_the_flathub_source_reads():
    found = Found()
    for item in Flatpak().scan():
        found.add(item.source, item.entry)
    assert found.entries == {"flathub": ["org.mozilla.firefox", "com.valvesoftware.Steam", "io.github.Hand.Made"]}
    assert not found.skipped
