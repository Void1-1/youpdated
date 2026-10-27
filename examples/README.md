# Examples

Standalone programs built on youpdated. Each runs against an existing config and history.

## desktop

```sh
python examples/desktop/youpdated_desk.py
python examples/desktop/youpdated_desk.py --every 30m
python examples/desktop/youpdated_desk.py --every 10m --digest 18:00
```

Options: `-c/--config`, `--state`, `--every` (`30m`, `1h`, `6h`; default `1h`), `--digest` (`18:00`), `--bundle-high`.

Each target gets one card, with a line per update. Right-click a card to set the target's priority: high, normal, or low. **Sort** orders the list by priority, newest, source, or name; newest first for ties. High targets get a notification of their own as soon as they update, and low ones never notify. With `--digest 18:00` everything else is held for one notification at that time each day, and `--bundle-high` holds high priority for it too.
Priorities and the sort are kept beside the history as `state.desk.json`, encrypted when the history is.

Needs Tk, which ships with python.org and most system Pythons. Homebrew Python needs `brew install python-tk`; Debian and Ubuntu need `apt install python3-tk`.
