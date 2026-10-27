"""`pytest examples`"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

import youpdated_desk as desk
from youpdated import crypto
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


def test_windows_toast_takes_its_text_from_the_environment(monkeypatch):
    spawned = []
    monkeypatch.setattr(desk.sys, "platform", "win32")
    monkeypatch.setattr(desk, "_spawn", lambda cmd, env=None: spawned.append((cmd, env)))
    # PowerShell ends a '...' string at a curly quote too, so this would run calc
    title = "x’+(Start-Process calc)+’"

    desk.notify(title, "Don’t miss it")

    ((cmd, env),) = spawned
    assert "calc" not in " ".join(cmd)
    assert env["YOUPDATED_TOAST_TITLE"] == title
    assert env["YOUPDATED_TOAST_BODY"] == "Don’t miss it"
    assert "$env:YOUPDATED_TOAST_TITLE" in cmd[-1]


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
    assert lines[0] == "python/cpython: v0"
    assert lines[-1] == "and 2 more"


def test_summary_falls_back_to_the_raw_target():
    assert desk._summary([make_update()], {}).startswith("python/cpython: ")


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


# priority and sorting


def at(hours_ago):
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def titles(updates):
    return [u.title for u in updates]


def test_newest_sort_puts_undated_last():
    ups = [
        make_update(title="undated", published=None),
        make_update(title="old", published=at(9)),
        make_update(title="new", published=at(1)),
    ]
    assert titles(desk.sort_updates(ups, "newest", desk.Prefs(), {})) == ["new", "old", "undated"]


def test_priority_sort_ranks_high_then_normal_then_low_newest_first_within():
    prefs = desk.Prefs()
    prefs.set_priority("npm", "left-pad", "low")
    prefs.set_priority("steam", "440", "high")
    ups = [
        make_update(source="npm", target="left-pad", title="low", published=at(0)),
        make_update(title="normal old", published=at(5)),
        make_update(title="normal new", published=at(1)),
        make_update(source="steam", target="440", title="high", published=at(9)),
    ]
    assert titles(desk.sort_updates(ups, "priority", prefs, {})) == [
        "high", "normal new", "normal old", "low"
    ]


def test_source_and_name_sorts_fall_back_to_newest_first():
    ups = [
        make_update(source="steam", target="440", title="tf2", published=at(1)),
        make_update(source="git", target="b/b", title="b old", published=at(5)),
        make_update(source="git", target="b/b", title="b new", published=at(2)),
        make_update(source="git", target="a/a", title="a", published=at(9)),
    ]
    labels = {("steam", "440"): "Team Fortress 2"}  # sorted by label, not the raw key
    prefs = desk.Prefs()
    assert titles(desk.sort_updates(ups, "source", prefs, labels)) == ["b new", "b old", "a", "tf2"]
    assert titles(desk.sort_updates(ups, "name", prefs, labels)) == ["a", "b new", "b old", "tf2"]


def test_grouping_puts_each_target_where_its_first_update_falls():
    ups = [
        make_update(target="a/a", title="a new"),
        make_update(target="b/b", title="b"),
        make_update(target="a/a", title="a old"),
    ]
    assert [titles(g) for g in desk.group_updates(ups)] == [["a new", "a old"], ["b"]]


def test_prefs_live_beside_the_history():
    assert desk.prefs_path(Path("/x/state.sqlite3")) == Path("/x/state.desk.json")


def test_prefs_round_trip_and_normal_is_not_stored(tmp_path):
    path = tmp_path / "state.desk.json"
    prefs = desk.Prefs(path)
    prefs.sort = "name"
    prefs.set_priority("git", "python/cpython", "high")
    prefs.set_priority("npm", "left-pad", "low")
    prefs.set_priority("npm", "left-pad", "normal")  # back to normal, so forgotten
    prefs.save()

    loaded = desk.Prefs.load(path)
    assert loaded.sort == "name"
    assert loaded.levels == {("git", "python/cpython"): "high"}


@pytest.mark.skipif(not crypto.available(), reason="needs the cryptography backend")
def test_prefs_are_encrypted_when_the_history_is(tmp_path):
    path = tmp_path / "state.desk.json"
    prefs = desk.Prefs(path, passphrase="hunter2")
    prefs.set_priority("git", "python/cpython", "high")
    prefs.save()

    assert crypto.is_encrypted_file(path)
    assert b"cpython" not in path.read_bytes()
    assert desk.Prefs.load(path, "hunter2").priority("git", "python/cpython") == "high"
    assert desk.Prefs.load(path).levels == {}           # no passphrase: defaults
    assert desk.Prefs.load(path, "wrong").levels == {}


@pytest.mark.parametrize(
    "content",
    [None, b"", b"not json", b"[1, 2]", b'{"sort": "sideways", "priority": {"git": {"a/b": "urgent"}}}'],
)
def test_missing_or_odd_prefs_fall_back_to_defaults(tmp_path, content):
    path = tmp_path / "state.desk.json"
    if content is not None:
        path.write_bytes(content)
    prefs = desk.Prefs.load(path)
    assert prefs.sort == "priority" and prefs.levels == {}


# digest


def test_parse_clock():
    assert desk.parse_clock("18:00") == time(18, 0)
    with pytest.raises(ValueError, match="like 18:00"):
        desk.parse_clock("6pm")


def test_next_digest_is_today_until_it_passes():
    six = time(18, 0)
    assert desk.next_digest(six, datetime(2026, 10, 26, 17, 59)) == datetime(2026, 10, 26, 18, 0)
    assert desk.next_digest(six, datetime(2026, 10, 26, 18, 0)) == datetime(2026, 10, 27, 18, 0)


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


def test_each_target_becomes_one_card(app):
    ups = [make_update(target=f"o/r{n}", title=f"v{n}") for n in range(4)]
    app._apply(desk.Outcome(updates=ups, labels={}))
    app.root.update_idletasks()
    assert len(cards(app)) == 4


def card_lines(card):
    """A card's update lines, then its `and N more` if any"""
    inner = card.winfo_children()[1]
    return [
        w.winfo_children()[0].cget("text") if isinstance(w, tk.Frame) else w.cget("text")
        for w in inner.winfo_children()[1:]
    ]


