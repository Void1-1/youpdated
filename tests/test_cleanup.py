"""Uninstall: find traces, and refusing to delete other stuff"""

from __future__ import annotations

import pytest

from youpdated import cleanup
from youpdated.cleanup import Trace, find_traces, package_removal_command, remove_traces


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """Point config and data at temp directory"""
    directory = tmp_path / "youpdated"
    # find_traces() also scans the working directory for a project config, so move off the checkout
    monkeypatch.chdir(tmp_path)
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


def test_package_removal_command_targets_the_running_interpreter():
    import sys

    command = package_removal_command()
    assert command.startswith(sys.executable)
    assert command.endswith("-m pip uninstall youpdated")
