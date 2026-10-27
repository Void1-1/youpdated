#!/usr/bin/env python3
"""A small desktop ui for youpdated"""

# should be cross platform compatable, open a github issue if i forgot something

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from tkinter import font as tkfont
from tkinter import simpledialog

from youpdated import crypto
from youpdated.config import (
    ConfigError,
    default_state_path,
    find_config,
    load_config,
)
from youpdated.http import Client, ProxyUnavailable, probe_proxy
from youpdated.models import Update
from youpdated.render.terminal import relative_age
from youpdated.runner import RunResult, parse_since, run
from youpdated.state import State

APP = "Youpdated"

BG = "#16161a"
CARD = "#1e1e24"
CARD_HOVER = "#26262e"
LINE = "#2c2c34"
TEXT = "#e8e8ee"
MUTED = "#82828e"
ACCENT = "#7aa2f7"
BAD = "#e0736d"
HIGH = "#e0af68"
DIM = "#5a5a64"
THUMB = "#33333d"
THUMB_HOVER = "#45454f"

PRIORITIES = ("high", "normal", "low")
SORTS = {"priority": "Priority", "newest": "Newest", "source": "Source", "name": "Name"}

SOURCE_COLOR = {
    "git": "#a9b1d6",
    "npm": "#e0736d",
    "steam": "#7aa2f7",
    "itch": "#f38ba8",
    "youtube": "#e06c75",
    "browser": "#9ece6a",
    "feed": "#e5c07b",
}


# notifications


def notify(title: str, body: str) -> None:
    """Best-effort desktop notification"""
    try:
        if sys.platform == "darwin":
            script = f"display notification {_quote_applescript(body)} with title {_quote_applescript(title)}"
            _spawn(["osascript", "-e", script])
        elif sys.platform == "win32":
            _spawn(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _TOAST_SCRIPT.replace("{TITLE}", _quote_powershell(title)).replace("{BODY}", _quote_powershell(body)),
                ]
            )
        else:
            _spawn(["notify-send", "-a", APP, title, body])
    except Exception:
        pass


def _quote_applescript(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _quote_powershell(text: str) -> str:
    return text.replace("'", "''")


# ew, its so ugly looking
_TOAST_SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode('{TITLE}')) > $null
$n.Item(1).AppendChild($t.CreateTextNode('{BODY}')) > $null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show(
    [Windows.UI.Notifications.ToastNotification]::new($t))
