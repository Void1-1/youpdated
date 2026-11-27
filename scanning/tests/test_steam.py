"""The steam scanner, against a library and manifest copied from a real install (home path and account redacted)"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from youpdated.importers import Found
from youpdated_scanning import steam
from youpdated_scanning.steam import SteamScanner, libraries, parse_keyvalues

FIXTURES = Path(__file__).parent / "fixtures" / "steam"


def manifest(appid: str, name: str, buildid: str = "1", installdir: str | None = None) -> str:
    """An ``appmanifest_*.acf``, trimmed to the keys read. Proton's and the redistributables' look like games'"""
    return (
        f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"Universe"\t\t"1"\n\t"name"\t\t"{name}"\n'
        f'\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"{installdir or name}"\n\t"buildid"\t\t"{buildid}"\n}}\n'
    )


def library(path: Path, *manifests: tuple[str, str]) -> Path:
    """``path/steamapps`` with ``(file name, text)`` manifests"""
    steamapps = path / "steamapps"
    steamapps.mkdir(parents=True)
    for name, text in manifests:
        (steamapps / name).write_text(text, encoding="utf-8")
    return path


def folders(*paths: Path) -> str:
    """A ``libraryfolders.vdf`` as Steam writes it now, backslashes escaped"""
    sections = "".join(
        f'\t"{n}"\n\t{{\n\t\t"path"\t\t"{str(path).replace(chr(92), chr(92) * 2)}"\n\t\t"label"\t\t""\n\t}}\n'
        for n, path in enumerate(paths)
    )
    return f'"libraryfolders"\n{{\n{sections}}}\n'


@pytest.fixture
def root(tmp_path):
    """Steam's directory on macOS with one game, as the fixtures have it"""
    root = library(tmp_path / "Steam")
    for fixture in ("libraryfolders.vdf", "appmanifest_275850.acf"):
        shutil.copyfile(FIXTURES / fixture, root / "steamapps" / fixture)
    return root


def test_a_game_by_appid_with_its_build(root):
    (item,) = SteamScanner([root]).scan()
    assert (item.source, item.entry, item.name, item.version) == ("steam", "275850", "No Man's Sky", "26924890")
    assert item.where == (root / "steamapps" / "common" / "No Man's Sky").resolve()


def test_every_library_steam_lists(root, tmp_path):
    # The fixture's own path is someone else's home, so it isn't there
    other = library(tmp_path / "Games" / "SteamLibrary", ("appmanifest_440.acf", manifest("440", "Team Fortress 2")))
    (root / "steamapps" / "libraryfolders.vdf").write_text(folders(root, other), encoding="utf-8")
    assert [item.entry for item in SteamScanner([root]).scan()] == ["275850", "440"]
    assert libraries(root) == [root, other.resolve()]


def test_windows_paths_are_unescaped():
    parsed = parse_keyvalues('"libraryfolders"\n{\n\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t}\n}\n')
    assert parsed["libraryfolders"]["1"]["path"] == "D:\\SteamLibrary"


def test_the_old_library_format(tmp_path):
    root, other = library(tmp_path / "Steam"), library(tmp_path / "Library", ("appmanifest_70.acf", manifest("70", "Half-Life")))
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        # Steam escapes backslashes, so a Windows path is written with them doubled
        f'"LibraryFolders"\n{{\n\t"TimeNextStatsReport"\t\t"1561832478"\n\t"1"\t\t"{str(other).replace(chr(92), chr(92) * 2)}"\n}}\n',
        encoding="utf-8",
    )
    assert [item.entry for item in SteamScanner([root]).scan()] == ["70"]


def test_tools_are_not_games(tmp_path):
    root = library(
        tmp_path / "Steam",
        ("appmanifest_228980.acf", manifest("228980", "Steamworks Common Redistributables")),
        ("appmanifest_1007.acf", manifest("1007", "Steamworks SDK Redist")),
        ("appmanifest_1493710.acf", manifest("1493710", "Proton Experimental", installdir="Proton - Experimental")),
        ("appmanifest_2805730.acf", manifest("2805730", "Proton 9.0")),
        ("appmanifest_1628350.acf", manifest("1628350", "Steam Linux Runtime 3.0 (sniper)", installdir="SteamLinuxRuntime_sniper")),
        ("appmanifest_620.acf", manifest("620", "Portal 2")),
    )
    assert [item.name for item in SteamScanner([root]).scan()] == ["Portal 2"]


def test_broken_and_missing_libraries_are_passed_over(tmp_path):
    root = library(
        tmp_path / "Steam",
        ("appmanifest_1.acf", '"AppState"\n{\n\t"appid"\t\t"1"\n'),
        ("appmanifest_2.acf", '"AppState"\n{\n\t"name"\t\t"no appid"\n}\n'),
        ("appmanifest_3.acf", '"AppState" { "appid" "3" "name" "Unterminated }'),
        ("appmanifest_4.acf", manifest("4", "Fine", buildid="0")),
    )
    (root / "steamapps" / "libraryfolders.vdf").write_text(folders(root, tmp_path / "unplugged"), encoding="utf-8")
    (item,) = SteamScanner([root]).scan()
    # buildid 0 is a game that hasn't finished its first download
    assert (item.entry, item.version) == ("4", None)


def test_the_same_game_in_two_places_is_one_find(tmp_path):
    first = library(tmp_path / "a", ("appmanifest_440.acf", manifest("440", "Team Fortress 2")))
    second = library(tmp_path / "b", ("appmanifest_440.acf", manifest("440", "Team Fortress 2")))
    assert [item.entry for item in SteamScanner([first, second]).scan()] == ["440"]


def test_keyvalues_comments_conditionals_and_escapes():
    text = '// written by Steam\n"a"\n{\n\t"b"\t"one" [$WIN32]\n\t"c"\t"say \\"hi\\""\t// note\n\tbare\tword\n}\n'
    assert parse_keyvalues(text) == {"a": {"b": "one", "c": 'say "hi"', "bare": "word"}}
    for broken in ('"a" {', '"a" }', '{ }', '"a" "b" "c"', '"a" "unterminated'):
        with pytest.raises(ValueError):
            parse_keyvalues(broken)


def test_steam_roots_on_linux_are_one_install_through_the_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(steam.sys, "platform", "linux")
    monkeypatch.setattr(steam.Path, "home", classmethod(lambda cls: tmp_path))
    real = library(tmp_path / ".local" / "share" / "Steam")
    (tmp_path / ".steam").mkdir()
    try:
        (tmp_path / ".steam" / "steam").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("can't make symlinks here")
    assert steam.steam_roots() == [real.resolve()]


def test_every_find_is_one_the_steam_source_reads(root):
    found = Found()
    for item in SteamScanner([root]).scan():
        found.add(item.source, item.entry)
    assert found.entries == {"steam": ["275850"]}
    assert not found.skipped
