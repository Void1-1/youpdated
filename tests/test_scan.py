"""`scan`: the CLI side of youpdated-scanning, which is a separate package"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from youpdated.cli import main
from youpdated.config import EXAMPLE_CONFIG, load_config


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def scanning(monkeypatch):
    """A stand-in youpdated_scanning whose scan adds what ``finds`` lists: (source, entry) or (source, entry, name)"""
    module = types.ModuleType("youpdated_scanning")
    module.finds = []
    module.calls = []

    def scan(found, only=None):
        module.calls.append(only)
        if only and "nope" in only:
            raise ValueError("no scanner named nope; expected one of steam")
        installed = []
        for source, entry, *name in module.finds:
            found.add(source, entry)
            installed.append(SimpleNamespace(source=source, entry=entry, name=name[0] if name else None))
        return installed

    module.scan = scan
    monkeypatch.setitem(sys.modules, "youpdated_scanning", module)
    return module


def test_without_the_extra_it_says_how_to_install_it(config, monkeypatch, capsys):
    # None in sys.modules makes the import fail
    monkeypatch.setitem(sys.modules, "youpdated_scanning", None)
    assert main(["scan", "-c", str(config)]) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "youpdated-scanning" in out and "pipx inject youpdated youpdated-scanning" in out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_finds_are_added_like_an_import(config, scanning, capsys):
    scanning.finds = [("steam", "570"), ("homebrew", "cask:firefox")]
    assert main(["scan", "-c", str(config)]) == 0
    assert 570 in load_config(config).sources["steam"]
    out = capsys.readouterr().out
    assert "Added 570 under steam" in out
    assert "Already watching cask:firefox under homebrew" in out


def test_test_writes_nothing(config, scanning, capsys):
    scanning.finds = [("steam", "570")]
    assert main(["scan", "--test", "-c", str(config)]) == 0
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG
    assert "nothing was written" in capsys.readouterr().out


def test_nothing_found_says_so(config, scanning, capsys):
    assert main(["scan", "-c", str(config)]) == 0
    assert "Found nothing to watch" in capsys.readouterr().out


def test_only_is_passed_through_and_an_unknown_scanner_is_an_error(config, scanning, capsys):
    assert main(["scan", "--only", "steam", "--only", "apps", "-c", str(config)]) == 0
    assert scanning.calls == [["steam", "apps"]]
    assert main(["scan", "--only", "nope", "-c", str(config)]) == 1
    assert "no scanner named nope" in capsys.readouterr().out


def test_a_find_its_entry_doesnt_name_is_shown_by_name(config, scanning, capsys):
    scanning.finds = [
        ("steam", "275850", "No Man's Sky"),
        ("appstore", "com.apple.dt.Xcode", "Xcode"),
        ("winget", "Microsoft.VisualStudioCode", "Microsoft Visual Studio Code"),
        # already watched by the example config
        ("steam", "440", "Team Fortress 2"),
    ]
    assert main(["scan", "--test", "-c", str(config)]) == 0
    out = capsys.readouterr().out
    assert "steam 275850 is No Man's Sky" in out
    assert " is Xcode" not in out and "Visual Studio Code" not in out and "Team Fortress" not in out
