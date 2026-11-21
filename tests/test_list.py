"""`youpdated list`"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from youpdated import crypto
from youpdated.cli import main
from rich.console import Console

from youpdated.render.targets import is_stale, render
from youpdated.state import State, stamp, state_lock

from .fake_source import write_items

encryption = pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")


def item(uid: str, **fields: Any) -> dict[str, Any]:
    return {"uid": uid, "published": f"2026-10-{int(uid.split('.')[-1]) + 1:02d}T12:00:00Z", **fields}


@pytest.fixture
def setup(tmp_path: Path, fake: Path):
    """``(config, state)`` paths watching fake `alpha` and `beta`"""
    config = tmp_path / "config.yaml"
    config.write_text(
        "settings:\n  privacy:\n    jitter: [0, 0]\n"
        "sources:\n  fake:\n    - alpha\n    - beta\n",
        encoding="utf-8",
    )
    return config, tmp_path / "state.db"


def check(setup: tuple[Path, Path], *extra: str) -> int:
    config, state = setup
    return main(["check", "-c", str(config), "--state", str(state), "--no-notify", *extra])


def listed(setup: tuple[Path, Path], *extra: str) -> dict[str, Any]:
    config, state = setup
    return json.loads(_run(setup, "--json", *extra))


def _run(setup: tuple[Path, Path], *extra: str) -> str:
    import io
    from contextlib import redirect_stdout

    config, state = setup
    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["list", "-c", str(config), "--state", str(state), *extra]) == 0
    return out.getvalue()


def rows(setup: tuple[Path, Path], *extra: str) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in listed(setup, *extra)["targets"]}


def test_before_any_check_targets_are_listed_unchecked(setup):
    reply = listed(setup)

    assert [row["key"] for row in reply["targets"]] == ["alpha", "beta"]
    assert all(row["latest"] is None and not row["stale"] for row in reply["targets"])
    assert reply["last_run"] is None
    # and the history isn't created
    assert not setup[1].exists()


def test_rows_carry_the_latest_item(setup, fake):
    write_items(fake, {"alpha": [item("1.1"), item("1.3")], "beta": [], "_labels": {"alpha": "Alpha"}})
    assert check(setup) == 0

    reply = listed(setup)
    alpha, beta = reply["targets"]

    assert alpha["latest"]["version"] == "1.3" and alpha["display"] == "Alpha"
    assert alpha["last_checked"] is not None and alpha["highest"] is None
    assert beta["latest"] is None and beta["last_checked"] is not None
    assert reply["last_run_by"] == "cli"
    # the fields the app's targets.list has, and two extra
    assert set(alpha) == {
        "source", "key", "display", "priority", "starred", "last_checked",
        "last_error", "unread", "latest", "stale", "highest",
    }


def test_the_terminal_view_groups_by_source(setup, fake, capsys):
    write_items(fake, {"alpha": [item("1.3")], "beta": [item("2.0", version=None, title="A post")]})
    check(setup)
    capsys.readouterr()

    out = _run(setup)

    assert "fake (2)" in out
    assert "1.3" in out and "A post" in out
    assert "2 targets · last check" in out and "(cli)" in out


def test_a_failure_is_recorded_and_cleared(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "beta": "fail"})
    check(setup)

    beta = rows(setup)["beta"]
    assert beta["stale"] and "beta is down" in beta["last_error"]

    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.2")]})
    check(setup)
    beta = rows(setup)["beta"]
    assert not beta["stale"] and beta["last_error"] is None


def test_a_recent_clean_fetch_is_not_stale(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.1")]})
    check(setup)
    write_items(fake, {"alpha": [item("1.1")], "beta": "fail"})
    check(setup)

    beta = rows(setup)["beta"]
    assert beta["last_error"] and not beta["stale"]

    # the last clean fetch falls outside the grace
    with State(setup[1]) as state:
        old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        state.cache_set("baseline", "fake:beta", old)
    assert rows(setup)["beta"]["stale"]


def test_a_partial_check_does_not_make_the_rest_stale():
    now = datetime.now(timezone.utc)
    old = stamp(now - timedelta(days=6))
    # skipped by the last check, not failing
    assert not is_stale(old, failing=False, now=now)
    assert is_stale(old, failing=True, now=now)
    assert not is_stale(stamp(now - timedelta(hours=11)), failing=True, now=now)
    assert is_stale(None, failing=True, now=now)


def test_wipe_forgets_the_failure(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "beta": "fail"})
    check(setup)
    with State(setup[1]) as state:
        assert ("fake", "beta") in state.failing()
        state.forget("fake", ["beta"])
        assert state.failing() == {}


def test_filters(setup, fake, capsys):
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.2")]})
    check(setup)

    assert list(rows(setup, "ALP")) == ["alpha"]
    assert list(rows(setup, "-s", "fake")) == ["alpha", "beta"]
    assert "1 of 2 targets" in _run(setup, "bet")

    config, state = setup
    capsys.readouterr()
    assert main(["list", "-c", str(config), "--state", str(state), "-s", "nope"]) == 1
    assert "unknown source(s): nope" in capsys.readouterr().out


def test_sorting(setup, fake):
    config, _ = setup
    config.write_text(
        config.read_text(encoding="utf-8") + "    - gamma\n", encoding="utf-8"
    )
    write_items(fake, {"alpha": [item("1.5")], "beta": [item("1.9")], "gamma": [item("1.1")]})
    check(setup)

    assert list(rows(setup)) == ["alpha", "beta", "gamma"]
    assert list(rows(setup, "--sort", "newest")) == ["beta", "alpha", "gamma"]
    out = _run(setup, "--sort", "newest")
    assert "fake (" not in out


def test_a_bump_target_shows_its_highest_release(setup, fake, capsys):
    config, _ = setup
    config.write_text(
        "settings:\n  privacy:\n    jitter: [0, 0]\n"
        "sources:\n  fake:\n    - name: alpha\n      versions: {bump: patch}\n",
        encoding="utf-8",
    )
    # a backport after the highest release
    write_items(fake, {"alpha": [
        item("2.0", version="v2.0", published="2026-10-01T12:00:00Z"),
        item("1.9", version="v1.9", published="2026-10-09T12:00:00Z"),
    ]})
    check(setup)

    (alpha,) = listed(setup)["targets"]
    # nothing bumped over an earlier release yet
    assert alpha["latest"] is None
    # as written, with own date
    assert alpha["highest"] == {"version": "v2.0", "title": "alpha 2.0", "published": "2026-10-01T12:00:00Z"}
    out = _run(setup)
    assert "v2.0" in out and "v1.9" not in out and "nothing found" not in out

    with State(setup[1]) as state:
        state.cache_delete("app", ["top:fake:alpha"])
    write_items(fake, {"alpha": [
        item("3.0", version="v2.0rc1", tags=["prerelease"]),
        item("2.0", version="v2.0"),
    ]})
    check(setup)
    assert listed(setup)["targets"][0]["highest"]["version"] == "v2.0"

    # dropping the rule shows the newest again
    config.write_text(config.read_text(encoding="utf-8").replace("      versions: {bump: patch}\n", ""), encoding="utf-8")
    assert listed(setup)["targets"][0]["highest"] is None


def test_list_does_not_wait_for_a_running_check(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.1")]})
    check(setup)

    with state_lock(setup[1]):
        started = time.monotonic()
        assert rows(setup)["alpha"]["latest"]["version"] == "1.1"
    assert time.monotonic() - started < 5


def test_list_reads_while_another_thread_writes(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.1")]})
    check(setup)
    done = threading.Event()

    def busy() -> None:
        with State(setup[1]) as state:
            while not done.is_set():
                state.cache_set("meta", "poke", str(time.monotonic()))
                # Every commit takes SQLite's exclusive lock
                time.sleep(0.005)

    writer = threading.Thread(target=busy)
    writer.start()
    try:
        for _ in range(5):
            assert rows(setup)["alpha"]["latest"]["version"] == "1.1"
    finally:
        done.set()
        writer.join()


def test_resolved_names_are_kept_by_cli_checks(setup, fake):
    write_items(fake, {"alpha": [item("1.1")], "_labels": {"alpha": "Alpha Game"}})
    check(setup)
    # the name is only known while fetching
    write_items(fake, {})
    assert rows(setup)["alpha"]["display"] == "Alpha Game"


def test_watching_nothing(tmp_path, fake, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("sources: {}\n", encoding="utf-8")
    assert main(["list", "-c", str(config), "--state", str(tmp_path / "s.db")]) == 0
    assert "Not watching anything yet" in capsys.readouterr().out


@pytest.mark.parametrize("flat", [False, True])
def test_narrow_terminals_shorten_names_not_ages(flat):
    now = datetime.now(timezone.utc)
    checked = stamp(now - timedelta(hours=13))
    rows = [
        {
            "source": "homebrew", "key": "formula:wget", "display": "formula:wget",
            "stale": False, "highest": None, "last_checked": checked,
            "latest": {"version": "1.25.0_2", "title": None, "published": None},
        },
        {
            "source": "git", "key": "codeberg.org/forgejo/forgejo",
            "display": "codeberg.org/forgejo/forgejo", "stale": True, "highest": None,
            "last_checked": checked,
            "latest": {
                "version": None, "title": "Team Fortress 2 Update Released",
                "published": stamp(now - timedelta(weeks=102)),
            },
        },
    ]
    console = Console(width=72, color_system=None, record=True)
    render(rows, console, flat=flat, total=2, last_run=now, last_run_by="cli")
    out = console.export_text()
    assert all(len(line.rstrip()) <= 72 for line in out.splitlines())
    for whole in ("102w ago", "undated", "⚠ failing, last ok 13h ago"):
        assert whole in out
    if not flat:
        assert "homebrew (1)" in out
    assert "codeberg.org/forgejo/forgejo" not in out


@encryption
def test_an_encrypted_history_is_read(setup, fake, monkeypatch):
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, "correct horse battery staple")
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("1.2")]})
    check(setup)
    config, state = setup
    assert main(["encrypt", "-c", str(config), "--state", str(state)]) == 0
    before = state.read_bytes()

    assert rows(setup)["beta"]["latest"]["version"] == "1.2"
    assert state.read_bytes() == before
