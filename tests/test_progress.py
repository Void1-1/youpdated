"""Pacing that doesn't hold a lock, and the run hooks that keep a check audible."""

from __future__ import annotations

import io
import re
import signal
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


def _record_reservations(monkeypatch):
    """Capture the slot each _pace call sleeps towards"""
    reserved: list[float] = []
    lock = threading.Lock()

    def recording_sleep(wait):
        with lock:
            reserved.append(time.monotonic() + wait)

    monkeypatch.setattr(time, "sleep", recording_sleep)
    return reserved


def test_pace_leaves_a_gap_between_hits_on_one_host(monkeypatch):
    client = Client(PrivacyConfig(jitter=(0.05, 0.05)))
    reserved = _record_reservations(monkeypatch)

    for _ in range(3):
        client._pace("example.com")

    # First goes now, the next two each reserve a slot one gap further on
    assert len(reserved) == 2
    assert reserved[1] - reserved[0] >= 0.05 - 1e-3


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
    reserved = _record_reservations(monkeypatch)
    ready = threading.Barrier(4)

    def pace():
        ready.wait(timeout=5)  # all four contend for the host at once
        client._pace("example.com")

    threads = [threading.Thread(target=pace) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)

    # One caller takes the idle host; the other three queue
    assert len(reserved) == 3

    reserved.sort()
    gaps = [b - a for a, b in zip(reserved, reserved[1:])]
    assert all(gap >= 0.05 - 1e-3 for gap in gaps), gaps
    assert all(gap < 0.5 for gap in gaps), gaps


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
        sources={"git": list(repos)},
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
    assert sorted(reporter.started) == ["github.com/astral-sh/uv", "github.com/python/cpython"]
    assert sorted(name for name, _ in reporter.finished) == [
        "github.com/astral-sh/uv",
        "github.com/python/cpython",
    ]
    assert all(err is None for _, err in reporter.finished)


@respx.mock
def test_run_reports_a_failing_target_with_its_error(state, client):
    respx.get(url__regex=r".*").mock(return_value=Response(500))
    reporter = Recorder()

    result = run(_config("python/cpython"), state, client, progress=reporter)

    assert reporter.total == 1
    assert reporter.started == ["github.com/python/cpython"]
    assert reporter.finished == [("github.com/python/cpython", "FetchError")]
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

    console = Console(
        file=io.StringIO(), width=width, force_terminal=True, legacy_windows=False
    )
    return _LiveProgress(console), console


def test_live_progress_satisfies_the_protocol():
    live, _ = _live()
    assert isinstance(live, ProgressReporter)


def test_live_progress_keeps_rich_rendering_on_legacy_windows_consoles():
    from rich.console import Console
    from rich.live_render import LiveRender

    from youpdated.cli import _LiveProgress

    console = Console(file=io.StringIO(), force_terminal=True, legacy_windows=True)
    live = _LiveProgress(console)

    assert type(live._progress.live._live_render) is LiveRender


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

    assert live._describe() == "finishing"
    assert live._failures() == "1 failed"


def _written(console) -> str:
    """everything a console from `_live` has printed so far"""
    assert isinstance(console.file, io.StringIO)
    return console.file.getvalue()


def _rendered(live, console) -> str:
    console.print(live._progress.get_renderable())
    return _written(console)


def test_live_progress_keeps_the_bar_still_as_names_change():
    live, console = _live(width=80)
    live.run_started(3)
    live.target_started(Target(source="npm", key="x"))
    short = _rendered(live, console).splitlines()[-1]
    live.target_started(Target(source="steam", key="1", label="A Much Longer Game Name"))
    long = _rendered(live, console).splitlines()[-1]

    # The bar and counter sit at the same column whatever the names are
    assert short.index("0/3") == long.index("0/3")


def test_live_progress_truncates_long_names_to_one_line():
    live, console = _live(width=60)
    live.run_started(1)
    live.target_started(Target(source="steam", key="1", label="x" * 200))
    out = _rendered(live, console)

    assert len(out.splitlines()) == 1
    assert "0/1" in out
    assert "…" in out


def test_live_progress_shows_names_literally():
    live, console = _live()
    live.run_started(2)
    live.target_started(Target(source="steam", key="1", label="[bold]Game[/bold]"))
    live.target_started(Target(source="steam", key="2", label="[/oops]"))

    out = _rendered(live, console)
    assert "[bold]Game[/bold]" in out
    assert "[/oops]" in out


def test_live_progress_renders_and_clears():
    live, console = _live()
    with live:
        live.run_started(2)
        target = Target(source="github", key="python/cpython")
        live.target_started(target)
        live._progress.refresh()
        assert "python/cpython" in _written(console)
        live.target_finished(target, None)

    # transient=True, so the bar is wiped rather than left in the scrollback.
    assert "python/cpython" not in _written(console).splitlines()[-1]


def _draw(live, console) -> str:
    """draws a frame how a live display might, returns its text"""
    render = live._progress.live._live_render
    render.set_renderable(live._progress.get_renderable())
    with console.capture() as capture:
        console.print(render, end="")
    return re.sub(r"\x1b\[[?\d;]*[A-Za-z]|\r", "", capture.get())


def test_live_progress_frames_are_not_padded_to_the_full_width():
    live, console = _live(width=120)
    live.run_started(2)
    live.target_started(Target(source="github", key="python/cpython"))

    # trailing spaces would rewrap onto extra rows when the window narrows
    assert _draw(live, console).endswith("github:python/cpython")


