#!/usr/bin/env python3
"""A small desktop ui for youpdated"""

# should be cross platform compatable, open a github issue if i forgot something

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime
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
THUMB = "#33333d"
THUMB_HOVER = "#45454f"

SOURCE_COLOR = {
    "github": "#a9b1d6",
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
    def __init__(self, root: tk.Tk, checker: Checker, events: queue.Queue):
        self.root = root
        self.checker = checker
        self.queue = events
        self.updates: list[Update] = []
        self.labels: dict[tuple[str, str], str] = {}

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
        self.wrapped: list[tk.Label] = []

        self.canvas.configure(yscrollincrement=1)
        self.body.bind("<Configure>", lambda _e: self._measure())
        self.canvas.bind("<Configure>", self._resized)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._scroll)

    def _resized(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window, width=event.width)
        for label in self.wrapped:
            label.configure(wraplength=max(160, event.width - 74))
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
        elif sys.platform == "darwin":
            pixels = -event.delta * 6  # already in small units, unlike Windows
        else:
            pixels = -event.delta // 120 * 48
        self.canvas.yview_scroll(pixels, "units")

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
        self.root.after(150, self._drain)

    def _apply(self, outcome: Outcome) -> None:
        self.labels = outcome.labels or self.labels
        if outcome.updates:
            self.updates = (outcome.updates + self.updates)[:200]
            notify(_headline(outcome.updates), _summary(outcome.updates, self.labels))
        self._render()

        now = datetime.now().strftime("%H:%M")
        if outcome.baseline:
            self.status.configure(text=f"Baseline recorded · {now}")
        else:
            self.status.configure(text=f"{len(self.updates)} new · {now}")
        self.problem.configure(text=outcome.error or "")

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

        for update in self.updates:
            self._card(update)

    def _card(self, update: Update) -> None:
        color = SOURCE_COLOR.get(update.source, ACCENT)

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
            top, text=label, bg=CARD, fg=TEXT, font=self.f_strong, anchor="w"
        ).pack(side="left")
        tk.Label(
            top,
            text=f"{update.source} · {relative_age(update.published)}",
            bg=CARD, fg=MUTED, font=self.f_small, anchor="e",
        ).pack(side="right")

        body = tk.Label(
            inner, text=update.version or update.title, bg=CARD, fg=MUTED,
            font=self.f_body, anchor="w", justify="left",
            wraplength=max(160, self.canvas.winfo_width() - 74),
        )
        body.pack(fill="x", pady=(3, 0))
        self.wrapped.append(body)

        widgets = [card, inner, top, body] + list(top.winfo_children())
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, url=update.url: webbrowser.open(url))
        _hover(card, CARD, CARD_HOVER, widgets)

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


def _headline(updates: list[Update]) -> str:
    return "1 update" if len(updates) == 1 else f"{len(updates)} updates"


def _summary(updates: list[Update], labels: dict[tuple[str, str], str]) -> str:
    lines = []
    for update in updates[:3]:
        label = labels.get((update.source, update.target), update.target)
        lines.append(f"{label} — {update.version or update.title}")
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
    args = parser.parse_args(argv)

    try:
        interval = parse_since(args.every).total_seconds()
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

    App(root, checker, events)
    checker.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