"""


def _spawn(cmd: list[str]) -> None:
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)


# checking


@dataclass
class Outcome:
    updates: list[Update]
    labels: dict[tuple[str, str], str]
    error: str | None = None
    baseline: bool = False


class Checker:
    """Runs youpdated in a interval in background thread"""

    def __init__(self, config_path: str | None, state_path: str | None, interval: float, out: queue.Queue):
        self.config_path = config_path
        self.state_path = Path(state_path) if state_path else default_state_path()
        self.interval = interval
        self.out = out
        self.passphrase: str | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def check_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.out.put(("busy", None))
            self.out.put(("result", self._once()))
            self._wake.wait(self.interval)
            self._wake.clear()

    def _once(self) -> Outcome:
        try:
            config = load_config(self.config_path, passphrase=self.passphrase)
            if config.privacy.proxy:
                probe_proxy(config.privacy.proxy, timeout=config.privacy.timeout)
            with State(self.state_path, passphrase=self.passphrase) as state:
                with Client(config.privacy, state) as client:
                    result = run(config, state, client)
            return Outcome(
                updates=result.updates,
                labels=_labels(result),
                baseline=result.baseline,
                error=_first_error(result),
            )
        except (ConfigError, ProxyUnavailable) as exc:
            return Outcome([], {}, error=str(exc).splitlines()[0])
        except Exception as exc:
            return Outcome([], {}, error=f"{type(exc).__name__}: {exc}")


def _labels(result: RunResult) -> dict[tuple[str, str], str]:
    return {(t.source, t.key): t.display for t in result.targets}


def _first_error(result: RunResult) -> str | None:
    if not result.errors:
        return None
    first = result.errors[0]
    extra = f" (+{len(result.errors) - 1} more)" if len(result.errors) > 1 else ""
    return f"{first.source}/{first.target}: {first.message}{extra}"


# priority and sorting


def prefs_path(state_path: Path) -> Path:
    """Beside the history it describes: state.sqlite3 -> state.desk.json"""
    return state_path.with_suffix(".desk.json")


class Prefs:
    """Desk-only settings: each target's priority and the list's sort.

    Kept out of the youpdated config on purpose (#12), and encrypted whenever the history is
    """

    def __init__(self, path: Path | None = None, passphrase: str | None = None):
        self.path = path
        self.passphrase = passphrase
        self.sort = "priority"
        # only targets moved off normal are kept
        self.levels: dict[tuple[str, str], str] = {}

    @classmethod
    def load(cls, path: Path, passphrase: str | None = None) -> Prefs:
        prefs = cls(path, passphrase)
        try:
            blob = path.read_bytes()
            if crypto.is_encrypted(blob):
                if not passphrase:
                    return prefs
                blob = crypto.decrypt(blob, passphrase)
            raw = json.loads(blob)
            if raw.get("sort") in SORTS:
                prefs.sort = raw["sort"]
            for source, targets in raw.get("priority", {}).items():
                for target, level in targets.items():
                    if level in PRIORITIES:
                        prefs.set_priority(source, target, level)
        except (OSError, ValueError, AttributeError, TypeError, crypto.EncryptionError):
            pass  # missing or unreadable: start from defaults rather than refuse to open
        return prefs

    def priority(self, source: str, target: str) -> str:
        return self.levels.get((source, target), "normal")

    def set_priority(self, source: str, target: str, level: str) -> None:
        if level == "normal":
            self.levels.pop((source, target), None)
        else:
            self.levels[(source, target)] = level

    def save(self) -> None:
        if self.path is None:
            return
        nested: dict[str, dict[str, str]] = {}
        for (source, target), level in sorted(self.levels.items()):
            nested.setdefault(source, {})[target] = level
        blob = json.dumps({"sort": self.sort, "priority": nested}, indent=2).encode()
        if self.passphrase:
            blob = crypto.encrypt(blob, self.passphrase)
        crypto.write_private(self.path, blob)


_UNDATED = datetime.min.replace(tzinfo=timezone.utc)
_RANK = {level: rank for rank, level in enumerate(PRIORITIES)}


def sort_updates(
    updates: list[Update], how: str, prefs: Prefs, labels: dict[tuple[str, str], str]
) -> list[Update]:
    """`updates` in display order. Sorts are stable, so newest first breaks every tie"""
    ordered = sorted(updates, key=lambda u: u.published or _UNDATED, reverse=True)
    if how == "priority":
        ordered.sort(key=lambda u: _RANK[prefs.priority(u.source, u.target)])
    elif how == "source":
        ordered.sort(key=lambda u: u.source)
    elif how == "name":
        ordered.sort(key=lambda u: labels.get((u.source, u.target), u.target).casefold())
    return ordered


def group_updates(ordered: list[Update]) -> list[list[Update]]:
    """One group per target, placed where its first update falls, keeping the order within"""
    groups: dict[tuple[str, str], list[Update]] = {}
    for update in ordered:
        groups.setdefault((update.source, update.target), []).append(update)
    return list(groups.values())


# digest


def parse_clock(value: str) -> time:
    """`18:00`, local time"""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise ValueError(f"expected a time like 18:00, got {value!r}") from None


def next_digest(at: time, now: datetime) -> datetime:
    """The first `at` after `now`"""
    due = datetime.combine(now.date(), at)
    return due if due > now else due + timedelta(days=1)


# ui


class Scrollbar(tk.Canvas):
    def __init__(self, parent: tk.Widget, target: tk.Canvas):
        super().__init__(parent, width=8, bg=BG, highlightthickness=0, bd=0)
        self.target = target
        self.thumb = self.create_rectangle(0, 0, 0, 0, fill=THUMB, outline="")
        self.span = (0.0, 1.0)
        self.grab: tuple[int, float] | None = None

        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "grab", None))
        self.bind("<Enter>", lambda _e: self.itemconfigure(self.thumb, fill=THUMB_HOVER))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self.thumb, fill=THUMB))

    def set(self, first: str, last: str) -> None:
        self.span = (float(first), float(last))
        self._draw()

    def _draw(self) -> None:
        first, last = self.span
        height = self.winfo_height()
        if last - first >= 1.0:  # nothing to scroll
            self.coords(self.thumb, 0, 0, 0, 0)
            return
        top = first * height
        bottom = max(last * height, top + 28)
        self.coords(self.thumb, 1, top, 7, min(bottom, height))

    def _press(self, event: tk.Event) -> None:
        first, last = self.span
        height = self.winfo_height() or 1
        if not first * height <= event.y <= last * height:
            self.target.yview_moveto(max(0.0, event.y / height - (last - first) / 2))
        self.grab = (event.y, self.span[0])

    def _drag(self, event: tk.Event) -> None:
        if self.grab is None:
            return
        origin, first = self.grab
        height = self.winfo_height() or 1
        self.target.yview_moveto(max(0.0, first + (event.y - origin) / height))


class App:
    def __init__(
        self,
        root: tk.Tk,
        checker: Checker,
        events: queue.Queue,
        prefs: Prefs | None = None,
        *,
        bundle_high: bool = False,
        digest: time | None = None,
    ):
        self.root = root
        self.checker = checker
        self.queue = events
        self.updates: list[Update] = []
        self.labels: dict[tuple[str, str], str] = {}
        self.prefs = prefs or Prefs()
        self.bundle_high = bundle_high
        self.digest_due = next_digest(digest, datetime.now()) if digest is not None else None
        # updates held for the digest
        self.pending: list[Update] = []

        root.title(APP)
        root.geometry("620x680")
        root.minsize(420, 320)
        root.configure(bg=BG)

        family = _ui_font()
        self.f_title = tkfont.Font(family=family, size=13, weight="bold")
        self.f_body = tkfont.Font(family=family, size=12)
        self.f_small = tkfont.Font(family=family, size=10)
        self.f_strong = tkfont.Font(family=family, size=12, weight="bold")

        self._build_header()
        self._build_list()
        self._build_status()

        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(100, self._drain)

    # header

    def _build_header(self) -> None:
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(16, 10))

        tk.Label(bar, text=APP, bg=BG, fg=TEXT, font=self.f_title).pack(side="left")

        self.button = tk.Label(
            bar, text="Check now", bg=CARD, fg=TEXT, font=self.f_small,
            padx=12, pady=5, cursor="hand2",
        )
        self.button.pack(side="right")
        self.button.bind("<Button-1>", lambda _e: self.checker.check_now())
        _hover(self.button, CARD, CARD_HOVER)

        self.sort_var = tk.StringVar(self.root, self.prefs.sort)
        self.sort_menu = _menu(self.root)
        for key, name in SORTS.items():
            self.sort_menu.add_radiobutton(
                label=name, value=key, variable=self.sort_var, command=self._sorted
            )
        self.sort_button = tk.Label(
            bar, text=_sort_text(self.prefs.sort), bg=CARD, fg=TEXT, font=self.f_small,
            padx=12, pady=5, cursor="hand2",
        )
        self.sort_button.pack(side="right", padx=(0, 8))
        self.sort_button.bind("<Button-1>", lambda _e: _popup(
            self.sort_menu,
            self.sort_button.winfo_rootx(),
            self.sort_button.winfo_rooty() + self.sort_button.winfo_height(),
        ))
        _hover(self.sort_button, CARD, CARD_HOVER)

        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")

    # scrolling list

    def _build_list(self) -> None:
        holder = tk.Frame(self.root, bg=BG)
        holder.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = Scrollbar(holder, self.canvas)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=8)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.body = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        # labels that wrap, with how much of the canvas width they leave to other things
        self.wrapped: list[tuple[tk.Label, int]] = []
        self.priority_var = tk.StringVar(self.root)
        self.priority_menu = _menu(self.root)

        self.canvas.configure(yscrollincrement=1)
        self.body.bind("<Configure>", lambda _e: self._measure())
        self.canvas.bind("<Configure>", self._resized)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._scroll)
        try:
            self.canvas.bind_all("<TouchpadScroll>", self._touchpad)
        except tk.TclError:
            pass  # Tk 8.6 has no such event, and sends trackpads as <MouseWheel>

    def _resized(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window, width=event.width)
        for label, inset in self.wrapped:
            label.configure(wraplength=max(160, event.width - inset))
        self._measure()

    def _measure(self) -> None:
        """Scrollregion is exactly the content, so there is nothing above it to scroll into."""
        self.canvas.configure(scrollregion=(0, 0, 0, self.body.winfo_reqheight()))

    def _scroll(self, event: tk.Event) -> None:
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return  # it all fits; overscroll would just show empty space
        if event.num == 4:
            pixels = -48
        elif event.num == 5:
            pixels = 48
        elif sys.platform == "darwin" and tk.TkVersion < 9:
            pixels = -event.delta * 6  # already in small units, unlike Windows
        elif sys.platform == "darwin":
            pixels = round(-event.delta * 0.4)  # Tk 9 sends 120 a notch, like Windows; keeps smaller steps
        else:
            pixels = -event.delta // 120 * 48
        self.canvas.yview_scroll(pixels, "units")

    def _touchpad(self, event: tk.Event) -> None:
        """Trackpads on Tk 9, in pixels: the x delta is the high 16 bits of `delta`, y the low 16"""
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        low = event.delta & 0xFFFF
        dy = low - 0x10000 if low & 0x8000 else low
        self.canvas.yview_scroll(-dy, "units")

    # status

    def _build_status(self) -> None:
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(8, 12))
        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=self.f_small, anchor="w")
        self.status.pack(side="left")
        self.problem = tk.Label(bar, text="", bg=BG, fg=BAD, font=self.f_small, anchor="e")
        self.problem.pack(side="right")

    # queue

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "busy":
                    self.status.configure(text="Checking…")
                elif kind == "result":
                    self._apply(payload)
        except queue.Empty:
            pass
        self._digest_check()
        self.root.after(150, self._drain)

    def _apply(self, outcome: Outcome) -> None:
        self.labels = outcome.labels or self.labels
        if outcome.updates:
            self.updates = (outcome.updates + self.updates)[:200]
            self._announce(outcome.updates)
        self._render()

        now = datetime.now().strftime("%H:%M")
        if outcome.baseline:
            self.status.configure(text=f"Baseline recorded · {now}")
        else:
            self.status.configure(text=f"{len(self.updates)} new · {now}")
        self.problem.configure(text=outcome.error or "")

    # notifying

    def _announce(self, updates: list[Update]) -> None:
        """High targets notify on their own as found; the rest go out bundled, now or at the digest"""
        rest: list[Update] = []
        high: dict[tuple[str, str], list[Update]] = {}
        for update in updates:
            key = (update.source, update.target)
            if self.prefs.priority(*key) == "high" and not self.bundle_high:
                high.setdefault(key, []).append(update)
            else:
                rest.append(update)

        for key, group in high.items():
            notify(self.labels.get(key, key[1]), _summary(group, self.labels, named=False))
        if self.digest_due is None:
            self._send(rest)
        else:
            self.pending.extend(rest)

    def _send(self, updates: list[Update]) -> None:
        """One notification, high first and low left out"""
        loud = [
            u for u in sort_updates(updates, "priority", self.prefs, self.labels)
            if self.prefs.priority(u.source, u.target) != "low"
        ]
        if loud:
            notify(_headline(loud), _summary(loud, self.labels))

    def _digest_check(self) -> None:
        if self.digest_due is None or datetime.now() < self.digest_due:
            return
        self.digest_due = next_digest(self.digest_due.time(), datetime.now())
        pending, self.pending = self.pending, []
        self._send(pending)

    # priority and sorting

    def _sorted(self) -> None:
        self.prefs.sort = self.sort_var.get()
        self.sort_button.configure(text=_sort_text(self.prefs.sort))
        self._save_prefs()
        self._render()

    def set_priority(self, source: str, target: str, level: str) -> None:
        self.prefs.set_priority(source, target, level)
        self._save_prefs()
        self._render()

    def _priority_menu_for(self, update: Update) -> tk.Menu:
        """The shared right-click menu, filled for `update`'s target"""
        menu = self.priority_menu
        menu.delete(0, "end")
        label = self.labels.get((update.source, update.target), update.target)
        menu.add_command(label=label, state="disabled")
        menu.add_separator()
        self.priority_var.set(self.prefs.priority(update.source, update.target))
        for level in PRIORITIES:
            menu.add_radiobutton(
                label=level.title(), value=level, variable=self.priority_var,
                command=lambda level=level: self.set_priority(update.source, update.target, level),
            )
        return menu

    def _save_prefs(self) -> None:
        try:
            self.prefs.save()
        except (OSError, crypto.EncryptionError) as exc:
            self.problem.configure(text=f"Settings not saved: {exc}")

    # drawing

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.wrapped.clear()

        if not self.updates:
            tk.Label(
                self.body, text="Nothing new", bg=BG, fg=MUTED, font=self.f_body, pady=60
            ).pack(fill="x")
            return

        ordered = sort_updates(self.updates, self.prefs.sort, self.prefs, self.labels)
        for group in group_updates(ordered):
            self._card(group)

    def _card(self, group: list[Update]) -> None:
        """One target: its newest update heads the card, each update below is a line of its own"""
        update = group[0]
        color = SOURCE_COLOR.get(update.source, ACCENT)
        level = self.prefs.priority(update.source, update.target)
        low = level == "low"

        card = tk.Frame(self.body, bg=CARD, cursor="hand2")
        card.pack(fill="x", padx=18, pady=(0, 6))

        stripe = tk.Frame(card, bg=color, width=3)
        stripe.pack(side="left", fill="y")

        inner = tk.Frame(card, bg=CARD)
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=9)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")

        label = self.labels.get((update.source, update.target), update.target)
        tk.Label(
            top, text=label, bg=CARD, fg=MUTED if low else TEXT, font=self.f_strong, anchor="w"
        ).pack(side="left")
        if level == "high":
            tk.Label(
                top, text="high", bg=CARD, fg=HIGH, font=self.f_small, anchor="w"
            ).pack(side="left", padx=(8, 0))
        tk.Label(
            top,
            text=f"{update.source} · {relative_age(update.published)}",
            bg=CARD, fg=DIM if low else MUTED, font=self.f_small, anchor="e",
        ).pack(side="right")

        # (widget, the link a click on it opens)
        links = [(w, update.url) for w in [card, inner, top, *top.winfo_children()]]
        for index, line in enumerate(group[:3]):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(3, 0))
            text = tk.Label(
                row, text=line.version or line.title, bg=CARD, fg=DIM if low else MUTED,
                font=self.f_body, anchor="w", justify="left",
            )
            inset = 74
            if index:  # the first line's age is already in the header
                age = tk.Label(
                    row, text=relative_age(line.published), bg=CARD, fg=DIM if low else MUTED,
                    font=self.f_small, anchor="e",
                )
                age.pack(side="right")
                links.append((age, line.url))
                inset += 72
            text.configure(wraplength=max(160, self.canvas.winfo_width() - inset))
            text.pack(side="left", fill="x", expand=True)
            self.wrapped.append((text, inset))
            links += [(row, line.url), (text, line.url)]
        if len(group) > 3:
            more = tk.Label(
                inner, text=f"and {len(group) - 3} more", bg=CARD, fg=DIM, font=self.f_small, anchor="w"
            )
            more.pack(fill="x", pady=(3, 0))
            links.append((more, update.url))

        for widget, url in links:
            widget.bind("<Button-1>", lambda _e, url=url: webbrowser.open(url))
            for seq in _menu_clicks():
                widget.bind(
                    seq, lambda e, u=update: _popup(self._priority_menu_for(u), e.x_root, e.y_root)
                )
        _hover(card, CARD, CARD_HOVER, [w for w, _ in links])

    def quit(self) -> None:
        self.checker.stop()
        self.root.destroy()