class _Terminal(io.TextIOBase):
    """minimal terminal for the live display"""

    def __init__(self, width: int, resize: str) -> None:
        self.width, self.resize_mode = width, resize
        self.rows: list[tuple[str, bool]] = [("", False)]  # text, wraps into the next row
        self.row = self.col = 0  # col == width: about to wrap
        self.autowrap = True

    def _goto(self, row: int) -> None:
        self.rows += [("", False)] * (row + 1 - len(self.rows))
        self.row = row

    def _put(self, char: str) -> None:
        if self.col >= self.width and self.autowrap:
            self.rows[self.row] = (self.rows[self.row][0], True)
            self._goto(self.row + 1)
            self.col = 0
        self.col = min(self.col, self.width - 1)
        text, wraps = self.rows[self.row]
        text = text.ljust(self.col)
        self.rows[self.row] = (text[: self.col] + char + text[self.col + 1 :], wraps)
        if self.autowrap or self.col < self.width - 1:
            self.col += 1

    def write(self, data: str) -> int:
        for private, n, code, char in re.findall(r"\x1b\[(\??)([\d;]*)([A-Za-z])|([^\x1b])", data):
            if char == "\r":
                self.col = 0
            elif char == "\n":
                self._goto(self.row + 1)
                self.col = 0
            elif char:
                self._put(char)
            elif private and n == "7":
                self.autowrap = code == "h"
            elif code == "A":
                self.row, self.col = max(self.row - int(n or 1), 0), min(self.col, self.width - 1)
            elif code == "K":
                self.rows[self.row] = ("", False)
            elif code == "J":
                self.rows[self.row] = (self.rows[self.row][0][: self.col], False)
                del self.rows[self.row + 1 :]
        return len(data)

    def resize(self, width: int) -> None:
        if self.resize_mode == "clip":
            self.rows = [(text[:width], wraps) for text, wraps in self.rows]
            self.width, self.col = width, min(self.col, width - 1)
            return
        lines: list[str] = []
        cursor, joined = (0, 0), ""
        for index, (text, wraps) in enumerate(self.rows):
            if index == self.row:
                cursor = (len(lines), len(joined) + self.col)
            joined += text.ljust(self.width) if wraps else text
            if not wraps:
                lines.append(joined)
                joined = ""
        self.width, self.rows = width, []
        for index, line in enumerate(lines):
            clipped = self.resize_mode == "vscode" and index == cursor[0]
            chunks = [line[:width]] if clipped else [
                line[i : i + width] for i in range(0, len(line), width)
            ] or [""]
            if index == cursor[0]:
                row, col = (0, min(cursor[1], width - 1)) if clipped else divmod(cursor[1], width)
                if row >= len(chunks):  # just past the last cell
                    row, col = len(chunks) - 1, width
                self.row, self.col = len(self.rows) + row, col
            self.rows += [(chunk, i < len(chunks) - 1) for i, chunk in enumerate(chunks)]

    def screen(self) -> list[str]:
        rows = [text.rstrip() for text, _ in self.rows]
        while rows and not rows[-1]:
            rows.pop()
        return rows


def _live_on(terminal: _Terminal):
    """a live display drawing to ``terminal`` under a command line"""
    from rich.console import Console

    from youpdated.cli import _LiveProgress

    console = Console(
        file=terminal,
        width=terminal.width,
        height=40,
        force_terminal=True,
        color_system="256",
        legacy_windows=False,
    )
    console.print("$ youpdated")
    live = _LiveProgress(console)
    live._progress.live.auto_refresh = False
    return console, live


def _start(live) -> None:
    live.run_started(4)
    for key in ("python/cpython", "torvalds/linux", "rust-lang/rust"):
        live.target_started(Target(source="github", key=key))
    live._progress.refresh()


@pytest.mark.parametrize("late", [False, True], ids=["width-on-time", "width-late"])
@pytest.mark.parametrize("resize", ["rewrap", "vscode", "clip"])
def test_live_progress_leaves_nothing_behind_when_the_window_is_resized(resize, late):
    """``late``: frames also go out after the terminal resized, before the new width is known"""
    terminal = _Terminal(100, resize)
    console, live = _live_on(terminal)
    with live:
        _start(live)
        for width in (70, 45, 30, 80, 52, 120, 37, 90):
            terminal.resize(width)
            if late:
                live._progress.refresh()
            console.width = width
            live._progress.refresh()
            assert len(terminal.screen()) == 2  # the command and one bar

    console.print("done")
    assert terminal.screen() == ["$ youpdated", "done"]


def test_live_progress_frames_leave_room_to_narrow_into():
    terminal = _Terminal(100, "rewrap")
    console, live = _live_on(terminal)
    with live:
        _start(live)
        for width in (92, 85, 78, 72, 66):  # font size steps
            terminal.resize(width)
            assert not any(wraps for _, wraps in terminal.rows)
            console.width = width
            live._progress.refresh()


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="no SIGWINCH on Windows")
def test_live_progress_hides_while_the_window_is_resized():
    before = signal.getsignal(signal.SIGWINCH)
    terminal = _Terminal(100, "rewrap")
    console, live = _live_on(terminal)
    with live:
        _start(live)
        for width in range(99, 40, -1):  # a drag
            terminal.resize(width)
            assert not any(wraps for _, wraps in terminal.rows)
            console.width = width
            signal.raise_signal(signal.SIGWINCH)
            live._progress.refresh()
            assert terminal.screen() == ["$ youpdated"]

        live._render._hidden_until = 0  # the window stopped changing size
        live._progress.refresh()
        assert len(terminal.screen()) == 2

    assert signal.getsignal(signal.SIGWINCH) == before


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
