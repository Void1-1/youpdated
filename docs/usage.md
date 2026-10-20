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
youpdated check                       # what's new since last run (the default command)
youpdated check --all                 # everything currently published, ignoring history
youpdated check --since 7d            # only items from the last week (30m, 12h, 7d, 2w, 1y)
youpdated check -s git -s npm         # limit to some sources
youpdated check --json                # machine-readable output
youpdated check --rss ~/feeds/you.xml # aggregated Atom feed for a reader
youpdated check --test -v             # test to show what it would request
youpdated check --no-save             # report without recording anything as seen
youpdated check --fail-on-error       # exit 2 if any source or notification failed
youpdated check --notify-cmd 'notify-send {title} {url}'  # run a command per new update
youpdated check --no-notify           # skip notifications this run
youpdated check --all --notify        # notify even with --all or --no-save
youpdated check -v                    # show each request, its status, and item summaries
youpdated check --state ./test.db     # use a throwaway history file
youpdated sources                     # list available sources
youpdated init --force                # rewrite the starter config
youpdated init --encrypt              # starter config written encrypted from the start
youpdated encrypt                     # lock the config and history behind a passphrase
youpdated decrypt                     # convert them back to plain files
youpdated uninstall --test            # list every file the tool wrote
```

Running `youpdated` without arguments assumes `youpdated check`.

## Machine-readable output

`--json` writes **only** JSON to stdout; progress and errors go to stderr, so this is safe:

```sh
youpdated check --json | jq '.updates[] | select(.target | startswith("github.com/")) | .title'
```

Exit codes: `0` success, `1` config, encryption, or proxy error, `2` with `--fail-on-error` when a source or notification failed, `130` on interrupt.

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
