# Privacy

- **No credentials.** Every source works without authentication. `GITHUB_TOKEN`, `GITLAB_TOKEN`, `YOUTUBE_API_KEY` and any `token_env:` are read from the environment if set (only for raising rate limits), are never written to config, and are only sent to the host they belong to. They go in request headers, never the URL, so `-v`, `--test`, and error messages don't show them, and [notification](notifications.md) commands don't inherit them or `YOUPDATED_PASSPHRASE`.
- **Local only.** Config and history live in your platform's config/data directories. Nothing is sent anywhere except the sources you list, and the [notification](notifications.md) endpoints if configured. They can be [encrypted at rest](encryption.md).
- **HTTP path.** Every request goes through [youpdated/http.py](../youpdated/http.py). `privacy.proxy` covers all traffic. To check:

  ```sh
  youpdated check --all --no-save    # with privacy.proxy: socks5://127.0.0.1:9
  ```

- **If the proxy fails, nothing is sent.** There is no direct fallback: if `privacy.proxy` is set and the proxy is unreachable, requests error. Youpdated also checks the proxy is up *before* the run starts and refuses with exit `1` if it isn't. `--test` reports the proxy as unreachable instead of aborting, since it doesn't send anything anyway.
- **Hostnames are resolved by the proxy.** SOCKS5 CONNECT sends the domain name to the proxy (`ATYP 3`), so your local resolver never sees which sites you watch. A proxied request with a locally-resolved hostname could leak the list to your DNS.
- **No cookies are kept.** None are stored from a response, none are sent with a later request, the next hop of a redirect, or another source's request running alongside it.
- **Requests are paced** per site with random jitter instead of arriving in a burst, and at most `concurrency` are in flight at once. A site is a domain with its subdomains, so `github.com` and `api.github.com`, or every `<user>.itch.io` game, share one gap. While one site's gap runs out, others are checked. A host that answers `429` or an error is waited on by every request to it, and one that keeps failing isn't asked again for the rest of the check. Each site is sent one browser user agent per check, as a browser would.
- **Conditional GETs** (ETag / Last-Modified) mean unchanged feeds are re-fetched cheaply, which is both faster and less fingerprintable. `--all` turns them off, since a 304 has no items.

## What each source contacts

| Source | Endpoint |
| --- | --- |
| `git` | GitHub's `.atom` feeds (no auth needed), each other forge's public API |
| `npm` | the public registry |
| `homebrew` | the formulae.brew.sh JSON API |
| `pypi` | PyPI's JSON index, as pip reads it |
| `steam` | the store's news feed |
| `itch` | `<game>/devlog.rss` + the game page |
| `youtube` | the channel feed; while it's down, an Invidious instance (a third party, which sees the channels asked for), then the Data API with `YOUTUBE_API_KEY` |
| `browser` | each vendor's public version history |
| `feed` | the feed itself |
| `webpage` | the page itself |

**The desktop app's Show icons** (off by default) adds requests for a picture for each target: Steam's store details and header image, a git repo owner's avatar (from the forge's API outside GitHub), a YouTube channel's page and picture, an itch game's page and cover, or a feed's image or its site's icon. They go through the same client and `privacy:` settings as everything else, a few per check, and about once a month per target afterwards. npm, PyPI, Homebrew, and browsers have no icons and get no extra requests. The icons are kept in the history, so they can be encrypted, and turning the setting off deletes them.

**App diagnostic logs** (Settings → Advanced, off by default) writes errors to `%LOCALAPPDATA%\youpdated\app\app.log` on Windows and `~/Library/Logs/Youpdated/app.log` on Mac, and is never sent anywhere. It holds app, engine, and OS versions, if notifications could be shown, and errors, including engine outputs when something fails. That can reveal sources you follow, and system file paths, so read it before sharing. It isn't encrypted, even when your config and history are! Turning the setting off stops new writes but keeps the file; on Windows, **Remove Youpdated from this PC** deletes it, and on Mac you can delete the folder.

## The `privacy:` block

```yaml
privacy:
  # proxy: socks5://127.0.0.1:9050   # Tor's default SOCKS port
  user_agent: rotate                 # 'rotate', 'youpdated', or a fixed UA string
  jitter: [0.5, 3.0]                 # random gap between hits on one site
  concurrency: 4                     # requests in flight at once
  timeout: 20
  # fast_mode: true                  # see "Fast mode" below
```

## User agent

- `rotate` (default) sends each site one of a few current desktop browser strings per check, so requests blend in with ordinary browsing. Forge APIs (GitHub, GitLab, Codeberg) are sent `python-httpx/<version>` instead.
- `youpdated` sends `Youpdated/<version> (+https://github.com/Void1-1/youpdated)`. Sites can see what is asking and rate limit rather than block it. Every request says it comes from Youpdated, so `rotate` is still suggested for privacy.
- Any other string is sent as is, to every site.

**On encrypted setups with a proxy**, `youpdated` identifies your traffic explicitly. Youpdated warns about it on every check and confirms once, when `youpdated edit` saves the change or on the first `youpdated check`. A check that can't ask (no terminal, `--json`, a scheduled run) sends nothing until it's been confirmed. The answer is kept in the history.

## Fast mode

```yaml
privacy:
  fast_mode: true
```

or `youpdated check --fast` for one check. Requests to one host are 0.1–0.5s apart instead of `jitter`, and each host is paced on its own instead of each site, though platforms that rate limit their subdomains together, like itch.io, still share one gap.

> **Fast mode trades privacy for speed.** Requests with it look automated. Sites are more likely to rate limit or block the IP, and they're easier to single out from other traffic and link together, even through `privacy.proxy` or Tor. Every check run in fast mode warns about this.
