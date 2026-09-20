"""`pytest examples`"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta, timezone

import pytest

import youpdated_desk as desk
from youpdated.models import RunError, Target, Update
from youpdated.runner import RunResult


def make_update(source="github", target="python/cpython", title="v3.14.7", **kw):
    published = kw.pop("published", datetime.now(timezone.utc) - timedelta(hours=4))
    return Update(
        source=source, target=target, uid=kw.pop("uid", title), title=title,
        url=kw.pop("url", "https://example.com/x"), published=published, **kw
    )


# notifications


@pytest.mark.parametrize(
    "text",
    [
        "python/cpython — v3.14.7",     # em dash: json.dumps would emit —
        'CLTF2 "Halloween" Cup',        # embedded quotes
        "a\\b",                         # backslash
        "line one\nline two",           # newline
        "naïve café 🎮",                # assorted non-ascii
    ],
)
def test_applescript_quoting_avoids_ascii_escapes(text):
    quoted = desk._quote_applescript(text)
    assert quoted.startswith('"') and quoted.endswith('"')
    assert "\\u" not in quoted           # AppleScript cannot parse \uXXXX
    assert "\n" not in quoted            # a raw newline would end the literal
    inner = quoted[1:-1]
    assert inner.replace('\\"', "").count('"') == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="AppleScript is macOS only")
@pytest.mark.parametrize("text", ["python/cpython — v3.14.7", 'a "quoted" thing', "a\\b"])
def test_applescript_quoting_is_accepted_by_osascript(text):
    # `return <literal>` parses the same literal a notification would, without showing one.
    done = subprocess.run(
        ["osascript", "-e", f"return {desk._quote_applescript(text)}"],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == text.replace("\n", "\\n")


def test_powershell_quoting_doubles_single_quotes():
    assert desk._quote_powershell("it's") == "it''s"


def test_notify_never_raises(monkeypatch):
    monkeypatch.setattr(desk, "_spawn", lambda cmd: (_ for _ in ()).throw(OSError("nope")))
    desk.notify("title", "body")  # swallowed


# summaries


def test_headline_is_singular_for_one():
    assert desk._headline([make_update()]) == "1 update"
    assert desk._headline([make_update(), make_update()]) == "2 updates"


def test_summary_lists_three_then_counts_the_rest():
    ups = [make_update(title=f"v{n}", uid=str(n)) for n in range(5)]
    labels = {("github", "python/cpython"): "python/cpython"}
    lines = desk._summary(ups, labels).splitlines()
    assert len(lines) == 4
    assert lines[0] == "python/cpython — v0"
    assert lines[-1] == "and 2 more"


def test_summary_falls_back_to_the_raw_target():
    assert desk._summary([make_update()], {}).startswith("python/cpython — ")


def test_first_error_counts_the_others():
    result = RunResult(errors=[RunError("github", "a", "boom"), RunError("npm", "b", "bang")])
    assert desk._first_error(result) == "github/a: boom (+1 more)"
    assert desk._first_error(RunResult()) is None


def test_labels_use_the_resolved_display_name():
    result = RunResult(targets=[Target(source="steam", key="440", label="Team Fortress 2")])
    assert desk._labels(result) == {("steam", "440"): "Team Fortress 2"}


# checker


def test_once_reports_a_bad_config_instead_of_raising(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("sources: [not, a, mapping]\n")
    outcome = desk.Checker(str(bad), str(tmp_path / "s.db"), 60, None)._once()
    assert outcome.error and not outcome.updates


def test_once_reports_a_missing_config(tmp_path):
    outcome = desk.Checker(str(tmp_path / "nope.yaml"), str(tmp_path / "s.db"), 60, None)._once()
    assert outcome.error


def test_state_path_defaults_when_not_given():
    assert desk.Checker(None, None, 60, None).state_path is not None


# gui


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:                      # no display
        pytest.skip(f"no Tk display: {exc}")
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass  # a test already closed it


class FakeChecker:
    passphrase = None
    state_path = None

    def __init__(self):
        self.checks = 0
        self.stopped = False

    def start(self): pass
    def stop(self): self.stopped = True
    def check_now(self): self.checks += 1


@pytest.fixture
def app(root, monkeypatch):
    monkeypatch.setattr(desk, "notify", lambda *a: None)
    import queue
    built = desk.App(root, FakeChecker(), queue.Queue())
    root.update_idletasks()
    return built


def wheel(delta=0, num=0):
    event = tk.Event()
    event.delta, event.num = delta, num
    return event


def cards(app):
    return app.body.winfo_children()


def test_empty_state_renders_a_single_placeholder(app):
    app._render()
    app.root.update_idletasks()
    assert len(cards(app)) == 1


def test_each_update_becomes_one_card(app):
    ups = [make_update(uid=str(n), title=f"v{n}") for n in range(4)]
    app._apply(desk.Outcome(updates=ups, labels={}))
    app.root.update_idletasks()
    assert len(cards(app)) == 4


def test_updates_accumulate_newest_first_and_stay_capped(app):
    app._apply(desk.Outcome(updates=[make_update(uid="old", title="old")], labels={}))
    app._apply(desk.Outcome(updates=[make_update(uid="new", title="new")], labels={}))
    assert [u.title for u in app.updates] == ["new", "old"]

    app._apply(desk.Outcome(updates=[make_update(uid=str(n)) for n in range(300)], labels={}))
    assert len(app.updates) == 200


def test_a_result_with_updates_notifies(root, monkeypatch):
    import queue
    sent = []
    monkeypatch.setattr(desk, "notify", lambda title, body: sent.append((title, body)))
    built = desk.App(root, FakeChecker(), queue.Queue())
    built._apply(desk.Outcome(updates=[make_update()], labels={}))
    assert sent == [("1 update", "python/cpython — v3.14.7")]


def test_an_empty_result_does_not_notify(root, monkeypatch):
    import queue
    sent = []
    monkeypatch.setattr(desk, "notify", lambda title, body: sent.append(1))
    built = desk.App(root, FakeChecker(), queue.Queue())
    built._apply(desk.Outcome(updates=[], labels={}))
    assert sent == []


def test_baseline_is_shown_in_the_status_line(app):
    app._apply(desk.Outcome(updates=[], labels={}, baseline=True))
    assert app.status.cget("text").startswith("Baseline recorded")


def test_a_source_failure_reaches_the_status_bar(app):
    app._apply(desk.Outcome(updates=[], labels={}, error="github/x: boom"))
    assert app.problem.cget("text") == "github/x: boom"


def test_scrollregion_is_exactly_the_content(app):
    app._apply(desk.Outcome(updates=[make_update(uid=str(n)) for n in range(20)], labels={}))
    app.root.update_idletasks()
    app._measure()
    _, top, _, bottom = app.canvas.cget("scrollregion").split()
    assert int(top) == 0 # nothing above the first card
    assert int(bottom) == app.body.winfo_reqheight() # and nothing below the last


def test_scrolling_is_refused_when_it_all_fits(app):
    app.root.deiconify()
    app.root.geometry("600x700")      # taller than the one card below
    app._apply(desk.Outcome(updates=[make_update()], labels={}))
    app.root.update()
    before = app.canvas.yview()
    app._scroll(tk.Event())           # a bare event would raise if it got that far
    assert app.canvas.yview() == before


@pytest.mark.parametrize(
    "platform, delta, num, expect_up",
    [("darwin", 3, 0, True), ("darwin", -3, 0, False),
     ("win32", 120, 0, True), ("win32", -120, 0, False)],
)
def test_wheel_direction_per_platform(app, monkeypatch, platform, delta, num, expect_up):
    """A macOS delta of 3 must not floor to zero the way `delta // 120` did."""
    app._apply(desk.Outcome(updates=[make_update(uid=str(n)) for n in range(40)], labels={}))
    app.root.update_idletasks()
    app._measure()
    monkeypatch.setattr(desk.sys, "platform", platform)

    app.canvas.yview_moveto(0.5)
    before = app.canvas.yview()[0]
    app._scroll(wheel(delta=delta, num=num))
    after = app.canvas.yview()[0]
    assert (after < before) if expect_up else (after > before)


def test_check_now_button_asks_the_checker(app):
    app.root.deiconify()
    app.root.update()
    app.button.event_generate("<Button-1>", x=4, y=4)
    app.root.update()
    assert app.checker.checks == 1


def test_closing_stops_the_checker(root):
    import queue
    checker = FakeChecker()
    built = desk.App(root, checker, queue.Queue())
    built.quit()
    assert checker.stopped


# cli


def test_a_bad_interval_is_rejected():
    with pytest.raises(SystemExit) as exit_info:
        desk.main(["--every", "soon"])
    assert exit_info.value.code == 2
