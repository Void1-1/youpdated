# Youpdated

[![tests](https://github.com/Void1-1/youpdated/actions/workflows/tests.yml/badge.svg)](https://github.com/Void1-1/youpdated/actions/workflows/tests.yml)

Youpdated keeps track of the games, apps, and packages you follow, and tells you what changed since you last checked in.

It is built to be **privacy-preserving, anonymous, and customizable**: no accounts, no API keys required, everything only stored locally.

```text
╭─ steam ──────────────────────────────────────────────────────────╮
│ Team Fortress 2  (440)                                           │
│   • CLTF2 Halloween Cup   7d ago                                 │
│     https://store.steampowered.com/news/app/440/view/6728766547… │
╰──────────────────────────────────────────────────────────────────╯
╭─ git ────────────────────────────────────────────────────────────╮
│ github.com/python/cpython                                        │
│   • v3.14.7   5d ago                                             │
│     https://github.com/python/cpython/releases/tag/v3.14.7       │
╰──────────────────────────────────────────────────────────────────╯
```

## Sources

| Source | What it watches |
| --- | --- |
| `git` | Releases, tags, and commits on GitHub, GitLab, Codeberg, or any Forgejo/Gitea server |
| `npm` | New versions of a JavaScript package |
| `homebrew` | New versions of a Homebrew formula or cask |
| `pypi` | New releases of a Python package, and if it's archived or deprecated |
| `steam` | Patch notes and news for a game |
| `itch` | Devlog posts and new builds of an itch.io game |
| `youtube` | New videos on a channel or playlist |
| `browser` | New Chrome, Brave, Firefox, and Edge releases |
| `feed` | Anything else with an RSS or Atom feed |
| `webpage` | The version a download page shows, for anything with no feed |

## Get started

### 1. Install

Works on macOS, Linux, and Windows. Needs **Python 3.11 or newer** (`python3 --version` to check). Then:

```sh
pipx install youpdated
```

[pipx](https://pipx.pypa.io) installs command-line tools so they don't interfere with anything else.
`pip install youpdated` works too.

Check it worked with:

```sh
youpdated --version
```

### 2. Create your config

```sh
youpdated init
```

This creates a starter config file and **prints where it was placed**.

### 3. Add sources to watch

Name the source, then what to watch, in any form from [Where to find identifiers](#where-to-find-identifiers):

```sh
youpdated add git neovim/neovim
youpdated add npm react
youpdated add youtube @NASA
```

A link or handle that can only work with one source doesn't need to be named: `youpdated add https://github.com/neovim/neovim`. Any other web address goes under `webpage`.

To watch all dependencies of a project, import its `package.json`, `requirements.txt`, or `Brewfile`: `youpdated import package.json`.

The starter config comes with examples; `youpdated rm` to remove any you don't want.

Or open the file and list them under each source yourself:

```yaml
sources:
  git:
    - python/cpython
  steam:
    - 440
  youtube:
    - "@NASA"
```

### 4. Run it

```sh
youpdated check
```

**The first run will not display updates.** That's intended: it records everything that already exists, so later runs show only what's new. To see everything now, run `youpdated check --all`.

## Where to find identifiers

| Source you want watched | Example of what to put in config | How to find it |
| --- | --- | --- |
| A GitHub repo | `python/cpython` | The `owner/repo` part of the repo URL. A full `https://github.com/owner/repo` URL also works. |
| A GitLab, Codeberg, or Forgejo/Gitea repo | `https://gitlab.com/gitlab-org/cli` | The repo's URL as it appears in the address bar. |
| An npm package | `express`, `"@types/node"` | The package name on npmjs.com. **Quote names starting with `@`.** |
| A Homebrew formula or cask | `wget`, `cask:firefox` | The name after `brew install`. Put `cask:` in front of casks. |
| A PyPI package | `httpx` | The name after `pip install`. |
| A Steam game | `440` | The number in the store URL: `store.steampowered.com/app/`**`440`**`/Team_Fortress_2/`. The full store URL also works. |
| An itch.io game | `https://user.itch.io/game-name` | The game's page URL, exactly as it appears in the address bar. |
| A YouTube channel | `"@NASA"` | The `@handle` from the channel URL. **Keep the quotes with `@` characters** |
| A YouTube playlist | `playlist: PLxxxx` | The `list=` part of the playlist URL. A full playlist URL also works. |
| A browser | `chrome`, `brave`, `firefox`, `edge` | Just the name. |
| Anything else | `https://example.com/feed.xml` | Any RSS or Atom feed URL. Many sites publish one at `/feed.xml`, `/releases.atom`, or `/blog/rss`. |
| Anything without a feed | `https://www.videolan.org/vlc/` | The download page's URL. The version is read off the page. See [Web pages](docs/configuration.md#web-pages). |

## Basic commands

```sh
youpdated check              # what's new since the last run
youpdated check --all        # everything, not just what's new
youpdated check --since 7d   # only things from the last week
youpdated check -s steam     # only one source
youpdated check --test -v    # check your config without fetching anything
youpdated add npm react      # start watching something
youpdated remove npm react   # stop watching it
youpdated import Brewfile    # watch everything a package.json, requirements.txt, or Brewfile lists
youpdated uninstall          # remove Youpdated and its files
```

## Privacy

- **No accounts or keys.** Every source works without sign in.
- **Everything stays local.** Nothing is sent anywhere except the sites you choose to watch.
- **Optional extras:** route everything through Tor or another proxy, and encrypt your config with a passphrase.

## Learn more

- [Configuration](https://github.com/Void1-1/youpdated/blob/main/docs/configuration.md): all options, ignoring update types, and how long history is kept
- [Usage](https://github.com/Void1-1/youpdated/blob/main/docs/usage.md): all commands, JSON and RSS output, and running on a schedule
- [Notifications](https://github.com/Void1-1/youpdated/blob/main/docs/notifications.md): get updates on your phone, Discord, Slack, or to a script
- [Privacy](https://github.com/Void1-1/youpdated/blob/main/docs/privacy.md): proxies, Tor, and details on what information is sent where
- [Encryption at rest](https://github.com/Void1-1/youpdated/blob/main/docs/encryption.md): lock your config and history behind a passphrase
- [Troubleshooting](https://github.com/Void1-1/youpdated/blob/main/docs/troubleshooting.md): common issues, and more about uninstalling
- [Contributing](https://github.com/Void1-1/youpdated/blob/main/CONTRIBUTING.md): installing from source, running the tests, and [adding a source](https://github.com/Void1-1/youpdated/blob/main/ADDING_SOURCES.md)
