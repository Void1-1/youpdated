# Usage

- [The first run](#the-first-run)
- [Command reference](#command-reference)
- [Machine-readable output](#machine-readable-output)
- [Running it on schedule](#running-it-on-schedule)

## The first run

**The first run reports nothing.** It records a baseline of what exists, so that later runs show changes, not every item published:

```text
╭──────────── first run ─────────────╮
│ Baseline recorded for 15 target(s),│
│ 142 existing item(s).              │
│ Future runs report only what's new.│
╰────────────────────────────────────╯
```

To see the current state immediately, add `--all`:

```sh
youpdated check --all
```

Afterward, `youpdated check` prints only what changed since the last run.

Same for a target added later. Its first check records a baseline for that target, and the rest of the run reports as usual:

```text
Baseline recorded for 1 new target(s): npm:react. Their existing items were not reported.
```

## Command reference

```sh
youpdated check                        # what's new since last run (the default command)
youpdated check --all                  # everything currently published, ignoring history
youpdated check --since 7d             # only items from the last week (30m, 12h, 7d, 2w, 1y)
youpdated check -s git -s npm          # limit to some sources
youpdated check --json                 # machine-readable output
youpdated check --rss ~/feeds/you.xml  # aggregated Atom feed for a reader
youpdated check --test -v              # show what it would request, and any config problems
youpdated check --no-save              # report without recording anything as seen
youpdated check --fail-on-error        # exit 2 if any source or notification failed
youpdated check --notify-cmd 'notify-send {title} {url}'  # run a command per new update
youpdated check --no-notify            # skip notifications this run
youpdated check --all --notify         # notify even with --all or --no-save
youpdated check -v                     # show each request, its status, and item summaries
youpdated check --state ./test.db      # use a throwaway history file
youpdated sources                      # list available sources
youpdated add git neovim/neovim        # start watching something (several at once is fine)
youpdated add https://pypi.org/project/rich/ # the source can be left out when the link includes the information
youpdated add --test steam 440         # show the change to the config without writing it
youpdated remove git neovim/neovim     # stop watching it; any form works, like the full URL
youpdated rm --wipe 440                # and delete its history
youpdated import package.json Brewfile # watch everything listed in dependency files
youpdated import --test requirements.txt # show what it would add without writing
youpdated import --format requirements.txt <(pip freeze) # a pipe works too
youpdated edit                         # open the config in the default editor
youpdated init --force                 # rewrite the starter config
youpdated init --encrypt               # starter config written encrypted from the start
youpdated encrypt                      # lock the config and history behind a passphrase
youpdated decrypt                      # convert them back to plain files
youpdated uninstall --test             # list all tool files and the package
```

Running `youpdated` without arguments assumes `youpdated check`.

A check records what it found only once its report is out, so if the report or the `--rss` file can't be written, the same updates are reported again next time. `--since` narrows the report: a new item older than the window is left unrecorded, and a check without `--since` still reports. `--test` sends nothing, and lists the config's problems, such as an entry no source can read, in the `problems` panel, which counts for `--fail-on-error`. One run at a time uses a history file; another waits up to a minute for it, then exits `1`.

`add` and `rm` (or `remove`) edit the config in place and keep comments and layout. `rm` removes entries watching what you name, including options and comments.

The source can be left out when an entry can only mean one: a GitHub, GitLab, or Codeberg repo, `owner/repo`, a feed URL, a Steam app id or store page, an itch.io page, a YouTube `@handle` or channel, a browser name, `cask:name`, a scoped `@scope/name` npm package, or a package page on npmjs.com, pypi.org, or formulae.brew.sh. Package pages are written as the package name. A bare name like `express` could be npm, PyPI, or Homebrew, so name the source for those. Entries without a source can mix sources: `youpdated add https://github.com/neovim/neovim @types/react`.

A removed target's history is forgotten after [`expiry`](configuration.md#how-long-history-is-kept). `rm --wipe` forgets it immediately: seen items, baseline, highest version, names cached for it, and cached addresses it fetched. An address another target still fetches keeps its cache.

If the config is written in a way these can't edit safely, they refuse and say to add or remove by hand.

`edit` opens the config in the [`editor:`](configuration.md#where-is-the-config) if set, then `$VISUAL`, then `$EDITOR`, then your device default text editor, and saves it once closed. Over SSH or without a desktop, the fallback is `nano` or `vi`. An editor that opens a window and returns immediately, like the defaults or `code`, is waited for: save, then press Enter in the cli. It edits a copy, so nothing is saved if the editor exits with an error. Before saving, common issues an editor or a paste can make are fixed and listed: smart quotes around a value (`“@NASA”`), a value starting with `@` or a backtick that needs quotes, and no-break or invisible spaces. The copy is checked with `check --test`: if it doesn't load or has an entry no source can read, it asks to edit again (keeping changes), save anyway, or discard. For an encrypted config, see [Encryption](encryption.md).

`import` reads a `package.json`, `requirements.txt`, or `Brewfile` and adds what it lists. A file is recognized by its name (`requirements-dev.txt`, `requirements/base.txt`, and `~/.Brewfile` count); `--format` reads one named otherwise.

- **package.json**: `dependencies`, `devDependencies`, `optionalDependencies`, and `peerDependencies`, under `npm`. An alias (`npm:lodash@^4`) watches the package it installs.
- **requirements.txt**: each requirement, under `pypi`, following `-r` includes. Versions, extras, and markers are dropped. A file that installs from another index (`--index-url`, `--no-index`) is skipped, since PyPI's package of the same name may be a different one; `--extra-index-url` is shown as a warning.
- **Brewfile**: `brew` lines as formulae and `cask` lines as casks, under `homebrew`.

If a dependency is found that no source can watch, it is listed as skipped: a dependency installed from a path, URL, git repo, editable install, formula from a third-party tap, or `mas` and `vscode` lines.

## Machine-readable output

`--json` writes **only** JSON to stdout; progress and errors go to stderr, so this is safe:

```sh
youpdated check --json | jq '.updates[] | select(.target | startswith("github.com/")) | .title'
```

Exit codes: `0` success, `1` config, encryption, history file, or proxy error, `2` with `--fail-on-error` when a source or notification failed, `130` on interrupt. `uninstall` exits `1` when it leaves a file in place. The JSON has a `warnings` list for config that does nothing, such as an `ignore:` or `versions:` key on a nonexisting source.

## Running it on schedule

Once a day is plenty, most of these sources change slowly, and conditional requests make repeat runs cheap. Use absolute paths, since cron and launchd don't inherit your shell's `PATH`; `which youpdated` prints yours.

To hear about what a scheduled run found without reading its log, set up [notifications](notifications.md).

**cron** (`crontab -e`) run at 9am and append to a log:

```cron
0 9 * * * /home/you/.local/bin/youpdated check >> ~/youpdated.log 2>&1
```

**Keep an RSS feed fresh** for a reader to poll. Note that `--rss` writes that run's items, so a plain `check --rss` will leave an almost empty file. For a feed that always holds a rolling window, ask for it. `--no-save` keeps this from interfering with your daily incremental run:

```cron
0 * * * * /home/you/.local/bin/youpdated check --all --since 30d --no-save --rss ~/feeds/youpdated.xml
```

**macOS launchd**: save as `~/Library/LaunchAgents/com.youpdated.check.plist`, then `launchctl load ~/Library/LaunchAgents/com.youpdated.check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.youpdated.check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.local/bin/youpdated</string>
    <string>check</string>
    <string>--rss</string>
    <string>/Users/you/feeds/youpdated.xml</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer></dict>
  <key>StandardOutPath</key><string>/tmp/youpdated.log</string>
  <key>StandardErrorPath</key><string>/tmp/youpdated.err</string>
</dict></plist>
```

If your config is encrypted, an unattended run needs the passphrase in the environment: see [Encryption at rest](encryption.md#unattended-runs).
