"""Uninstall: find traces, and refusing to delete other stuff"""

from __future__ import annotations

import os
import sys
import time
from importlib import metadata
from types import SimpleNamespace

import pytest

from youpdated import cleanup, cli, config
from youpdated.cleanup import (
    Package,
    Trace,
    find_package,
    find_traces,
    remove_package,
    remove_traces,
)


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """Point config and data at temp directory"""
    directory = tmp_path / "youpdated"
    # find_traces() also scans the working directory and the tool's folder for a project config, so move off the checkout
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "tool_dir", lambda: tmp_path)
    monkeypatch.setattr(cleanup, "config_dir", lambda: directory)
    monkeypatch.setattr(cleanup, "data_dir", lambda: directory)
    monkeypatch.setattr(cleanup, "default_config_path", lambda: directory / "config.yaml")
    monkeypatch.setattr(cleanup, "default_state_path", lambda: directory / "state.sqlite3")
    return directory


def _populate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text("sources:\n  npm: [express]\n", encoding="utf-8")
    (directory / "state.sqlite3").write_bytes(b"sqlite")


def test_finds_config_state_and_directory(app_dir):
    _populate(app_dir)
    traces = find_traces()
    kinds = {t.kind for t in traces}

    assert kinds == {"config", "state", "directory"}
    assert app_dir / "config.yaml" in [t.path for t in traces]


def test_reports_nothing_when_nothing_was_installed(app_dir):
    assert find_traces() == []


def test_keep_config_leaves_the_config_out(app_dir):
    _populate(app_dir)
    traces = find_traces(keep_config=True)

    assert not any(t.kind == "config" for t in traces)
    assert any(t.kind == "state" for t in traces)


def test_an_empty_leftover_directory_is_still_reported(app_dir):
    """Files removed but directory left"""
    app_dir.mkdir(parents=True)
    (trace,) = find_traces()
    assert trace.kind == "directory"


def test_explicit_overrides_are_included(app_dir, tmp_path):
    custom_config = tmp_path / "custom.yaml"
    custom_config.write_text("sources: {}\n", encoding="utf-8")
    custom_state = tmp_path / "custom.sqlite3"
    custom_state.write_bytes(b"x")

    paths = [t.path for t in find_traces(custom_config, custom_state)]
    assert custom_config in paths and custom_state in paths


def test_nonexistent_paths_are_not_listed(app_dir, tmp_path):
    assert find_traces(tmp_path / "nope.yaml") == []


def test_removes_files_then_the_emptied_directory(app_dir):
    _populate(app_dir)
    removed, failed = remove_traces(find_traces())

    assert not failed
    assert len(removed) == 3
    assert not app_dir.exists()


def test_refuses_to_remove_a_directory_holding_someone_elses_files(app_dir):
    _populate(app_dir)
    (app_dir / "notes.txt").write_text("not ours", encoding="utf-8")

    removed, failed = remove_traces(find_traces())

    assert app_dir.exists(), "a directory with unknown files must not be removed"
    assert (app_dir / "notes.txt").read_text(encoding="utf-8") == "not ours"
    assert any("not empty" in reason for _, reason in failed)
    assert len(removed) == 2  # the config and state files go


def test_refuses_to_remove_a_directory_that_is_not_ours(tmp_path):
    """name guard: even an empty directory is left alone unless it is the application directory"""
    stranger = tmp_path / "Documents"
    stranger.mkdir()

    removed, failed = remove_traces([Trace(stranger, "directory", "test")])

    assert stranger.exists()
    assert not removed
    assert any("not a youpdated directory" in reason for _, reason in failed)


def test_removal_is_idempotent(app_dir):
    _populate(app_dir)
    remove_traces(find_traces())

    assert find_traces() == []
    assert remove_traces(find_traces()) == ([], [])


class _Dist:
    """enough of an installed distribution for find_package()"""

    version = "9.9.9"

    def __init__(self, location, installer):
        self.location = location
        self.installer = installer

    def read_text(self, name):
        return self.installer if name == "INSTALLER" else None

    def locate_file(self, path):
        return self.location / path