def test_a_targets_updates_share_one_card_newest_first(app):
    ups = [make_update(title=f"v{n}", published=at(n)) for n in range(5)]
    ups.append(make_update(target="o/other", title="other", published=at(2.5)))
    app._apply(desk.Outcome(updates=ups, labels={}))
    assert [card_lines(c) for c in cards(app)] == [["v0", "v1", "v2", "and 2 more"], ["other"]]


def test_each_line_opens_its_own_link(app, monkeypatch):
    opened = []
    monkeypatch.setattr(desk.webbrowser, "open", opened.append)
    app.root.deiconify()
    app._apply(desk.Outcome(updates=[
        make_update(title="new", url="https://example.com/new", published=at(1)),
        make_update(title="old", url="https://example.com/old", published=at(5)),
    ], labels={}))
    app.root.update()
    for row in cards(app)[0].winfo_children()[1].winfo_children()[1:]:
        row.winfo_children()[0].event_generate("<Button-1>", x=2, y=2)
    app.root.update()
    assert opened == ["https://example.com/new", "https://example.com/old"]


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
    assert sent == [("1 update", "python/cpython: v3.14.7")]


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


def many_targets(count):
    return [make_update(target=f"o/r{n}") for n in range(count)]


def test_scrollregion_is_exactly_the_content(app):
    app._apply(desk.Outcome(updates=many_targets(20), labels={}))
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
    app._apply(desk.Outcome(updates=many_targets(40), labels={}))
    app.root.update_idletasks()
    app._measure()
    monkeypatch.setattr(desk.sys, "platform", platform)

    app.canvas.yview_moveto(0.5)
    before = app.canvas.yview()[0]
    app._scroll(wheel(delta=delta, num=num))
    after = app.canvas.yview()[0]
    assert (after < before) if expect_up else (after > before)


def scrolled_by(app, handler, event):
    app._apply(desk.Outcome(updates=many_targets(40), labels={}))
    app.root.update_idletasks()
    app._measure()
    app.canvas.yview_moveto(0.5)
    before = app.canvas.canvasy(0)
    handler(event)
    return app.canvas.canvasy(0) - before


def test_a_tk9_macos_wheel_notch_scrolls_like_windows(app, monkeypatch):
    """Tk 9 reports a macOS notch as 120, not the 1 Tk 8.6 did; times 6 would jump 720px"""
    monkeypatch.setattr(desk.sys, "platform", "darwin")
    monkeypatch.setattr(desk.tk, "TkVersion", 9.1)
    assert scrolled_by(app, app._scroll, wheel(delta=-120)) == 48


@pytest.mark.parametrize("dx, dy, expect", [(0, -12, 12), (0, 12, -12), (-5, -3, 3), (7, 0, 0)])
def test_touchpad_scrolls_by_its_y_pixels(app, dx, dy, expect):
    packed = (dx << 16) | (dy & 0xFFFF)
    assert scrolled_by(app, app._touchpad, wheel(delta=packed)) == expect


@pytest.mark.skipif(tk.TkVersion < 9, reason="Tk 8.6 has no <TouchpadScroll>")
def test_touchpad_scroll_is_bound(app):
    assert app.canvas.bind_all("<TouchpadScroll>")


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


# gui: priority and sorting