def _hover(widget: tk.Widget, normal: str, active: str, group: list | None = None) -> None:
    targets = group or [widget]

    def paint(color: str):
        def handler(_e: tk.Event) -> None:
            for target in targets:
                target.configure(bg=color)
        return handler

    widget.bind("<Enter>", paint(active), add="+")
    widget.bind("<Leave>", paint(normal), add="+")


def _menu(parent: tk.Misc) -> tk.Menu:
    # macOS draws its own menus and ignores the colours
    return tk.Menu(
        parent, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
        activeforeground=TEXT, selectcolor=TEXT, bd=0,
    )


def _popup(menu: tk.Menu, x: int, y: int) -> None:
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def _menu_clicks() -> tuple[str, ...]:
    """Right click. macOS Tk has called it button 2 or 3 depending on version, and Control-click counts"""
    if sys.platform == "darwin":
        return ("<Button-2>", "<Button-3>", "<Control-Button-1>")
    return ("<Button-3>",)


def _sort_text(key: str) -> str:
    return f"Sort: {SORTS[key]} ▾"


def _headline(updates: list[Update]) -> str:
    return "1 update" if len(updates) == 1 else f"{len(updates)} updates"


def _summary(updates: list[Update], labels: dict[tuple[str, str], str], named: bool = True) -> str:
    """Up to three lines then a count. `named=False` leaves off the label, for one target's own notification"""
    lines = []
    for update in updates[:3]:
        text = update.version or update.title
        if named:
            label = labels.get((update.source, update.target), update.target)
            text = f"{label}: {text}"
        lines.append(text)
    if len(updates) > 3:
        lines.append(f"and {len(updates) - 3} more")
    return "\n".join(lines)


