# Configuration

Everything Youpdated watches, and how, is in a YAML file. `youpdated init` writes a commented starter. For what to put under each source, see [Where to find identifiers](../README.md#where-to-find-identifiers).

- [Where the config is](#where-is-the-config)
- [Identifier details](#identifier-details)
- [Full example](#full-example)
- [Git forges](#git-forges)
- [Ignoring update types](#ignoring-update-types)
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

## Identifier details

More details and options beyond the [basics](../README.md#where-to-find-identifiers):

- **git:** `host/owner/repo` as well as a URL, and GitLab subgroups (`group/sub/project`) work. A dotted first segment reads as a host, so give a dotted group a `host:` (`repo: my.group/sub/project` with `host: gitlab.com`). A pasted page within a repo (`/releases/tag/v1`) is trimmed to the repo, but `owner/repo/extra` is refused instead of read as `owner/repo`. Self-hosted servers are covered in [Git forges](#git-forges).
- **homebrew:** only `homebrew/core` and `homebrew/cask` are covered, not third-party taps.
- **pypi:** case, `-`, `_`, and `.` don't matter. Ex: `typing_extensions` and `Typing-Extensions` are the same package.
- **steam:** the game's name is looked up for you; set `name:` to override it.
- **youtube:** a `UC…` channel id or a full channel URL also works.
- **browser:** narrow it with `platform:` and `channel:`, as in the [full example](#full-example).

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

expiry: 1y                           # forget items missing from every fetch this long

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

An update is dropped if it carries **any** tag listed. A GitHub release candidate is tagged both `release` and `prerelease`, so ignoring `prerelease` drops it while ordinary releases stay.

An itch build is tagged `build`, not `release`. It is a fingerprint of the game page, not a published release. A page with a date move and no new files is tagged `page`. So `ignore: [build]` drops new builds while keeping devlog posts, and ignoring `release` everywhere leaves itch alone. (`watch: [devlog]` on an itch entry still turns builds off outright)

Without a `GITHUB_TOKEN`, `prerelease` is inferred from the tag name (`v3.15.0rc2`, `1.2.0-beta.1`), because the anonymous `.atom` feeds don't have prerelease flags. With a token, GitHub's flag is used. GitLab has no prerelease flag, so it is always inferred; Forgejo's flag is always used.

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
