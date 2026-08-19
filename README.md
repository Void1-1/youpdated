# Youpdated

[![tests](https://github.com/Void1-1/youpdated/actions/workflows/tests.yml/badge.svg)](https://github.com/Void1-1/youpdated/actions/workflows/tests.yml)

A dynamic scraper that collects update information about the games, apps, and packages you follow, and reports back what changed.

It is built to be **privacy-preserving, anonymous, and customizable**: no accounts, no API keys required, nothing stored anywhere non-local.

```text
╭─ steam ──────────────────────────────────────────────────────────╮
│ Team Fortress 2  (440)                                           │
│   • CLTF2 Halloween Cup   7d ago                                 │
│     https://store.steampowered.com/news/app/440/view/6728766547… │
╰──────────────────────────────────────────────────────────────────╯
╭─ github ─────────────────────────────────────────────────────────╮
│ python/cpython                                                   │
│   • v3.14.7   5d ago                                             │
│     https://github.com/python/cpython/releases/tag/v3.14.7       │
╰──────────────────────────────────────────────────────────────────╯
```

## Sources

| Source | What it watches | Endpoint |
| --- | --- | --- |
| `github` | Releases, tags, commits | `.atom` feeds (no auth needed) |
| `npm` | Newly published versions | the public registry |
| `steam` | Patch notes and news | the store's news feed |
| `itch` | Devlog posts and new builds | `<game>/devlog.rss` + the game page |
| `youtube` | New videos on a channel or playlist | the channel feed, with fallbacks |
| `browser` | Chrome, Brave, Firefox, Edge releases | each vendor's public version history |
| `feed` | Any RSS/Atom URL: the escape hatch for everything else | the feed itself |

---

## 1. Install

Requires **Python 3.11 or newer** (`python3 --version` to check). CI runs the full suite on
**Linux, macOS, and Windows** across Python 3.11–3.14.

```sh
cd /path/to/Youpdated
python3 -m venv .venv
.venv/bin/pip install .
```

On **Windows**, use `python` and the `Scripts` directory:

```powershell
python -m venv .venv
.venv\Scripts\pip install .
```

That installs a `youpdated` command inside the virtualenv, at `.venv/bin/youpdated` on
macOS/Linux and `.venv\Scripts\youpdated.exe` on Windows. Either call it by that full path, or put
it on your `PATH`:

```sh
export PATH="$PWD/.venv/bin:$PATH"           # macOS / Linux
youpdated --version                          # -> youpdated 0.2.0
```

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"   # Windows
youpdated --version
```

To make that permanent, add the `export` line to your `~/.zshrc`, or the `$env:PATH` line to your
PowerShell profile. Examples assume `youpdated` is on your `PATH`; if it isn't, substitute the full
path above.

## 2. Create your config

```sh
youpdated init
```

This writes a commented starter config and **prints the path it used**, which is:

| OS | Config file | History database |
| --- | --- | --- |
| macOS | `~/Library/Application Support/youpdated/config.yaml` | `~/Library/Application Support/youpdated/state.sqlite3` |
| Linux | `~/.config/youpdated/config.yaml` | `~/.local/share/youpdated/state.sqlite3` |
| Windows | `%LOCALAPPDATA%\youpdated\config.yaml` | `%LOCALAPPDATA%\youpdated\state.sqlite3` |

Three locations are checked, (in this order) the first one found chosen:

1. `--config PATH`, if you pass it
2. `./youpdated.yaml` or `./youpdated.yml` in the current directory
3. the per-user path in the table above

Use `youpdated init -c ./youpdated.yaml` if you'd rather keep the config in a project folder.
Add `--force` to overwrite an existing file.

## 3. Edit it to watch what want

Open the file printed. Every source entry takes **a bare value** or **a mapping**. A minimal config:

```yaml
sources:
  github:
    - python/cpython
  npm:
    - express
```

### Where to find identifiers

| You want to watch | Put this in the config | How to find it |
| --- | --- | --- |
| A GitHub repo | `python/cpython` | The `owner/repo` part of the repo URL. A full `https://github.com/owner/repo` URL also works. |
| An npm package | `express`, `"@types/node"` | The package name on npmjs.com. **Quote scoped names**: YAML treats a leading `@` specially. |
| A Steam game | `440` | The number in the store URL: `store.steampowered.com/app/`**`440`**`/Team_Fortress_2/`. The full store URL also works, and the game's name is looked up for you. |
| An itch.io game | `https://user.itch.io/game-name` | The game's page URL, exactly as it appears in the address bar. |
| A YouTube channel | `"@NASA"` | The `@handle` from the channel URL. A `UC…` channel id or full channel URL also works. **Quote it**: YAML treats a leading `@` specially. |
| A YouTube playlist | `playlist: PLxxxx` | The `list=` parameter in the playlist URL. A full playlist URL also works. |
| A browser | `chrome`, `brave`, `firefox`, `edge` | Just the name. Add `platform:` and `channel:` to narrow it. See the [full example](#full-example). |
| Anything else | `https://example.com/feed.xml` | Any RSS or Atom feed URL. Many apps publish one at `/feed.xml`, `/releases.atom`, or `/blog/rss`. |

### Full example

```yaml
privacy:
  # proxy: socks5://127.0.0.1:9050   # Tor's default SOCKS port
  user_agent: rotate                 # 'rotate', or a fixed UA string
  jitter: [0.5, 3.0]                 # random gap between hits on one host
  concurrency: 4
  timeout: 20

sources:
  github:
    - python/cpython                 # easy
    - repo: astral-sh/uv             # advanced:
      watch: [releases, commits]     # releases | tags | commits
      branch: main                   # for `commits`

  npm:
    - express
    - "@types/node"
    - package: react
      tag: next                      # follow a dist-tag other than latest

  steam:
    - 440                            # appid; the store name is resolved for you
    - appid: 730
      name: Counter-Strike 2

  itch:
    - https://hempuli.itch.io/baba-is-you    # devlogs and builds (default)
    - url: https://aak581.itch.io/engineering-marvels-from-hell
      watch: [devlog, releases]              # choose one if you prefer

  youtube:
    - "@NASA"                        # handle, channel id, or channel URL
    - playlist: PLxxxxxxxxxxxxxxxx

  browser:
    - chrome
    - brave
    - firefox
    - browser: edge
      platform: windows              # mac | windows | linux | android | ios
      channel: beta                  # stable | beta | dev | canary (+ esr/nightly for firefox)

  feed:                              # anything with an RSS/Atom feed
    - https://blog.rust-lang.org/feed.xml
    - url: https://github.com/obsidianmd/obsidian-releases/releases.atom
      name: Obsidian
      limit: 5
```

Check your config without sending a request:

```sh
youpdated check --test -v
```

That validates the file and prints every URL it would fetch. Config mistakes exit `1` with a message naming the offense.

## 4. Run

```sh
youpdated check
```

**The first run reports nothing** It records a baseline of what exists, so that later runs show changes rather than every item published:

```text
╭──────────── first run ─────────────╮
│ Baseline recorded for 15 target(s),│
│ 142 existing item(s).              │
│ Future runs report only what's new.│
╰────────────────────────────────────╯
```

To see the current state right away, add `--all`:

```sh
youpdated check --all
```

From then on, `youpdated check` prints only what changed since the previous run.

---

## Command reference

```sh
youpdated check                       # what's new since last run (the default command)
youpdated check --all                 # everything currently published, ignoring history
youpdated check --since 7d            # only items from the last week (30m, 12h, 7d, 2w)
youpdated check -s github -s npm      # limit to some sources
youpdated check --json                # machine-readable output
youpdated check --rss ~/feeds/you.xml # aggregated Atom feed for a reader
youpdated check --test -v             # test to show what it would request
youpdated check --no-save             # report without recording anything as seen
youpdated check --fail-on-error       # exit 2 if any source failed
youpdated check -v                    # show each request, its status, and item summaries
youpdated check --state ./test.db     # use a throwaway history file
youpdated sources                     # list available sources
youpdated init --force                # rewrite the starter config
youpdated init --encrypt              # starter config written encrypted from the start
youpdated encrypt                     # lock the config and history behind a passphrase
youpdated decrypt                     # convert them back to plain files
youpdated uninstall --test            # list every file the tool wrote
```

Running `youpdated` with no arguments runs `youpdated check`.

`--json` writes **only** JSON to stdout; progress and errors go to stderr, so this is safe:

```sh
youpdated check --json | jq '.updates[] | select(.source == "github") | .title'
```

Exit codes: `0` success, `1` config, encryption, or proxy error, `2` with `--fail-on-error` when a source failed, `130` on interrupt.

## Running it on schedule

Once a day is plenty, most of these sources change slowly, and conditional requests make repeat runs cheap. Use absolute paths, since cron and launchd don't inherit your shell's `PATH`.

**cron** (`crontab -e`) run at 9am and append to a log:

```cron
0 9 * * * /path/to/Youpdated/.venv/bin/youpdated check >> ~/youpdated.log 2>&1
```

**Keep an RSS feed fresh** for a reader to poll. Note that `--rss` writes that run's items, so a plain `check --rss` leaves almost an empty file. For a feed that always holds a rolling window, ask for it. `--no-save` keeps this from interfering with your daily incremental run:

```cron
0 * * * * /path/to/Youpdated/.venv/bin/youpdated check --all --since 30d --no-save --rss ~/feeds/youpdated.xml
```

**macOS launchd**: save as `~/Library/LaunchAgents/com.youpdated.check.plist`, then `launchctl load ~/Library/LaunchAgents/com.youpdated.check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.youpdated.check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/Youpdated/.venv/bin/youpdated</string>
    <string>check</string>
    <string>--rss</string>
    <string>/Users/you/feeds/youpdated.xml</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer></dict>
  <key>StandardOutPath</key><string>/tmp/youpdated.log</string>
  <key>StandardErrorPath</key><string>/tmp/youpdated.err</string>
</dict></plist>
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Config error: no config file found` | Run `youpdated init`, or pass `--config PATH`. |
| First run printed nothing | Working as intended — it recorded a baseline. Run `youpdated check --all` to see current items. |
| `--all` shows fewer items than expected | Nothing is wrong; sources cap how much history they expose (10–20 items each). |
| A source appears in the yellow `problems` panel | That one source failed; the rest of the run still completed. Re-run with `-v` to see the request and status. |
| `unknown source 'X'` | Check spelling against `youpdated sources`. |
| YouTube fails every path | Its feed throttles intermittently. Retry, or set `privacy.proxy`, or export `YOUTUBE_API_KEY`. |
| An itch game reports nothing | It has neither a devlog nor a file listing. Confirm with `youpdated check -s itch --all -v`. |
| Everything reports as new again | The history database was deleted or `--state` points somewhere new. |
| `youpdated: command not found` | The virtualenv isn't on your `PATH`. Use `.venv/bin/youpdated`. |
| `ModuleNotFoundError: youpdated` | You used `pip install -e .` on MacOS |
| `Encryption error: wrong passphrase, or the file has been modified` | The passphrase doesn't match, or the file was edited or truncated. Nothing is written on a failed unlock. |
| `Encryption error: ... no terminal to ask on` | A cron or piped run can't prompt. Set `YOUPDATED_PASSPHRASE`. |
| `encryption needs the cryptography package` | `pip install 'youpdated[encryption]'`. |
| `... is not encrypted, but a passphrase was given` | A half-converted setup. Re-run `youpdated encrypt` to finish it. |
| `Proxy error: cannot reach the proxy at ...` | `privacy.proxy` is set but nothing is listening. Start Tor (or your proxy), or remove the line. Nothing was fetched or recorded. |
| Every source fails with `ProtocolError: Malformed reply` | Something is listening on the proxy port but isn't a SOCKS5 proxy. Check the port and scheme. |

To start over from a clean slate, delete the history database (the path is in the table in [step 2](#2-create-your-config)); your config is untouched.

## Uninstalling

Youpdated writes two files, but it will find and remove them for you. **Always check first:**

```sh
youpdated uninstall --test
```

That prints every path it would delete. When you're ready:

```sh
youpdated uninstall
```

It lists the files again and waits for you to confirm. Add `--yes` to skip the prompt in a script. It refuses rather than assuming consent.

```sh
youpdated uninstall --keep-config     # wipe history, keep your config
youpdated uninstall --state ./test.db # also remove a database from --state
```

Two rules: a directory is removed only if it is named `youpdated` and is empty once the tool's own files are gone. Anything else is kept and reported.

It prints the command to remove the package itself. (which it can't do while running):

```sh
/path/to/.venv/bin/python -m pip uninstall youpdated
```

If you installed into a dedicated virtualenv, deleting that directory removes the package too.

## Encryption at rest

By default the config and the history database are plain files. Anyone with your login, or your unencrypted backups, can read the list. To lock them behind a passphrase:

```sh
pip install 'youpdated[encryption]'   # pulls in `cryptography`; not needed otherwise
youpdated encrypt                     # `set-encrypted` also works
```

Starting fresh, skip the plaintext step entirely. `init --encrypt` writes the starter config already encrypted, so it never exists in the clear at all:

```sh
youpdated init --encrypt
```

You can't edit in place: run `youpdated decrypt`, edit, then `youpdated encrypt` again. That does put the config on disk in plaintext while you work on it, so `init --encrypt` is rarely worth anything, or you edit before encrypting the first time.

It asks for a passphrase twice and rewrites both files in place. From then on every command asks for it once, decrypts **into memory**, and writes back encrypted when the run ends.

```sh
youpdated check                       # Passphrase: ...
youpdated decrypt                     # back to plain files
```

For cron and other unattended runs there is no terminal to ask on, so pass the passphrase in the environment instead:

```cron
0 9 * * * YOUPDATED_PASSPHRASE='...' /path/to/.venv/bin/youpdated check >> ~/youpdated.log 2>&1
```

That trades the passphrase's secrecy for the crontab's. Ensure it is more protected than your config was. Reading it from a file your shell sources, or from a keychain helper, is stronger.

**What this does and does not protect.** Files are encrypted whole with AES-256-GCM under a key derived from the passphrase with scrypt (n=2¹⁶, r=8), and the tag is checked on every read, so a modified file is refused rather than trusted. It does **not** cover anything while Youpdated is running: the passphrase and the decrypted config are in the process's memory, and `YOUPDATED_PASSPHRASE` is visible to other processes of the same user. Nor does it erase the plaintext that was already on the disk. `encrypt` overwrites the file, but the old blocks may survive in free space until they are reused.

**There is no recovery.** Don't forget your passphrase, or you'll have to recreate configs.

| | Encrypted | Plain |
| --- | --- | --- |
| `youpdated check` | asks for the passphrase | runs |
| `cat ~/.config/youpdated/config.yaml` | binary | readable |
| `sqlite3 state.sqlite3 'select * from seen'` | `file is not a database` | readable |
| File permissions | `0600` on macOS/Linux, see below on Windows | as created |

**On Windows the file mode does nothing.** There are no POSIX permission bits, so the `0600` the
writer asks for is ignored apart from the read-only flag, and access is decided by the ACL the file
inherits from its directory. In the default location under `%LOCALAPPDATA%` that already excludes
other standard users. The encryption itself is unaffected and works
identically on all three platforms.

### Using it as a module

[youpdated/crypto.py](https://github.com/Void1-1/youpdated/blob/main/youpdated/crypto.py) stands alone.

```python
from youpdated import crypto

blob = crypto.encrypt(b"anything", "passphrase")
crypto.write_private("secret.bin", blob)          # atomic, 0600
assert crypto.decrypt(blob, "passphrase") == b"anything"
```

Everything in `crypto.__all__` is supported, the container format included: `encrypt`, `decrypt`, `is_encrypted`, `is_encrypted_file`, `write_private`, `prompt_passphrase`, `available`, `require_backend`, `EncryptionError`, `MAGIC`, `VERSION`, `PASSPHRASE_ENV`. A `decrypt` on a file this version can't read raises rather than guessing, and `VERSION` is bumped if the layout ever changes.

The same passphrase threads through the two core entry points, so you can drive an encrypted setup without the CLI:

```python
from youpdated.config import load_config
from youpdated.state import State

config = load_config("youpdated.yaml", passphrase="...")
with State("state.sqlite3", passphrase="...") as state:
    print(state.seen_count())
```

```sh
$ python -c "from youpdated.cli import main; main(['sources'])" && python -c "
import sys, youpdated.cli
print('cryptography loaded:', any(m.startswith('cryptography') for m in sys.modules))"
cryptography loaded: False
```

Don't use encryption with untrusted sources or non-official code without verifying security yourself!

## Privacy

- **No credentials.** Every source works unauthenticated. `GITHUB_TOKEN` and `YOUTUBE_API_KEY` are read from the environment if set (only for raising rate limits) and are never written to config.
- **Local only.** Config and history live in your platform's config/data directories. Nothing is sent anywhere except the sources you list. They can be [encrypted at rest](#encryption-at-rest).
- **One HTTP path.** Every request goes through [youpdated/http.py](https://github.com/Void1-1/youpdated/blob/main/youpdated/http.py). `privacy.proxy` covers all traffic. To check:

  ```sh
  youpdated check --all --no-save    # with privacy.proxy: socks5://127.0.0.1:9
  ```

- **The proxy fails closed.** There is no direct fallback: if `privacy.proxy` is set and the proxy
  is unreachable, requests error rather than going around it. Youpdated also checks the proxy is
  up *before* the run starts and refuses with exit `1` if it isn't, so a run with Tor down can't
  quietly report "nothing new" and record an empty baseline. `--test` reports the proxy as
  unreachable instead of aborting, since it sends nothing either way.
- **Hostnames are resolved by the proxy, not by you.** SOCKS5 CONNECT sends the domain name to the
  proxy (`ATYP 3`), so your local resolver never sees which sites you watch. A proxied request with a locally-resolved hostname would leak the whole list to your DNS server.

- **Cookies are discarded** on every request, so nothing accumulates across a run.
- **Requests are paced** per host with random jitter instead of arriving in a burst.
- **Conditional GETs** (ETag / Last-Modified) mean unchanged feeds are re-fetched cheaply, which is both faster and less fingerprintable. `--all` turns them off, since a 304 has no items.

## Development

Install the test dependencies and run the suite from the repo root:

```sh
.venv/bin/pip install ".[dev]"
PYTHONPATH=$PWD .venv/bin/python -m pytest
```

`PYTHONPATH=$PWD` is what makes your working-tree changes take effect without reinstalling. Run the CLI the same way while developing:

```sh
PYTHONPATH=$PWD .venv/bin/python -m youpdated check --test -v
```

Tests run entirely offline: each parser is exercised against real captured payloads in [tests/fixtures/](https://github.com/Void1-1/youpdated/tree/main/tests/fixtures/) via `respx`, which fails the test rather than allowing a real request. After changing source code, reinstall with `.venv/bin/pip install .` to update the `youpdated` command itself.

### Contributing

`main` is protected by a branch ruleset, changes arrive by pull request:

```sh
git switch -c my-change
# ...work, commit...
git push -u origin my-change
gh pr create --fill
```

The ruleset requires a pull request (no approvals needed), all 13 test jobs green, and the branch up to date with `main`; force-pushes and branch deletion are blocked. See [ADDING_SOURCES.md](https://github.com/Void1-1/youpdated/blob/main/ADDING_SOURCES.md) if your change is a new source.

### Building a release

```sh
.venv/bin/pip install ".[publish]"
rm -rf dist build *.egg-info
.venv/bin/python -m build          # -> dist/*.whl and dist/*.tar.gz
.venv/bin/twine check dist/*       # metadata and README render check
```

Verify the artifact before uploading, by installing the wheel into a throwaway virtualenv rather than trusting the source tree:

```sh
python3 -m venv /tmp/verify
/tmp/verify/bin/pip install dist/youpdated-*.whl
/tmp/verify/bin/youpdated sources
```

Then publish. Uploading needs a PyPI account and an API token. Create one under
[Account settings --> API tokens](https://pypi.org/manage/account/token/), scoped to this project
once it exists (for a first upload the token has to be account-wide):

```sh
.venv/bin/twine upload dist/*
# username: __token__
# password: pypi-...   (the token, including the pypi- prefix)
```

Test against [TestPyPI](https://test.pypi.org) first if you want to see the rendered project page before it's permanent:

```sh
.venv/bin/twine upload --repository testpypi dist/*
```

Then tag it and cut a GitHub release:

```sh
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 dist/* --notes-from-tag
```

**A version is permanent.** PyPI refuses to replace an existing version even after a delete, so bump `version` in `pyproject.toml` before rebuilding. Add the matching `CHANGELOG.md` entry in the same commit.

### Adding a source

**See [ADDING_SOURCES.md](https://github.com/Void1-1/youpdated/blob/main/ADDING_SOURCES.md)** for the full guide.

The short version: implement `targets()` and `fetch()` from the protocol in
[youpdated/sources/base.py](https://github.com/Void1-1/youpdated/blob/main/youpdated/sources/base.py), decorate the class with `@register`, and import it in [youpdated/sources/\_\_init\_\_.py](https://github.com/Void1-1/youpdated/blob/main/youpdated/sources/__init__.py). Reuse `entry_fields()` so your source accepts both the bare-value and mapping config shapes, and `parse_feed()` from [youpdated/sources/feed.py](https://github.com/Void1-1/youpdated/blob/main/youpdated/sources/feed.py) if the upstream is RSS/Atom. Third-party packages can register a source through a `youpdated.sources` entry point instead, without modifying this repo.