def _ui_font() -> str:
    if sys.platform == "darwin":
        return "SF Pro Text"
    if sys.platform == "win32":
        return "Segoe UI"
    return "DejaVu Sans"


# entry point


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="youpdated-desk", description=__doc__)
    parser.add_argument("-c", "--config", help="config file to use")
    parser.add_argument("--state", help="history database to use")
    parser.add_argument("--every", default="1h", help="check interval (30m, 1h, 6h); default 1h")
    parser.add_argument(
        "--digest", metavar="HH:MM",
        help="hold notifications for one a day at this time; high priority still notifies as found",
    )
    parser.add_argument(
        "--bundle-high", action="store_true",
        help="bundle high priority updates with the rest instead of notifying for each target",
    )
    args = parser.parse_args(argv)

    try:
        interval = parse_since(args.every).total_seconds()
        digest = parse_clock(args.digest) if args.digest else None
    except ValueError as exc:
        parser.error(str(exc))

    root = tk.Tk()
    events: queue.Queue = queue.Queue()
    checker = Checker(args.config, args.state, interval, events)

    config_path = find_config(args.config)
    locked = [
        p for p in (config_path, checker.state_path)
        if p is not None and p.exists() and crypto.is_encrypted_file(p)
    ]
    if locked:
        checker.passphrase = simpledialog.askstring(APP, "Passphrase", show="•", parent=root)
        if not checker.passphrase:
            root.destroy()
            return 1

    prefs = Prefs.load(prefs_path(checker.state_path), checker.passphrase)
    App(root, checker, events, prefs, bundle_high=args.bundle_high, digest=digest)
    checker.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
