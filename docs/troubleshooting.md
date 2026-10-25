# Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Config error: no config file found` | Run `youpdated init`, or pass `--config PATH`. |
| First run printed nothing | Working as intended: it recorded a baseline. Run `youpdated check --all` to see current items. |
| A newly added target printed nothing | Same as above, for that one target. `youpdated check --all -s <source>` shows its current items. |
| `--all` shows fewer items than expected | Nothing is wrong; sources cap how much history they expose (10–20 items each). |
| A source appears in the yellow `problems` panel | That one source failed; the rest of the run still completed. Re-run with `-v` to see the request and status. |
| `unknown source 'X'` | Check spelling against `youpdated sources`. |
| `can't tell which forge X runs` | A self-hosted `git` host with no hint in its name. Add `forge: github`, `gitlab`, or `forgejo`. |
| `notify` in the `problems` panel | A webhook or command failed. The updates were still recorded, so they won't be sent again. Check the URL, or run the command by hand. |
| YouTube fails every path | Its feed throttles intermittently. Retry, or set `privacy.proxy`, or export `YOUTUBE_API_KEY`. |
| An itch game reports nothing | It has neither a devlog nor a file listing. Confirm with `youpdated check -s itch --all -v`. |
| Everything reports as new again | The history database was deleted or `--state` points somewhere new. |
| `youpdated: command not found` | The install location isn't on your `PATH`. With pipx, run `pipx ensurepath` and open a new terminal; from source, use `.venv/bin/youpdated`. |
| `ModuleNotFoundError: youpdated` | You used `pip install -e .` on macOS. Use `pip install .` instead. |
| `Encryption error: wrong passphrase, or the file has been modified` | The passphrase doesn't match, or the file was edited or truncated. Nothing is written on a failed unlock. |
| `Encryption error: ... no terminal to ask on` | A cron or piped run can't prompt. Set `YOUPDATED_PASSPHRASE`. |
| `encryption needs the cryptography package` | `pipx install 'youpdated[encryption]'` (or `pip install`). |
| `... is not encrypted, but a passphrase was given` | A half-converted setup. Re-run `youpdated encrypt` to finish it. |
| `Proxy error: cannot reach the proxy at ...` | `privacy.proxy` is set but nothing is listening. Start Tor (or your proxy), or remove the line. Nothing was fetched or recorded. |
| Every source fails with `ProtocolError: Malformed reply` | Something is listening on the proxy port but isn't a SOCKS5 proxy. Check the port and scheme. |

To start over, delete the history database (the path is [findable here](configuration.md#where-is-the-config)); your config isn't affected.

## Uninstalling

Youpdated writes two files, but it will find and remove them for you, and the package itself. **Always check first:**

```sh
youpdated uninstall --test
```

That prints what it would delete. When you're ready:

```sh
youpdated uninstall
```

It lists everything again and waits for you to confirm. Add `--yes` to skip the prompt in a script.

```sh
youpdated uninstall --keep-config     # remove everything but your config
youpdated uninstall --keep-config --keep-package  # only wipe history
youpdated uninstall --keep-package    # delete the files, leave Youpdated installed
youpdated uninstall --state ./test.db # also remove a database from --state
```

Two rules: a directory is removed only if it is named `youpdated` and is empty once the tool's own files are gone. Anything else is kept and reported.

The package is removed last, using it's installer: `pipx uninstall youpdated` for pipx, `uv tool uninstall youpdated` for uv, and `python -m pip uninstall youpdated` with the Python it runs on otherwise. If that fails, it says why, prints the command to run yourself, and exits 1.

Windows won't delete a program while it runs, so the uninstaller starts in the background and finishes after `youpdated uninstall` exits. If `youpdated` still runs after that, run the command it printed.

Run from a checkout (`python -m youpdated` or `PYTHONPATH`), there is no package to remove, so only the files are removed. Remove a development install from your virtualenv with `.venv/bin/pip uninstall youpdated`, or delete the virtualenv.