@pytest.fixture
def installed(monkeypatch):
    """Pretend Youpdated is installed in an environment at `prefix` and runs from there"""

    def install(prefix, installer="pip\n", runs_from=None):
        site = prefix / "lib" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        dist = _Dist(site, installer)
        monkeypatch.setattr(
            cleanup,
            "metadata",
            SimpleNamespace(
                distribution=lambda name: dist,
                PackageNotFoundError=metadata.PackageNotFoundError,
            ),
        )
        monkeypatch.setattr(cleanup, "tool_dir", lambda: (runs_from or site).resolve())
        monkeypatch.setattr(sys, "prefix", str(prefix))
        return prefix

    return install


def test_no_package_when_not_installed(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(
        cleanup,
        "metadata",
        SimpleNamespace(distribution=missing, PackageNotFoundError=metadata.PackageNotFoundError),
    )
    assert find_package() is None


def test_no_package_when_running_from_a_checkout(installed, tmp_path):
    """the installed package isn't this copy, so removing it leaves this one behind"""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    installed(tmp_path / "venv", runs_from=checkout)

    assert find_package() is None


def test_a_leftover_egg_info_is_not_an_install(installed, tmp_path):
    """a build leaves youpdated.egg-info in the checkout, which no installer wrote"""
    installed(tmp_path / "checkout", installer=None)

    assert find_package() is None


def test_pip_install_is_removed_by_the_running_interpreter(installed, tmp_path):
    installed(tmp_path / "venv")
    package = find_package()
    assert package is not None

    assert package.installer == "pip"
    assert package.command == [sys.executable, "-m", "pip", "uninstall", "--yes", "youpdated"]
    assert package.version == "9.9.9"
    assert package.after_exit == (os.name == "nt")


def test_uv_pip_install_is_removed_with_uv(installed, tmp_path):
    installed(tmp_path / "venv", installer="uv\n")
    package = find_package()
    assert package is not None

    assert package.command == ["uv", "pip", "uninstall", "--python", sys.executable, "youpdated"]


def test_pipx_install_is_removed_with_pipx_from_its_own_home(installed, tmp_path):
    prefix = installed(tmp_path / "pipx" / "venvs" / "youpdated2")  # pipx install --suffix 2
    (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")
    package = find_package()
    assert package is not None

    assert package.installer == "pipx"
    assert package.command == ["pipx", "uninstall", "youpdated2"]
    assert package.env == {"PIPX_HOME": str(tmp_path / "pipx")}


def test_uv_tool_install_is_removed_with_uv_tool(installed, tmp_path):
    prefix = installed(tmp_path / "tools" / "youpdated", installer="uv\n")
    (prefix / "uv-receipt.toml").write_text("", encoding="utf-8")
    package = find_package()
    assert package is not None

    assert package.installer == "uv tool"
    assert package.command == ["uv", "tool", "uninstall", "youpdated"]
    assert package.env == {"UV_TOOL_DIR": str(tmp_path / "tools")}


def _deleting(path):
    """A command that removes `path` the way an uninstaller would"""
    return Package(
        "9.9.9", "pip", [sys.executable, "-c", f"import os; os.remove({str(path)!r})"]
    )


def test_remove_package_succeeds_once_this_copy_is_gone(tmp_path, monkeypatch):
    module = tmp_path / "cleanup.py"
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(cleanup, "__file__", str(module))

    assert remove_package(_deleting(module)) is None
    assert not module.exists()


def test_remove_package_reports_a_failing_uninstaller(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "__file__", str(tmp_path / "cleanup.py"))
    package = Package("9.9.9", "pip", [sys.executable, "-c", "raise SystemExit(3)"])

    assert remove_package(package) == "pip exited with status 3"


def test_remove_package_checks_the_files_really_went(tmp_path, monkeypatch):
    """pip exits 0 with "Skipping youpdated as it is not installed"."""
    module = tmp_path / "cleanup.py"
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(cleanup, "__file__", str(module))
    package = Package("9.9.9", "pip", [sys.executable, "-c", "pass"])

    error = remove_package(package)
    assert error is not None and "still in" in error


def test_remove_package_after_exit_waits_for_this_process(tmp_path):
    """Windows keeps the running youpdated.exe, so its uninstaller can't start until this exits"""
    module = tmp_path / "cleanup.py"
    module.write_text("", encoding="utf-8")
    package = _deleting(module)
    package.after_exit = True

    assert remove_package(package) is None
    helper = cleanup._waiting.pop()
    time.sleep(0.5)
    assert module.exists()

    assert helper.stdin is not None
    helper.stdin.close() # as when this process exits
    assert helper.wait(timeout=30) == 0
    assert not module.exists()


def test_remove_package_without_the_uninstaller_on_path(monkeypatch):
    monkeypatch.setenv("PATH", "")
    package = Package("9.9.9", "pipx", ["pipx-not-installed", "uninstall", "youpdated"])

    assert remove_package(package) == "pipx-not-installed not found on PATH"


@pytest.fixture
def removals(monkeypatch):
    """The CLI's package, and the packages it tried to remove"""
    calls = []
    package = Package("9.9.9", "pipx", ["pipx", "uninstall", "youpdated"])
    monkeypatch.setattr(cli, "find_package", lambda: package)
    monkeypatch.setattr(cli, "remove_package", lambda p: calls.append(p))
    return calls


def test_uninstall_removes_files_and_then_the_package(app_dir, removals, capsys):
    _populate(app_dir)

    assert cli.main(["uninstall", "--yes"]) == 0
    assert not app_dir.exists()
    assert [p.command for p in removals] == [["pipx", "uninstall", "youpdated"]]
    assert "removed youpdated 9.9.9" in capsys.readouterr().out


def test_uninstall_on_windows_says_the_package_goes_once_it_exits(app_dir, monkeypatch, capsys):
    package = Package("9.9.9", "pip", ["pip"], after_exit=True)
    monkeypatch.setattr(cli, "find_package", lambda: package)
    monkeypatch.setattr(cli, "remove_package", lambda p: None)

    assert cli.main(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "removing youpdated 9.9.9 once this command exits" in out
    assert "removed youpdated" not in out


def test_uninstall_fails_when_a_file_is_left_in_place(app_dir, removals, capsys):
    _populate(app_dir)
    (app_dir / "notes.txt").write_text("not ours", encoding="utf-8")

    assert cli.main(["uninstall", "--yes"]) == 1
    assert "1 left in place" in capsys.readouterr().out


def test_uninstall_removes_the_state_lock_with_the_state(app_dir, removals):
    from youpdated.state import State

    app_dir.mkdir()
    with State(app_dir / "state.sqlite3"):
        pass
    assert (app_dir / "state.sqlite3.lock").exists()

    assert cli.main(["uninstall", "--yes"]) == 0
    assert not app_dir.exists()


def test_uninstall_removes_the_package_even_with_no_files_left(app_dir, removals):
    assert cli.main(["uninstall", "--yes"]) == 0
    assert len(removals) == 1


def test_uninstall_test_lists_the_package_without_removing_it(app_dir, removals, capsys):
    _populate(app_dir)

    assert cli.main(["uninstall", "--test"]) == 0
    out = capsys.readouterr().out
    assert "youpdated 9.9.9" in out and "pipx uninstall youpdated" in out
    assert app_dir.exists()
    assert removals == []


def test_uninstall_keep_package_leaves_it_installed(app_dir, removals, capsys):
    _populate(app_dir)

    assert cli.main(["uninstall", "--yes", "--keep-package"]) == 0
    assert not app_dir.exists()
    assert removals == []
    assert "9.9.9" not in capsys.readouterr().out


def test_uninstall_fails_when_the_package_stays(app_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_package", lambda: Package("9.9.9", "pip", ["pip"]))
    monkeypatch.setattr(cli, "remove_package", lambda p: "pip exited with status 1")

    assert cli.main(["uninstall", "--yes"]) == 1
    assert "not removed" in capsys.readouterr().out


def test_uninstall_from_source_says_there_is_no_package(app_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_package", lambda: None)

    assert cli.main(["uninstall", "--yes"]) == 0
    assert "runs from source" in capsys.readouterr().out