def card_texts(app):
    """Each card's first line, top to bottom"""
    return [card_lines(card)[0] for card in cards(app)]


def test_the_list_follows_the_chosen_sort(app):
    app._apply(desk.Outcome(updates=[
        make_update(target="b/b", title="b", published=at(1)),
        make_update(target="a/a", title="a", published=at(5)),
    ], labels={}))
    assert card_texts(app) == ["b", "a"]

    app.sort_var.set("name")
    app._sorted()
    assert card_texts(app) == ["a", "b"]
    assert app.sort_button.cget("text") == "Sort: Name ▾"


def test_setting_a_priority_reorders_tags_and_saves(root, monkeypatch, tmp_path):
    import queue
    monkeypatch.setattr(desk, "notify", lambda *a: None)
    prefs = desk.Prefs(tmp_path / "state.desk.json")
    built = desk.App(root, FakeChecker(), queue.Queue(), prefs)
    built._apply(desk.Outcome(updates=[
        make_update(target="a/a", title="a", published=at(1)),
        make_update(target="b/b", title="b", published=at(5)),
    ], labels={}))

    built.set_priority("github", "b/b", "high")
    assert card_texts(built) == ["b", "a"]
    top = cards(built)[0].winfo_children()[1].winfo_children()[0]
    assert "high" in [w.cget("text") for w in top.winfo_children()]
    assert desk.Prefs.load(prefs.path).priority("github", "b/b") == "high"


def test_the_priority_menu_shows_and_sets_the_level(app):
    app.prefs.set_priority("github", "python/cpython", "low")
    menu = app._priority_menu_for(make_update())
    assert menu.entrycget(0, "label") == "python/cpython"
    assert [menu.entrycget(i, "label") for i in (2, 3, 4)] == ["High", "Normal", "Low"]
    assert app.priority_var.get() == "low"

    menu.invoke(2)
    assert app.prefs.priority("github", "python/cpython") == "high"


# gui: notifications


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(desk, "notify", lambda title, body: out.append((title, body)))
    return out


def make_app(root, **kw):
    import queue
    built = desk.App(root, FakeChecker(), queue.Queue(), desk.Prefs(), **kw)
    built.prefs.set_priority("steam", "440", "high")
    built.prefs.set_priority("npm", "left-pad", "low")
    return built


def test_high_targets_notify_on_their_own_and_low_never_does(root, sent):
    built = make_app(root)
    built._apply(desk.Outcome(updates=[
        make_update(),
        make_update(source="steam", target="440", title="Patch 1"),
        make_update(source="steam", target="440", title="Patch 2"),
        make_update(source="npm", target="left-pad", title="1.3.1"),
    ], labels={("steam", "440"): "Team Fortress 2"}))
    assert sent == [
        ("Team Fortress 2", "Patch 1\nPatch 2"),
        ("1 update", "python/cpython: v3.14.7"),
    ]


def test_only_low_updates_send_nothing(root, sent):
    make_app(root)._apply(desk.Outcome(updates=[make_update(source="npm", target="left-pad")], labels={}))
    assert sent == []


def test_bundle_high_puts_high_first_in_the_one_notification(root, sent):
    built = make_app(root, bundle_high=True)
    built._apply(desk.Outcome(updates=[
        make_update(),
        make_update(source="steam", target="440", title="Patch 1"),
    ], labels={}))
    assert sent == [("2 updates", "440: Patch 1\npython/cpython: v3.14.7")]


def test_the_digest_holds_the_rest_until_its_time(root, sent):
    built = make_app(root, digest=time(18, 0))
    built._apply(desk.Outcome(updates=[
        make_update(),
        make_update(source="steam", target="440", title="Patch 1"),
    ], labels={}))
    assert sent == [("440", "Patch 1")]  # high went out as found

    built._digest_check()
    assert len(sent) == 1                # not due yet

    built.digest_due = datetime.now() - timedelta(seconds=1)
    built._digest_check()
    assert sent[1:] == [("1 update", "python/cpython: v3.14.7")]
    assert built.pending == [] and built.digest_due > datetime.now()

    built.digest_due = datetime.now() - timedelta(seconds=1)
    built._digest_check()
    assert len(sent) == 2                # nothing held, nothing sent


# cli


def test_a_bad_interval_is_rejected():
    with pytest.raises(SystemExit) as exit_info:
        desk.main(["--every", "soon"])
    assert exit_info.value.code == 2


def test_a_bad_digest_time_is_rejected():
    with pytest.raises(SystemExit) as exit_info:
        desk.main(["--digest", "6pm"])
    assert exit_info.value.code == 2


def test_a_missing_config_is_an_error_not_a_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        desk.main(["-c", str(tmp_path / "nope.yaml")])
    assert exit_info.value.code == 2
    assert "config file not found" in capsys.readouterr().err
