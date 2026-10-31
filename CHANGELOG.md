# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`youpdated import`** (#37). Adds everything in a `package.json`, `requirements.txt`, or `Brewfile`, with `youpdated add`, `--test` shows the change without writing it. `package.json` dependencies of every kind go under `npm`, requirements go under `pypi` with `-r` includes followed, and `brew`/`cask` lines go under `homebrew`. A dependency from a path, URL, git repo, editable install, third-party tap, and Brewfile lines like `mas` are skipped with reasons shown. Files are recognized by name, or read as named with `--format`. Requirements from another index (`--index-url`, `--no-index`) are skipped, as PyPI's package of that name may be a different one, and `--extra-index-url` is shown as a warning. Credentials in a dependency's URL are never printed.
- **Version filters** (#37). A `versions:` rule reports only some of a target's versions. `bump: major` reports a version only when its first number is higher than every release before it: 4.0.0, but not 4.0.1 or a 3.9.x backport. `minor` and `patch` do the same for the second and third numbers, and `min: "3.13"` drops versions below 3.13. Rules can be set for every source, for one, or on one entry, like `ignore:`, and an entry's rules override the others one rule at a time; `bump: any` turns a bump back off. Only the first prerelease toward a bump is reported, then the final, so a run of nightlies or canaries counts once. Versions are compared by their numbers, so any dotted or underscored scheme works (`v3.15.0rc1`, `131.0.6778.69`, `curl-8_11_1`, `REL_17_2`), and updates without a version, like commits, aren't filtered. A version left out still counts as seen, so loosening a rule doesn't report the past. A target with `bump` keeps its highest versions in its history, so a source listing only the current version (`homebrew`) has something to compare against; `rm --wipe` and `expiry:` clear. The summary says how many versions were left out, and `--json` counts them as `filtered`. A `versions:` key no source is listed under is warned about, like an `ignore:` one.
- **`youpdated edit`** (#59). Opens the config in an editor, then saves it once the editor closes. An editor that opens a window and returns at once, like the device defaults or `code`, is waited on: save there, then press Enter. `editor:` in the config picks one over `$VISUAL` and `$EDITOR`: a command, a program's path, or (on macOS) an app's name. Smart quotes around a value, an unquoted value starting with `@`, and no-break or invisible spaces are fixed on save, and listed. An encrypted config is decrypted to a copy only you can read, under `/dev/shm` where available, and written back encrypted with the same passphrase. A config that sets `privacy.proxy` and encrypts asks before writing the copy. The edit is checked before saving. Nothing is saved when the editor exits with an error.
- **`track_latest:`** (#37). With `track_latest: true`, `git` and `steam` entries also ask for the latest release, in case busier items pushed it off the list: where GitHub's `/releases/latest` points (the API's latest release with a token), GitLab's latest permalink, Forgejo's latest release, and the newest Steam post the developer marked as an update. It's added only when not already listed. Off by default, as it's one request more per entry.
- **Sorting and priority in the desktop example app** (#12). Right-click an update to mark its target high, normal, or low priority; high ones are tagged and low ones dimmed. The list sorts by priority, newest, source, or name. High targets get their own notification as soon as they update, and low ones never notify. `--digest 18:00` holds the rest for one notification a day, and `--bundle-high` holds high priority too. Settings live in the example only, beside the history as `state.desk.json` and encrypted when the history is; the youpdated config is unchanged. (example app available from the GitHub)

### Changed

- **`youpdated add` lists entries already watched on one line per source** (`Already watching express, react under npm`), instead of one line each.
- **`youpdated uninstall` also removes the package.** Once the files are gone it runs the uninstaller used for the copy: `pipx uninstall` for pipx, `uv tool uninstall` for uv tools, `uv pip uninstall` for a uv-made environment, and `python -m pip uninstall` with the interpreter it runs on otherwise. The package is listed in `--test` and confirmation prompt. If the uninstaller fails, or exits but leaves the package, the command to run yourself is printed and exits 1. On Windows, which won't delete the running `youpdated.exe`, the uninstaller runs in the background once the command exits. `--keep-package` deletes files only. `--keep-config` now removes the package too; add `--keep-package` to only wipe history. A copy run from a checkout (`python -m youpdated`, `PYTHONPATH`) is not a package, and nothing other than files are removed.
- **One run at a time per history file.** A run holds a lock (`state.sqlite3.lock`, beside the database) and another waits up to a minute, then exits 1 saying the file is in use. An encrypted history is read whole and written back whole, so a `youpdated rm --wipe` during a scheduled check was undone, two overlapping checks could lose seen marks, and a file could be left undecryptable. `encrypt` and `decrypt` take the lock too. `uninstall` removes the lock file as well.
- **`--since` leaves what it hides unrecorded.** A new item older than the window was recorded as seen, so only `--all` could ever show it. It is now reported by the next check without `--since`.
- **`check --test` shows the config's problems** in the `problems` panel and exits 2 for them with `--fail-on-error`, and `--test --json` prints a report. It used to exit 0 and print nothing about them. A source that stops early for want of an answer is not counted as a problem.
- **`git` refuses `owner/repo/extra`** for GitHub and Forgejo instead of reading it as `owner/repo`: `add hashicorp/tap/terraform` landed under `git` as `hashicorp/tap`, and `rm python/cpython/typo` removed `python/cpython`. A pasted address (`https://github.com/o/r/releases/tag/v1`, `codeberg.org/o/r/releases`) is still trimmed to the repo.
- **A webhook is only sent again when it can't have arrived**: after a failed connection, `429`, `503`. Timeout or `500`, `502`, `504` could come after delivery, and aren't retried.
- **Notify commands no longer inherit** `YOUPDATED_PASSPHRASE`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, `YOUTUBE_API_KEY`.
- **A warning for an `ignore:` key with no coverage**, such as `ignore: {github: …}` left behind after moving entries to `git:`. It shows after the report, and in a new `warnings` list in `--json`.
- **`expiry:` and `--since` max out at 100y.** `never` still works.
- **The release workflow's publish job now pins actions to exact commits**, since it holds `id-token: write` and a tag can be moved.
- **The desktop example shows one card per target.** Several updates from the same target share its card, newest first, one line each up to three and then a count; each line opens its own link.

### Fixed

- **More prereleases are recognized without a GitHub token.** The anonymous atom feeds have no prerelease flag, so the tag name is read, and now catches `-next.2` (Angular), milestones `-M2` and `.CR1` (Spring, Quarkus), `beta4` right after the version (Nextcloud), `-ea.3` (Traefik), `llvmorg-24-init`, mesa's `-branchpoint` tags, Swift's dated snapshots, and an rc with build metadata (`-rc.0+dedupelabels`). A tag without a number, like neovim's `nightly`, keeps it as its version, so it is a prerelease as the API path flags it, and a commit hash ending in `7b0` is no longer one. Checked against GitHub's own flags on 11,259 releases of 87 projects: missed prereleases went from 441 to 288, none of which have anything in the tag to go by.
- **npm and pypi could miss a release behind prereleases.** A check read the 10 newest versions, so a package publishing canaries or nightlies faster than it was checked could push a release out before it was seen: in three years of `next`'s history, weekly checks would never have seen 42 of its final releases, or 7 of `react`'s. The 10 newest final releases and the `latest` version are now listed too, from the same request.
- **Security: the YouTube Data API key could be printed.** It was part of the request URL, which errors, `--json`, `-v`, and `--test` show. It is now sent in the `X-Goog-Api-Key` header, which is dropped on a redirect to another host.
- **Security: escape codes in a title, body, or link reached the terminal.** A feed could clear the screen or plant links with `&#27;`. Control characters and lone surrogates are now removed from everything a site sends, which also keeps `--rss` valid XML when a title holds one, and stops a lone surrogate from crashing `--rss` and `--json`.
- **Security: the desktop example could run code from a title on Windows.** PowerShell ends a quoted string at curly quotes too. The text now reaches PowerShell through the environment.
- **Updates could be lost when the report failed.** Items were recorded as seen before the report, `--rss`, and pruning ran, so a failure in any of them, such as a label with `[/…]` in the problems panel, a numeric Invidious title, or an unwritable `--rss` path, used them up unseen. The report and feed are now first. An unwritable `--rss` path is an error.
- **An invalid entry no longer drops later entries of its source.** It is reported alone.
- **A web page at a feed's address was an empty feed**, with no error, and on a first fetch its empty baseline reported the whole feed as new once the feed returned. It is now an error, for feeds and sources that reads one.
- **Brave recorded an empty baseline when the GitHub API was rate-limited**, since the atom feed's 10 newest releases are nearly all nightlies, and then reported old stable releases as new. The target now fails until either path has a release for its channel, and the stable channel falls back to the release GitHub marks as latest.
- **YouTube's fallbacks**: a malformed Invidious reply (`{"videos": [null]}`, an out-of-range date) skipped the other instances and the Data API; it now moves on. Invidious addresses from the public list are used only if they are HTTPS on a public hostname; a test had fetched `http://127.0.0.1:9`. The list is refreshed on use (once a day) instead of never; each fetch tries a random four kept instances, not just the first four, and `rm --wipe` clears.
- **Cookies could pass between requests** run at the same time, and to the next hop of a redirect. Now none are kept.
- **A webhook redirected with `301`, `302`, `303`.** It was resent as a request without a body, and counted as delivered. It is now a failure; `307` and `308` are still followed.
- **A repo listed under both `github:` and `git:` with different ignore rules** was fetched fully on every run.
- **`youpdated encrypt` on a symlinked config** replaced the link and left the plaintext at its target. It now writes through the link, same as `add`/`rm`. A write also doesn't leave a fixed-name `.tmp`, which could make the result readable by others.
- **A GitLab group with a dot in its name** (`my.group/sub/project` with `host: gitlab.com`) was read as a hostname.
- **A plugin whose constructor raises** crashed every command, and kept later plugins from loading. It is now skipped.
- **Cleaner errors instead of tracebacks** for a config that isn't UTF-8, a `--state` file that isn't a database, and the desktop example given a `-c` config that doesn't exist.
- **`uninstall` exited 0 when it left files in place.** It now exits 1.
- **An ntfy webhook lost its query**, so `?auth=` didn't work; `headers:` was the only way.
- **`split_command` accepted** a YAML boolean (`yes`, `off`) or an empty string in a list command.
- **Timestamps with a fraction and a zone offset**, such as `.123+05:30`, lost the offset in the `browser` source. One parser now serves every source.
- **Trackpad scrolling in the desktop example on macOS.** Tk 9 sends trackpads as `<TouchpadScroll>`, which the window ignored, and reports a mouse wheel notch as 120 rather than 1, which scrolled 720 pixels a notch. Tk 8.6 behaves as before.
- **Link highlighting** has been fixed to correctly bold links without extra coloration or breakage at spaces in links.
- **Terminal resizing** no longer adds extra lines or breaks output formatting in cli.

## [0.4.0] — 2026-10-24

### Added

- **`youpdated add` and `youpdated rm` commands** (#37). `youpdated add git python/cpython` edits the config for you, and `rm` (`remove`) removes entries. The file is edited as text, so comments, blank lines, and quoting should be kept; the result is parsed again and nothing is written unless only the named entries changed. Entries are checked by their source first, an entry already watched in another form (a full URL for `owner/repo`) is skipped, and `rm` matches any form. Both work on an encrypted config without writing plaintext to disk, keep a symlinked config's link and a plain one's permissions, and `--test` to show the change without writing it.
- **The source type can be omitted for `add` and `rm`** if the entry can only mean one: `youpdated add https://github.com/neovim/neovim` goes under `git`. Package pages on npmjs.com, pypi.org, and formulae.brew.sh are written as the package name. A value no source or more than one source claims is an error that says to name one. Sources take part with an optional `claim()`; see [ADDING_SOURCES.md](ADDING_SOURCES.md#recognizing-your-entries).
- **`youpdated rm --wipe`** also deletes the history of what it removes: seen items, baseline, ignore-rules, names cache, cached HTTP validators of the addresses fetched, then rebuilds the database. An address another target still fetches keeps its validator
- **Each run records which target asked for which address** (a new `http_cache_targets` table), so `--wipe` can find a target's cached addresses. Ones cached before this are found by asking the source which addresses it fetches, without sending anything. Links go with the target's baseline when it expires.
- **`pypi` source** (#32). Watches a Python package's releases through PyPI's JSON Simple API, the index pip reads. Names are normalized, so `typing_extensions` and `Typing-Extensions` are considered the same. Releases whose every file is yanked are skipped; alphas, betas, release candidates, and dev releases are `prerelease`, and the highest final release is tagged `latest`. A project marked archived, deprecated, or quarantined is reported once, tagged `status` and the status.
- **New dependency: `packaging`** (`>=22`), the PyPA's library pip itself uses, to order and classify versions the way pip does.
- **`homebrew` source** (#20). Watches a formula or cask through the formulae.brew.sh JSON API. A bare name is a formula; use `cask:firefox` or `cask: firefox` for a cask. Formula versions include the revision (`1.25.0_2`), since a revision bump is a new build of the same version. The API only exposes the current version with no date, so each check reports at most one item.
- **A config for the tool is found from any directory** (#28). Run from a checkout, `youpdated.yaml` or `youpdated.yml` at its root is now used when the current directory has none, ahead of the per-user config. `uninstall` lists it with the other project configs.
- **`git` source for GitHub, GitLab, and Forgejo/Gitea** (#33, #34, #35). One entry for any forge: a repo URL, `host/owner/repo`, or `owner/repo` for GitHub. Watches releases, tags, and commits. The forge is known for github.com, gitlab.com, codeberg.org, and gitea.com, guessed from a self-hosted hostname, or set with `forge:`. GitLab subgroups and GitHub Enterprise function. Tokens are optional and read from the environment (`GITHUB_TOKEN`, `GITLAB_TOKEN`, or per-entry `token_env:`).
- **Notifications** (#36). A `notify:` block pushes each run's new updates to webhooks (ntfy, Discord, Slack, or plain JSON, with the format guessed from URL) and/or a command run once per update. A command is never run through a shell: update fields are passed as `{placeholder}` arguments, `YOUPDATED_*` environment variables, and JSON on stdin. `--notify-cmd` adds a command from the command line, and `--no-notify`/`--notify` force sending off or on. Baselines, `--all`, and `--no-save`, by default, send nothing. Failures show in the problems panel and count for `--fail-on-error`, with webhook URLs reduced to their host.
- **A weekly canary against live services.** The test suite runs on recorded fixtures, so it can't check if a source changes anything important. The `canary` workflow runs every source against a real target (`.github/canary/canary.yaml`), fails a source that errors or parses nothing, and keeps a `canary` issue open until a later run passes.

### Changed

- **Breaking: the `github` source is now part of `git`** (#35). GitHub updates are reported with source `git`, and their target includes the host: `github.com/python/cpython` instead of `python/cpython`. This shows in the terminal, `--json`, `--rss`, and notifications. Scripts that match `.source == "github"` or a bare `owner/repo` target will match nothing, with no error. Match `.target | startswith("github.com/")` instead.

  Configs should continue to work: `github:` is another name for `git`, `-s github` selects `git`, and `ignore: {github: [...]}` still covers the entries listed under `github:`. History is moved to the new names the first time it is opened, so nothing is reported again. A repo listed under both `github:` and `git:` reports each update once. (Reach out if any errors occur to the GitHub)

### Fixed

- **GitHub releases no longer re-report when `GITHUB_TOKEN` is set or unset** (#24). The atom feed keyed releases by entry id and the REST API by release id, so every switch reported each release again. Both now key them by tag. History recorded under the old ids still counts as seen.
- **Tokens and webhook headers no longer follow redirects to another host.** httpx strips `Authorization` when a redirect leaves the origin, but nothing else, so a GitLab server answering with a redirect elsewhere was able to carry `PRIVATE-TOKEN`, same for a webhook's custom auth header. Redirects are now followed by Youpdated, and every header a source or webhook supplies is dropped on the first hop to a different origin (keeping only `User-Agent`, `Accept`, and `Accept-Language`). Same-host redirects and http to https upgrades keep headers.
- **An encrypted file can't ask for an unbounded key derivation.** The scrypt cost is read from the header before the file can be authenticated, so a crafted file could request hundreds of GiB of memory. Costs above 16× the default (about 1 GiB) are refused before scrypt runs. Every file Youpdated has written uses the default and is unaffected. This was only potentially an issue if a malicious actor was able to create a youpdated config on a machine, at which point, security is mostly lost anyways, but is still now fixed.

## [0.3.1] — 2026-10-10

### Fixed

- **itch builds no longer re-report on unchanged pages.** itch.io shuffles HTML attribute order, and the upload list was only read in one order, so some fetches saw no files and fingerprinted a "new build". Successive runs each reported a different handful of games until every variant had been seen. Upload and timestamp parsing no longer depend on attribute order.
- **YouTube Shorts no longer re-report when the feed falls back.** The official feed links Shorts as `/shorts/<id>`, which was not recognised, so Shorts were recorded as `yt:video:yt:video:<id>` there and `yt:video:<id>` through Invidious or Data API. Each switch reported Shorts again. Existing history is migrated on first open, so nothing already seen comes back.
- **`--no-save` really leaves the state alone.** It still stored each feed's ETag/Last-Modified, so the next real run was answered 304 and silently skipped whatever the preview had shown. (Whoops)
- **A target that fails partway no longer hides its new items.** If one of a target's fetches failed (e.g. GitHub `watch: [releases, commits]` with commits down), the others' validators were still stored while their items were discarded, so the next run got a 304 and never reported them. Validators are now stored only alongside the items they cover.
- **Feeds listed oldest-first report new entries.** Entries were cut to the limit in document order before sorting, so on an oldest-first feed longer than the limit the newest posts never made it in. Affects `feed`, Steam, itch devlogs, and GitHub atom feeds.
- **Chrome no longer errors** when a version is first listed without a rollout start time and later with.
- **Removing an `ignore:` rule shows what it hid.** Ignored items are not recorded, but the feed's validators were, so hidden items stayed hidden until it next changed. Each target's rules are now recorded, and a target whose rules changed is fetched fully once. Every target is fetched in full once after upgrading.

## [0.3.0] — 2026-10-07

### Added

- **`ignore:` rules drop update types** (#11). Every update already carried tags describing what it is; those are now filterable. A bare list applies everywhere, a mapping narrows it to one source (`'*'` for all), and a per-entry `ignore:` adds to whichever applies:

  ```yaml
  ignore:
    '*': [prerelease]
    github: [commit]

  sources:
    github:
      - repo: astral-sh/uv
        ignore: [tag]        # global rules + `tag`
  ```

  An update is dropped if it carries any listed tag. Ignored items are not recorded as seen, so removing a rule later shows what it was hiding. The run summary and `--json` both count what was dropped. See [Ignoring update types](docs/configuration.md#ignoring-update-types) for the tag vocabulary per source.

- **GitHub prereleases are tagged without a token.** `prerelease` previously came only from the REST API, which needs `GITHUB_TOKEN`, so on the anonymous `.atom` path nothing was tagged. It is now inferred from the tag name (`v3.15.0rc2`, `1.2.0-beta.1`, `0.12.0-alpha`), and a token takes precedence. Platform and build suffixes (`v1.0.0-linux`, `v4.2.0+build.7`) are untouched.

- **npm prereleases are tagged.** Any semver version with a prerelease suffix now carries `prerelease`. Build metadata (`+build-7`) does not count, even with a hyphen in it.

- **Runs report progress instead of going quiet.** A check printed nothing until the last target landed, so a slow source was indistinguishable from a hang. Interactive runs now show a live line naming the targets still in flight, with a count and elapsed time. It is suppressed under `-v`, `--test`, and when output is not a terminal.

- **A desktop example app** (#13), in `examples/desktop/`. A simple window checks on a schedule, sends a notification, and lists what changed. Uses standard library and youpdated. It reads the same config and history as the CLI, and prompts for a passphrase when either is encrypted. Its own CI covers Linux, macOS, and Windows, and runs only when `examples/` changes

### Changed

- **Install docs now with `pipx install youpdated`.** The package has been on PyPI since 0.1.0, but the README still told people to clone the repo and install from the working tree. Installing from source is now a subsection. The scheduling examples point at a `pipx` path rather than a repo virtualenv.
- **itch builds are tagged `build`, and page-only changes `page`,** instead of both being `release`. The tags say what changed, so `ignore:` can tell them apart. Anything filtering `--json` or `--rss` output on `release` for itch should use `build`.

### Fixed

- **A host's pacing lock is no longer held across its own wait.** `_pace` slept while holding the per-host lock, so every other thread bound for that host blocked in `acquire` for the whole gap, with no way to be interrupted, and a thread waiting its turn could be scheduled ahead of one ready to do real work. Each caller now reserves a slot under the lock and waits outside it. The spacing between requests to one host is unchanged.
- **Progress bar moved around** (#14). The target names led the line, so the bar and counter shifted sideways every time they changed, and a long name squeezed the bar out. The spinner, bar, count, elapsed time and failure count now come first at a fixed width, and the names fill what is left of the line, truncated with an ellipsis. Names are shown literally, so one containing brackets (`[/x]`) no longer crashes the bar.
- **Rate-limit retries honor `Retry-After`** (#17). A 429 or 503 was retried on a fixed rate ignoring any potential 'Retry-After' sent. The wait now follows the header (seconds or an HTTP-date) and holds the whole host, so other targets on that site wait too. A server asking for more than 60s fails that request at once instead of stalling the run. Without the header, the fixed backoff applies as before.
- **The history database is pruned** (#18). Seen items were added, so the database grew, including for targets removed from the config, and with encryption, the whole thing is decrypted and re-encrypted each run. An item missing from every fetch for longer than the new top-level `expiry:` (default `1y`, `never` to keep everything) is now forgotten, and the file is compacted. Items still listed by their source are never pruned, and stored validators older than half the expiry are skipped once to force a full fetch. Targets that failed or were left out with `--source` keep their history. A removed target's baseline expires with its history, so re-adding it later records a new baseline instead of reporting everything it has. `--json` counts what was pruned, and `--since` now also accepts years (`1y`).
- **A target added later no longer dumps a backlog.** The first run baseline applied to the whole state, so after it, a newly added target reported every item it had as new. Same for a target that failed the first run, or `-s`. Baselines are now recorded per target on its first successful fetch, and the run names those targets. State from earlier versions is read as already baselined, so nothing is swallowed on upgrade. A target's first fetch sends no stored validators, so a new channel on a document another target already watches (Brave, Firefox, Edge) records a real baseline instead of a 304.
- **`--test` lines read `[test] GET`**, not `[test]] GET`.

## [0.2.1] — 2026-08-25

### Fixed

- **The release workflow's `attach` job failed on v0.2.0 and has been removed.** It copied the built artifacts onto the GitHub Release, but this repo has immutable releases enabled, which freezes assets when the release is published — and the job runs after that, so it could only ever fail (`HTTP 422: Cannot upload assets to an immutable release`). Publishing to PyPI was unaffected; 0.2.0 shipped correctly. Releases from now on will not carry attached `.whl`/`.tar.gz` files. Get them from PyPI, where they are covered by a signed PEP 740 attestation binding each digest to this repository and workflow — a stronger guarantee than the copy the job was making.

- **Targets sharing one upstream document lost their updates** Every Brave channel is served by one GitHub releases document, and every Firefox channel by one Mozilla JSON. Each channel fetched it separately, so the first stored an ETag and the rest were answered `304 Not Modified` against that ETag moments later and reported *nothing*. With the default `jitter`, per-host pacing serializes those requests, which is the case that loses: a config watching five Firefox channels and three Brave channels reported 2 of 8. Under concurrency it was non-deterministic: is a channel reported depended on thread timing, so a loaded machine changed the result.

  A run now reuses each document across the targets that share it (`Client.run_scope()`), so the document is fetched once and every target sees it. The same config went from 8 requests reporting 2 of 8 targets, to 2 requests reporting 8 of 8. (And 147 ms to 43 ms of wall time) Outside a run the client is unchanged: one GET per call, and a 304 still means "nothing new".

- **The YouTube official-feed path never set the channel label.** `_label_and_parse` did not do the labelling it was supposed to, so a channel that answered on the primary path was reported by its raw `@handle` while the Invidious and Data API fallbacks named it properly. The label now comes from the feed on all three paths.

- **`feed` sources parsed every feed twice** A `feed:` entry given as a bare URL had no label, so `FeedSource.fetch` parsed the document once to read the feed's own `<title>` and then handed the same bytes to `parse_feed`, which parsed them again. Feed parsing is the most expensive part of a run, so this ~doubled the CPU cost of every bare-URL feed. One parse now feeds both the label and the entries.

  `parse_feed()` is unchanged for callers that only need entries; it is now a wrapper over the new `parse_document()` / `parse_entries()` split in `youpdated.sources.feed`.

## [0.2.0] — 2026-08-19

### Added

- **Encryption at rest for the config and history** ([#5](https://github.com/Void1-1/youpdated/issues/5)). `youpdated encrypt` (alias `set-encrypted`) converts an existing setup in place; `youpdated decrypt` converts it back. Every command detects an encrypted setup on its own and asks for the passphrase once, or reads it from `YOUPDATED_PASSPHRASE` for unattended runs.

  Files are encrypted whole with AES-256-GCM under a scrypt-derived key (n=2¹⁶, r=8, p=1), with the KDF parameters authenticated as additional data so they cannot be downgraded. Decryption happens **in memory**: the config is parsed from a decrypted buffer and the SQLite database is deserialized into an in-memory database and written back encrypted when the run ends, so no plaintext copy is put on disk even mid-run. A read-only run leaves the file byte-for-byte alone.

  Needs the `cryptography` package: `pip install 'youpdated[encryption]'`. Installs without it behave exactly as before.

- **`youpdated init --encrypt`** writes the starter config already encrypted, so a setup that is meant to be private never has a plaintext config on disk at all — unlike `init` then `encrypt`, which leaves the original blocks in free space.

- **A proxy preflight.** When `privacy.proxy` is set, the proxy is checked before the run starts and the run is refused with exit `1` if it is unreachable. `--test` reports it instead of aborting.

- **`youpdated.crypto` is a documented standalone module.** `from youpdated import crypto` gives you the container directly; everything in its `__all__` is a supported surface. It stays an optional *dependency* rather than a separate distribution on purpose: nothing in it imports `cryptography` at module scope, so a plain install already pays nothing for it, and shipping the container format apart from the code that reads it would risk version skew on files that are the user's only copy.

### Changed

- **A dead proxy now stops the run instead of failing every target.** Requests already failed closed (httpx routes everything through the proxy and never falls back to a direct connection) but with Tor off, a run would fail each target separately, record an empty baseline, and still exit `0`. In a cron log that is indistinguishable from "nothing new". It now exits `1` before the state database is even opened, so nothing is recorded.

### Fixed

- **A broken SOCKS handshake escaped the retry path.** `socksio` raises `ProtocolError`, which is not an `httpx.HTTPError`, so a proxy port answering with something that is not SOCKS5 bypassed the retries and surfaced as a raw exception rather than a `FetchError`. The client now treats `SOCKSError` as a network error like any other.

- **`tests/test_cleanup.py` could delete a real `./youpdated.yaml`.** The fixture redirected the config and data directories but not the working directory, so `find_traces()` picked up the project config of whoever ran the suite from a directory that had one, and `remove_traces()` deleted it. The fixture now chdirs to the temp directory.

## [0.1.1] — 2026-08-18

### Fixed

- **`browser` / Brave: a server error killed the target instead of falling back.** The Brave source reads the GitHub REST API and keeps the `.atom` feed as a fallback, but only a 403 or 429 reached it. A 5xx: a timeout, or a DNS failure, was retried, then raised, and the whole target was reported as failed. Observed against `api.github.com` returning 504. Any failure the HTTP client gives up on now falls back to the atom feed; a genuine outage of *both* still reports an error.

### Changed

- Retry backoff is configurable on the HTTP client (`retry_backoff`), so the test suite no longer spends real seconds exercising retry paths. The suite went from ~4.9s to ~0.4s.

### Added

- A `release` workflow that publishes to PyPI via trusted publishing when a GitHub Release is published, gated on the full 13-job test matrix and on the tag matching the version in `pyproject.toml`.

## [0.1.0] — 2026-08-18

First release.

### Added

- **Seven sources**, all working without accounts or API keys:
  - `github` — releases, tags, and commits via `.atom` feeds
  - `npm` — newly published versions from public registry
  - `steam` — patch notes and news; resolves the store name from a bare appid
  - `itch` — devlog posts and new builds, fingerprinted from the game page
  - `youtube` — channels and playlists, with Invidious and Data API fallback
  - `browser` — Chrome, Brave, Firefox, and Edge releases across platforms and channels
  - `feed` — any RSS/Atom URL, for apps without a dedicated source
- **Plugin architecture**: sources register in-tree with `@register` or ship from a third-party package through a `youpdated.sources` entry point.
- **Config**: every source entry takes a bare value for the common case or a mapping for advanced options
- **Incremental reporting**: a SQLite history so each run reports what changed. First run records a baseline.
- **Three output formats**: terminal report, `--json`, and `--rss`
- **Privacy controls**: optional SOCKS/HTTP proxy covering every request, user-agent rotation, per-host request pacing with jitter, per-request cookie clearing, and conditional GETs. `--test` prints URLs without sending
- **`youpdated uninstall`** to remove every file from the tool. Refuses directories it didn't create or have other files.

### Known issues

- YouTube's RSS endpoint throttles occassionaly and 404s valid URLs; fallback covers, but a run can still fail all three. Retry or set `privacy.proxy`.
- Some itch games publish no "Updated" timestamp, so build updates are reported undated.
- Firefox publishes current versions, so it reports one item per channel.
- Edge exposes release notes only for the stable and beta channels. (But like, it's Edge, why do you want to know when it updates?)

[0.4.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.4.0
[0.3.1]: https://github.com/Void1-1/youpdated/releases/tag/v0.3.1
[0.3.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.3.0
[0.2.1]: https://github.com/Void1-1/youpdated/releases/tag/v0.2.1
[0.2.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.2.0
[0.1.1]: https://github.com/Void1-1/youpdated/releases/tag/v0.1.1
[0.1.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.1.0
