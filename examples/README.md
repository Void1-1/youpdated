# Examples

Standalone programs built on youpdated. Each runs against an existing config and history.

## desktop

```sh
python examples/desktop/youpdated_desk.py
python examples/desktop/youpdated_desk.py --every 30m
```

Options: `-c/--config`, `--state`, `--every` (`30m`, `1h`, `6h`; default `1h`).

Needs Tk, which ships with python.org and most system Pythons. Homebrew Python needs
`brew install python-tk`; Debian and Ubuntu need `apt install python3-tk`.
