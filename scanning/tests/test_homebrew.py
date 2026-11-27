"""The homebrew scanner, against `brew list` output captured from a real install"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from youpdated.importers import Found
from youpdated_scanning import ScanError, base, homebrew
from youpdated_scanning.homebrew import HomebrewScanner

FIXTURES = Path(__file__).parent / "fixtures" / "homebrew"

OUTPUT = {
    ("list", "--formula", "--installed-on-request", "--full-name"): "formula-on-request.txt",
    ("list", "--formula", "--versions"): "formula-versions.txt",
    ("list", "--cask", "--full-name"): "cask.txt",
    ("list", "--cask", "--versions"): "cask-versions.txt",
}


def symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("can't make symlinks here")


class Brew(HomebrewScanner):
    """`brew` answering from the fixtures, with a Caskroom at ``caskroom``"""

    def __init__(self, caskroom: Path, extra: dict[tuple[str, ...], str] | None = None) -> None:
        self.caskroom, self.extra, self.calls = caskroom, extra or {}, []

    def brew(self, *args: str) -> str:
        self.calls.append(args)
        if args == ("--caskroom",):
            return f"{self.caskroom}\n"
        text = (FIXTURES / OUTPUT[args]).read_text(encoding="utf-8")
        return text + self.extra.get(args, "")


@pytest.fixture(autouse=True)
def macos(monkeypatch, tmp_path):
    monkeypatch.setattr(homebrew.sys, "platform", "darwin")


@pytest.fixture
def caskroom(tmp_path):
    """utm's Caskroom symlink to its app, like `brew install --cask utm` leaves. The pkg casks have none"""
    app = tmp_path / "Applications" / "UTM.app"
    app.mkdir(parents=True)
    symlink(tmp_path / "Caskroom" / "utm" / "5.0.9-2" / "UTM.app", app)
    return tmp_path / "Caskroom"


def test_formulae_asked_for_and_casks(caskroom):
    finds = list(Brew(caskroom).scan())
    assert [(item.entry, item.version) for item in finds] == [
        ("gh", "2.104.0"),
        ("git", "2.56.0_1"),
        # also dependencies
        ("openssl@3", "3.5.9"),
        ("python@3.12", "3.12.16"),
        ("cask:dotnet-sdk", "10.0.403"),
        ("cask:temurin@21", "21.0.13,9"),
        ("cask:utm", "5.0.9-2"),
    ]
    # ca-certificates is only a dependency
    assert all(item.source == "homebrew" for item in finds)


def test_a_cask_app_is_where_the_caskroom_links(caskroom, tmp_path):
    where = {item.entry: item.where for item in Brew(caskroom).scan()}
    assert where["cask:utm"] == (tmp_path / "Applications" / "UTM.app").resolve()
    # pkg installers leave no app
    assert where["cask:dotnet-sdk"] is None
    assert where["gh"] is None


def test_every_find_is_one_the_homebrew_source_reads(caskroom):
    found = Found()
    for item in Brew(caskroom).scan():
        found.add(item.source, item.entry)
    assert not found.skipped
    assert len(found.entries["homebrew"]) == 7


def test_third_party_taps_are_skipped_by_the_source_and_leave_their_app(tmp_path):
    # `--full-name` names a tap's formula or cask `user/tap/name`. No tap is installed where the fixtures came from
    app = tmp_path / "Applications" / "Widget.app"
    app.mkdir(parents=True)
    symlink(tmp_path / "Caskroom" / "widget" / "1.0" / "Widget.app", app)
    scanner = Brew(tmp_path / "Caskroom", {
        ("list", "--formula", "--installed-on-request", "--full-name"): "someone/tools/gadget\n",
        ("list", "--formula", "--versions"): "gadget 2.0\n",
        ("list", "--cask", "--full-name"): "someone/tools/widget\n",
        ("list", "--cask", "--versions"): "widget 1.0\n",
    })
    finds = {item.entry: item for item in scanner.scan()}
    assert finds["someone/tools/gadget"].version == "2.0"
    assert finds["cask:someone/tools/widget"].where is None

    found = Found()
    for item in finds.values():
        found.add(item.source, item.entry)
    assert "gadget" not in str(found.entries) and "widget" not in str(found.entries)
    assert {what for what, _ in found.skipped} == {"someone/tools/gadget", "cask:someone/tools/widget"}
    assert all("third-party tap" in why for _, why in found.skipped)


def test_no_casks_skips_the_cask_calls(tmp_path):
    class NoCasks(Brew):
        def brew(self, *args):
            return "" if args == ("list", "--cask", "--full-name") else super().brew(*args)

    scanner = NoCasks(tmp_path)
    assert [item.entry for item in scanner.scan()] == ["gh", "git", "openssl@3", "python@3.12"]
    assert ("--caskroom",) not in scanner.calls


def test_linux_has_no_casks(tmp_path, monkeypatch):
    monkeypatch.setattr(homebrew.sys, "platform", "linux")
    scanner = Brew(tmp_path)
    assert [item.entry for item in scanner.scan()] == ["gh", "git", "openssl@3", "python@3.12"]
    assert not any("--cask" in call for call in scanner.calls)


def test_parse_versions_keeps_the_last_of_several():
    assert homebrew.parse_versions("python@3.12 3.12.15 3.12.16\nbare\n") == {"python@3.12": "3.12.16"}


@pytest.fixture
def ran(monkeypatch):
    """subprocess.run replaced: records the call and returns what ``ran.result`` says"""
    ran = SimpleNamespace(calls=[], result=None)

    def run(command, **kwargs):
        ran.calls.append((command, kwargs))
        if isinstance(ran.result, BaseException):
            raise ran.result
        return ran.result

    monkeypatch.setattr(base.shutil, "which", lambda name: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(base.subprocess, "run", run)
    return ran


def test_brew_runs_without_updating_or_analytics(ran):
    ran.result = subprocess.CompletedProcess([], 0, stdout="gh\n", stderr="")
    assert HomebrewScanner().brew("list", "--formula") == "gh\n"
    (command, kwargs), = ran.calls
    assert command == ["/opt/homebrew/bin/brew", "list", "--formula"]
    assert kwargs["env"]["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert kwargs["env"]["HOMEBREW_NO_ANALYTICS"] == "1"
    assert kwargs["env"]["PATH"] == os.environ["PATH"]


def test_brew_failing_is_a_scan_error_with_its_last_line(ran):
    ran.result = subprocess.CompletedProcess([], 1, stdout="", stderr="Usage: brew list\n\nError: invalid option\n")
    with pytest.raises(ScanError, match=r"`brew list --cask` exited with 1: Error: invalid option"):
        HomebrewScanner().brew("list", "--cask")


def test_brew_stuck_is_a_scan_error(ran):
    ran.result = subprocess.TimeoutExpired(["brew"], homebrew.TIMEOUT)
    with pytest.raises(ScanError, match="took longer than"):
        HomebrewScanner().brew("list")
