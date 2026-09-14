"""Pacing that doesn't hold a lock, and the run hooks that keep a check audible."""

from __future__ import annotations

import io
import threading
import time

import pytest
import respx
from httpx import Response

from youpdated.config import Config, PrivacyConfig
from youpdated.http import Client
from youpdated.models import Target
from youpdated.runner import ProgressReporter, run


# pacing


def test_pace_leaves_a_gap_between_hits_on_one_host():
    client = Client(PrivacyConfig(jitter=(0.05, 0.05)))
    start = time.monotonic()
    for _ in range(3):
        client._pace("example.com")
    # First goes now, the next two wait one gap each.
    assert time.monotonic() - start == pytest.approx(0.10, abs=0.05)


def test_pace_does_not_delay_a_different_host():
    client = Client(PrivacyConfig(jitter=(5.0, 5.0)))
    client._pace("slow.example")
    start = time.monotonic()
    client._pace("other.example")
    assert time.monotonic() - start < 0.5


def test_pace_holds_the_host_lock_only_to_reserve_a_slot():
    client = Client(PrivacyConfig(jitter=(0.3, 0.3)))
    client._pace("example.com")  # seed _host_last so the next call must wait

    with client._registry_lock:
        lock = client._host_locks["example.com"]

    waiting = threading.Event()

    def pace():
        waiting.set()
        client._pace("example.com")

    thread = threading.Thread(target=pace)
    thread.start()
    waiting.wait(timeout=1)
    time.sleep(0.1)  # thread is now inside its reserved wait

    # The lock is free even though the thread has not come back yet
    assert lock.acquire(timeout=0.05)
    lock.release()
    assert thread.is_alive()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_concurrent_pacing_still_spaces_every_caller(monkeypatch):
    """Reserving can't let two threads claim the same instant"""
    client = Client(PrivacyConfig(jitter=(0.05, 0.05)))
    reserved: list[float] = []
    woke: list[float] = []
    lock = threading.Lock()
    real_sleep = time.sleep

    def recording_sleep(wait):
        # _pace sleeps out the slot it reserved, so the deadline it is sleeping
        # towards is that reservation. Assert on it rather than on the wake time:
        # a caller wakes some scheduler-dependent moment *after* its slot, and on
        # a loaded runner that overshoot is wider than the gap being checked for.
        with lock:
            reserved.append(time.monotonic() + wait)
        real_sleep(wait)

    monkeypatch.setattr(time, "sleep", recording_sleep)

    def pace():
        client._pace("example.com")
        with lock:
            woke.append(time.monotonic())

    threads = [threading.Thread(target=pace) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(woke) == 4
    assert len(reserved) == 3

    reserved.sort()
    gaps = [b - a for a, b in zip(reserved, reserved[1:])]
    assert all(gap >= 0.05 - 1e-3 for gap in gaps), gaps
    assert all(gap < 0.5 for gap in gaps), gaps
    # loose timing for windows systems
    assert max(woke) >= max(reserved) - 0.05


# run progress


class Recorder:
    """A ProgressReporter that just remembers the calls"""

    def __init__(self) -> None:
        self.total: int | None = None
        self.started: list[str] = []
        self.finished: list[tuple[str, str | None]] = []
        self._lock = threading.Lock()

    def run_started(self, total: int) -> None:
        self.total = total

    def target_started(self, target: Target) -> None:
        with self._lock:
            self.started.append(target.display)

    def target_finished(self, target: Target, error: Exception | None) -> None:
        with self._lock:
            self.finished.append(
                (target.display, None if error is None else type(error).__name__)
            )


def _config(*repos: str) -> Config:
    return Config(
        sources={"github": list(repos)},
        privacy=PrivacyConfig(jitter=(0.0, 0.0), concurrency=2),
    )


def test_recorder_satisfies_the_protocol():
    assert isinstance(Recorder(), ProgressReporter)


@respx.mock
def test_run_reports_every_target(state, client):
    respx.get(url__regex=r".*").mock(
        return_value=Response(200, text="<feed xmlns='http://www.w3.org/2005/Atom'/>")
    )
    reporter = Recorder()
    config = _config("python/cpython", "astral-sh/uv")

    run(config, state, client, progress=reporter)

    assert reporter.total == 2
    assert sorted(reporter.started) == ["astral-sh/uv", "python/cpython"]
    assert sorted(name for name, _ in reporter.finished) == [
        "astral-sh/uv",
        "python/cpython",
    ]
    assert all(err is None for _, err in reporter.finished)


@respx.mock
def test_run_reports_a_failing_target_with_its_error(state, client):
    respx.get(url__regex=r".*").mock(return_value=Response(500))
    reporter = Recorder()

    result = run(_config("python/cpython"), state, client, progress=reporter)

    assert reporter.total == 1
    assert reporter.started == ["python/cpython"]
    assert reporter.finished == [("python/cpython", "FetchError")]
    # collected, not raised
    assert result.errors


@respx.mock
def test_every_started_target_is_also_finished(state, client):
    respx.get(url__regex=r".*").mock(return_value=Response(500))
    reporter = Recorder()

    run(_config("a/one", "b/two", "c/three"), state, client, progress=reporter)

    assert sorted(reporter.started) == sorted(name for name, _ in reporter.finished)


def test_run_without_a_reporter_is_unchanged(state, client):
    result = run(_config(), state, client)
    assert result.targets == []


@respx.mock
def test_no_reporter_calls_when_there_is_nothing_to_fetch(state, client):
    reporter = Recorder()
    run(Config(sources={}, privacy=PrivacyConfig()), state, client, progress=reporter)
    assert reporter.total is None
    assert reporter.started == []


# the terminal reporter


def _live(width: int = 120):
    from rich.console import Console

    from youpdated.cli import _LiveProgress

    console = Console(file=io.StringIO(), width=width, force_terminal=True)
    return _LiveProgress(console), console


def test_live_progress_satisfies_the_protocol():
    live, _ = _live()
    assert isinstance(live, ProgressReporter)


def test_live_progress_names_the_targets_in_flight():
    live, _ = _live()
    live.run_started(3)
    live.target_started(Target(source="github", key="python/cpython"))
    live.target_started(Target(source="steam", key="440", label="Team Fortress 2"))

    assert live._describe() == "github:python/cpython, steam:Team Fortress 2"


def test_live_progress_summarizes_a_crowded_run():
    live, _ = _live()
    live.run_started(5)
    for i in range(5):
        live.target_started(Target(source="npm", key=f"pkg{i}"))

    assert live._describe() == "npm:pkg0, npm:pkg1 +3 more"


def test_live_progress_drops_a_target_once_it_finishes():
    live, _ = _live()
    live.run_started(2)
    slow = Target(source="feed", key="slow")
    quick = Target(source="feed", key="quick")
    live.target_started(slow)
    live.target_started(quick)
    live.target_finished(quick, None)

    # Only the one still running is named
    assert live._describe() == "feed:slow"


def test_live_progress_counts_failures():
    live, _ = _live()
    live.run_started(2)
    target = Target(source="feed", key="broken")
    live.target_started(target)
    live.target_finished(target, RuntimeError("boom"))

    assert live._describe() == "finishing [1 failed]"


def test_live_progress_renders_and_clears():
    live, console = _live()
    with live:
        live.run_started(2)
        target = Target(source="github", key="python/cpython")
        live.target_started(target)
        live._progress.refresh()
        assert "python/cpython" in console.file.getvalue()
        live.target_finished(target, None)

    # transient=True, so the bar is wiped rather than left in the scrollback.
    assert "python/cpython" not in console.file.getvalue().splitlines()[-1]


def test_live_progress_ignores_calls_before_run_started():
    """run() only calls run_started when there is something to fetch"""
    live, _ = _live()
    live.target_started(Target(source="feed", key="x"))  # must not raise
    assert live._task is None


def test_live_progress_survives_a_collected_target():
    live, _ = _live()
    live.run_started(6)
    for i in range(6):
        live.target_started(Target(source="npm", key=f"pkg{i}"))

    assert len(live._in_flight) == 6
    assert live._describe() == "npm:pkg0, npm:pkg1 +4 more"


def test_live_progress_counts_a_target_listed_twice():
    live, _ = _live()
    live.run_started(2)
    live.target_started(Target(source="feed", key="dupe"))
    live.target_started(Target(source="feed", key="dupe"))

    assert live._describe() == "feed:dupe"

    live.target_finished(Target(source="feed", key="dupe"), None)
    # One copy is still running
    assert live._describe() == "feed:dupe"

    live.target_finished(Target(source="feed", key="dupe"), None)
    assert live._describe() == "finishing"
