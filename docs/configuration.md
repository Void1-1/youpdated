# Configuration

Everything Youpdated watches, and how, is in a YAML file. `youpdated init` writes a commented starter. For what to put under each source, see [Where to find identifiers](../README.md#where-to-find-identifiers).

- [Where the config is](#where-is-the-config)
- [Identifier details](#identifier-details)
- [Full example](#full-example)
- [Git forges](#git-forges)
- [Web pages](#web-pages)
- [Ignoring update types](#ignoring-update-types)
- [Filtering by version](#filtering-by-version)
- [Tracking the latest release](#tracking-the-latest-release)
- [How long history is kept](#how-long-history-is-kept)
- [Checking your config](#checking-your-config)

## Where is the config

`youpdated init` **prints the path it used**, which is:

| OS | Config file | History database |
| --- | --- | --- |
| macOS | `~/Library/Application Support/youpdated/config.yaml` | `~/Library/Application Support/youpdated/state.sqlite3` |
| Linux | `~/.config/youpdated/config.yaml` | `~/.local/share/youpdated/state.sqlite3` |
| Windows | `%LOCALAPPDATA%\youpdated\config.yaml` | `%LOCALAPPDATA%\youpdated\state.sqlite3` |

Four locations are checked when running, in the following order, and the first one found is used:

1. `--config PATH`, if you pass it
2. `./youpdated.yaml` or `./youpdated.yml` in the current directory
3. `youpdated.yaml` or `youpdated.yml` beside the tool: the root of a checkout you run from source, so its config works from any directory
4. the per-user path in the table above

Use `youpdated init -c ./youpdated.yaml` if you'd rather keep the config in a project folder. Add `--force` to overwrite an existing file.

`youpdated edit` opens whichever one is used in your editor, so you don't need its path. See [`edit`](usage.md). To choose the editor, set `editor:` to a command (`code`, `vim -n`), a program's path, or (on macOS) an app's name or path (`TextEdit`, `/Applications/Zed.app`). It's used over `$VISUAL` and `$EDITOR`.

## Identifier details

More details and options beyond the [basics](../README.md#where-to-find-identifiers):

- **git:** `host/owner/repo` as well as a URL, and GitLab subgroups (`group/sub/project`) work. A dotted first segment reads as a host, so give a dotted group a `host:` (`repo: my.group/sub/project` with `host: gitlab.com`). A pasted page within a repo (`/releases/tag/v1`) is trimmed to the repo, but `owner/repo/extra` is refused instead of read as `owner/repo`. Self-hosted servers are covered in [Git forges](#git-forges).
- **homebrew:** only `homebrew/core` and `homebrew/cask` are covered, not third-party taps.
- **pypi:** case, `-`, `_`, and `.` don't matter. Ex: `typing_extensions` and `Typing-Extensions` are the same package.
- **steam:** the game's name is looked up for you; set `name:` to override it.
- **youtube:** a `UC…` channel id or a full channel URL also works.
- **browser:** narrow it with `platform:` and `channel:`, as in the [full example](#full-example).
- **webpage:** a URL. Set `name:` if the page's title is long. How the version is found, and what to do when it isn't, is in [Web pages](#web-pages).

## Full example

Every source entry takes **a bare value** or **a mapping**. The bare value is the simplest form; mappings can add more options.

```yaml
privacy:
  # proxy: socks5://127.0.0.1:9050   # Tor's default SOCKS port
  user_agent: rotate                 # 'rotate', or a fixed UA string
  jitter: [0.5, 3.0]                 # random gap between hits on one host
  concurrency: 4
  timeout: 20

ignore:                              # update types you never want reported
  '*': [prerelease]                  # '*' is every source
  git: [commit]

versions:                            # which versions are reported
  npm: {bump: major}                 # only new major versions, like 19.0.0

expiry: 1y                           # forget items missing from every fetch this long
track_latest: true                   # also ask forges and Steam for the latest release
editor: code                         # what `youpdated edit` opens

sources:
  git:                               # GitHub, GitLab, Codeberg, Forgejo/Gitea
    - python/cpython                 # easy: owner/repo means GitHub
    - repo: astral-sh/uv             # advanced:
      watch: [releases, commits]     # releases | tags | commits
      branch: main                   # for `commits`
      ignore: [tag]                  # adds to the rules above, this entry only
    - https://gitlab.com/gitlab-org/cli
    - codeberg.org/forgejo/forgejo
    - repo: https://gitlab.gnome.org/GNOME/gtk
      watch: [releases, tags]        # releases | tags | commits
    - repo: tools/deploy             # a self-hosted instance
      host: git.example.org
      forge: forgejo                 # github | gitlab | forgejo (gitea is forgejo)
      token_env: EXAMPLE_GIT_TOKEN   # name of an env var holding a token

  npm:
    - express
    - "@types/node"
    - package: react
      tag: next                      # follow a dist-tag other than latest

  homebrew:
    - wget                           # a formula
    - cask:firefox                   # a cask
    - cask: docker                   # mapping form because a formula and a cask can share a name
      name: Docker Desktop

  pypi:
    - httpx
    - youpdated
    - package: django
      name: Django
      ignore: [prerelease]           # skip alphas, betas, rcs, and dev releases
      versions: {bump: minor}        # 5.2.0 and 6.0.0, but not 5.2.1

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

  webpage:                           # anything else: the version a page shows
    - https://www.videolan.org/vlc/
    - url: https://www.7-zip.org/
      name: 7-Zip
      regex: 'Download 7-Zip ([\d.]+)' # define the version information location

notify:                              # optional: push new updates somewhere
  webhooks:
    - https://ntfy.sh/my-private-topic
  command: notify-send "{label}" "{title}"
```

The `privacy:` block is explained in [Privacy](privacy.md), and `notify:` in
[Notifications](notifications.md).

## Git forges

The `git` source watches a repository on GitHub, GitLab, or Forgejo/Gitea (Codeberg runs Forgejo) using a repo URL, `host/owner/repo`, or `owner/repo` for GitHub.
Every forge accepts `watch:` (`releases`, `tags`, `commits`) and `branch:` for commits.

The forge is known for `github.com`, `gitlab.com`, `codeberg.org` and `gitea.com`, and a guess is attempted for a self-hosted host with `gitlab`, `gitea`, `forgejo` or `github` in its name (`gitlab.gnome.org`). Anything else needs `forge:`. GitHub Enterprise hosts should use `forge: github`.

No forge needs credentials for *public* repos. A token only raises rate limits, and is read from an environment variable, not the config. `GITHUB_TOKEN` can be used for `github.com` and `GITLAB_TOKEN` for `gitlab.com`. A host's token is only ever sent to that host, named per entry with `token_env:`

Updates are reported under the `git` source with the host in the name:
`github.com/python/cpython`, not `python/cpython`.

## Web pages

The `webpage` source watches a page that has no feed or registry and reports its version when it changes. Each check looks for the version in these places, most reliable first:

1. **Structured data:** JSON-LD or microdata `softwareVersion`, which many product pages publish for search engines.
2. **Download links** with a version in the file name, like `App-2.4.1.dmg` or `app_2.4.1_amd64.deb`. All of them must agree.
3. **Text naming the latest version:** "Latest version: 2.4.1", "Current version is 2.4.1", "Stable 2.4.1".
4. **Text labelling a version:** "Version 2.4.1", "Release 2.4.1".

The first place with a version decides, and it has to name only one. **A check will try to fail over reporting the wrong version**, so it fails when:

- That place names several versions, like a changelog's "Version 2.4.1 … Version 2.4.0", or links to both the current release and the previous one.
- Two places name different versions, like structured data that's out of date, or "Version 2.4.1" beside a single `App-1.9.8.dmg` for older systems. A series agrees with its release: "GIMP 3.2" beside `gimp-3.2.6-setup.exe` is fine.
- There's no version anywhere. A page whose content is built by JavaScript has nothing to read, as Youpdated doesn't run it. If the page links a feed, the error states, as a feed is the better thing to watch.

The page is *not* searched for its highest version-like number. Versions something needs, replaced, or is built on are left out ("Requires macOS 14.0 or later", "Python 3.11+", "upgrading from version 1.9", "Tested on macOS version 14.6", "Apache License, Version 2.0"), and so are series like "2.4.x", dates, and copyright years.

**Where the version was found is remembered.** If a later check finds it somewhere else, it fails rather than reporting, so that a redesigned page can't report incorrect versions. Set `regex:` to pin a version location, or start over with `youpdated rm --wipe webpage URL` and re-`add`.

**When it can't tell, give it `regex:`.** It's matched against the page's HTML, as your browser's View Source shows it, and the first match is the version: its `version` named group, otherwise its first group, otherwise the whole match. Regex replaces the search entirely, so other versions on the page don't matter.

```yaml
sources:
  webpage:
    - url: https://www.wireshark.org/download.html
      regex: 'Stable Release: ([\d.]+)'
    - url: https://example.com/download
      regex: '(?i)example-(?P<version>[\d.]+)-mac\.dmg'   # (?i) ignores case
```

Each check gives one update: the version, linking to the page. A version with a prerelease marker (`2.5.0-beta.1`, `3.13.0rc1`) is tagged `prerelease`. To see what a page reads as without waiting for it to change: `youpdated check --all -v -s webpage`, which also shows where each version was found.

## Ignoring update types

Every update has one or more **tags** describing what it is. An `ignore:` rule drops updates carrying a tag you list.

```yaml
ignore: [prerelease]          # shorthand: applies to every source
```

```yaml
ignore:
  '*': [prerelease]           # every source
  git: [commit, tag]          # just this source
```

Either form can be combined with a per-entry rule, which **adds** to it:

```yaml
sources:
  git:
    - python/cpython                    # global rules only
    - repo: astral-sh/uv
      ignore: [tag]                     # global rules + `tag`
```

These are the tags each default source sets:

| Source | Tags |
| --- | --- |
| `git` | `release`, `prerelease`, `tag`, `commit` |
| `npm` | `release`, `prerelease`, `latest` |
| `homebrew` | `release` |
| `pypi` | `release`, `prerelease`, `latest`; `status` plus `archived`, `deprecated`, or `quarantined` |
| `itch` | `devlog`, `build`, `page` |
| `browser` | `release`, and channel (`stable`, `beta`, `dev`, `canary`, `esr`, `nightly`) |
| `youtube` | `video` |
| `steam` | `news` |
| `feed` | `item` |
| `webpage` | `release`, `prerelease` |

An update is dropped if it carries **any** tag listed. A GitHub release candidate is tagged both `release` and `prerelease`, so ignoring `prerelease` drops it while ordinary releases stay.

An itch build is tagged `build`, not `release`. It is a fingerprint of the game page, not a published release. A page with a date move and no new files is tagged `page`. So `ignore: [build]` drops new builds while keeping devlog posts, and ignoring `release` everywhere leaves itch alone. (`watch: [devlog]` on an itch entry still turns builds off outright)

Without a `GITHUB_TOKEN`, `prerelease` is inferred from the tag name (`v3.15.0rc2`, `1.2.0-beta.1`, `v22.3.0-next.2`, `v4.2.0-M2`, `3.40.0.CR1`, `llvmorg-24-init`), because the anonymous `.atom` feeds don't have prerelease flags. With a token, GitHub's flag is used. GitLab has no prerelease flag, so it is always inferred; Forgejo's flag is always used. A project whose prereleases have plain version numbers, such as PCSX2's nightlies, only marks them with GitHub's flag, so they need a token to be told apart.

## Filtering by version

A `versions:` rule reports only some of a target's versions:

```yaml
sources:
  npm:
    - package: react
      versions:
        bump: major                     # 19.0.0, not 18.3.1
  pypi:
    - package: django
      versions:
        min: "5.0"                      # 5.0 and later, nothing from 4.2
```

- **`bump: major`** reports a version only when its first number is higher than every release before it: 4.0.0 after 3.9.2, but not 4.0.1. `minor` also reports 3.10.0, and `patch` also reports 3.9.3. A backport, such as 3.12.8 released after 3.13.0, is never a bump, and neither is a build number like Chrome's 131.0.6778.85 after 131.0.6778.69. `bump: any` reports every version, which is the default.
- **`min`** reports only versions at or above it. `min: 4` keeps 4.0.0 and its release candidates, and drops 3.9.2. Put it in quotes when it has a dot, as YAML reads `3.10` as the number 3.1.

Versions are compared by their numbers: `v3.15.0rc1` counts as 3.15, `1.25.0_2` as 1.25, and `curl-8_11_1` as 8.11.1, so any dotted or underscored numbering works, not only semver. A version with the same numbers as one before it, such as `2026.1.post1` or a Homebrew revision, isn't a second bump. Updates without a version, such as commits, devlog posts, and videos, are never filtered; a version without a number to read, like `stable` or a dated snapshot tag, always is.

A prerelease is an update its source tags `prerelease` (see [the tags each source sets](#ignoring-update-types)). Only the first one toward a bump is reported, as a heads-up, and then the final: `bump: major` shows 4.0.0rc1 and 4.0.0, but not 4.0.0rc2 or the nightlies in between. Add `ignore: [prerelease]` to see only the final. A browser's beta or nightly channel isn't tagged, so its betas are that channel's releases.

Like `ignore:`, rules can be set for every source or for one, and an entry's own rules override them one rule at a time:

```yaml
versions: {bump: major}               # shorthand: every source
```

```yaml
versions:
  '*': {bump: minor}                  # every source
  npm: {bump: major}                  # just this source

sources:
  npm:
    - express                         # bump: major
    - package: react
      versions: {min: "18"}           # bump: major, and 18 or later
    - package: vue
      versions: {bump: any}           # every version
```

Unlike an ignored update, a version a rule leaves out still counts as seen, so loosening the rule later doesn't report the versions it skipped. The summary line says how many were left out, and `--json` counts them as `filtered`.

A target with `bump` keeps its highest versions in its history. A new version is compared against releases the source no longer lists, and a source that lists only the current version, like `homebrew`, has something to compare the next one against. `check --all` compares only against what the source lists, since the history is past every version listed. So it can show the oldest listed version of a line as a bump, like 3.15.1 once 3.15.0 has dropped off the list. A target's first check with `bump` is compared the same way.

## Tracking the latest release

Each check reads only the newest items a source lists, ex: 10 releases for a GitHub repo (20 with a token, and on GitLab and Forgejo), and what a Steam game's news feed lists. A project that publishes nightlies, canaries, or announcements faster than you check can push a release off that list before it's seen.

`npm` and `pypi` also list the 10 newest final releases and the `latest` version, from the same request, so they always do this. So does Brave's stable channel when the GitHub API is rate-limited and the feed it falls back to is all nightlies. For `git` and `steam` it takes a request of its own per entry, so it's opt-in:

```yaml
track_latest: true
```

With it, a `git` entry watching releases also asks the forge for its latest release: where `/releases/latest` points on GitHub (the API, with a token), GitLab's latest permalink, or Forgejo's latest release. A `steam` entry asks for the newest post the developer marked as an update. It's added only when the list doesn't have it already, so nothing is reported twice. Without a GitHub token, that release has no date. `check --test -v` lists the extra requests.

## How long history is kept

The history database remembers every item it has reported so nothing repeats. To bound its growth, an item that hasn't appeared in any fetch for a year is forgotten, along with everything from targets you've removed from the config. Change the window with `expiry:`, or turn pruning off:

```yaml
expiry: 180d      # 30d, 52w, 2y, ... (at least 1d, max 100y)
# expiry: never   # keep everything
```

Pruning is safe: an item still listed by its source is never forgotten, however old, so nothing is reported a second time. Targets that failed this run, or were left out with `--source`, keep their history until they next fetch cleanly.

To forget a target's history immediately, remove it with `youpdated rm --wipe`. See [Usage](usage.md#command-reference).

## Checking your config

Check your config without sending a request:

```sh
youpdated check --test -v
```

That validates the file and prints every URL it would fetch. Config mistakes exit `1` with a message for the issue. An entry a source can't read is listed in the `problems` panel while the rest are checked, and with `--fail-on-error` it exits `2`.
