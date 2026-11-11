# Privacy

- **No credentials.** Every source works without authentication. `GITHUB_TOKEN`, `GITLAB_TOKEN`, `YOUTUBE_API_KEY` and any `token_env:` are read from the environment if set (only for raising rate limits), are never written to config, and are only sent to the host they belong to. They go in request headers, never the URL, so `-v`, `--test`, and error messages don't show them, and [notification](notifications.md) commands don't inherit them or `YOUPDATED_PASSPHRASE`.
- **Local only.** Config and history live in your platform's config/data directories. Nothing is sent anywhere except the sources you list, and the [notification](notifications.md) endpoints if configured. They can be [encrypted at rest](encryption.md).
- **HTTP path.** Every request goes through [youpdated/http.py](../youpdated/http.py). `settings.privacy.proxy` covers all traffic. To check:

  ```sh
  youpdated check --all --no-save    # with settings.privacy.proxy: socks5://127.0.0.1:9
  ```

- **If the proxy fails, nothing is sent.** There is no direct fallback: if `settings.privacy.proxy` is set and the proxy is unreachable, requests error. Youpdated also checks the proxy is up *before* the run starts and refuses with exit `1` if it isn't. `--test` reports the proxy as unreachable instead of aborting, since it doesn't send anything anyway.
- **Hostnames are resolved by the proxy.** SOCKS5 CONNECT sends the domain name to the proxy (`ATYP 3`), so your local resolver never sees which sites you watch. A proxied request with a locally-resolved hostname could leak the list to your DNS.
- **No cookies are kept.** None are stored from a response, none are sent with a later request, the next hop of a redirect, or another source's request running alongside it.
- **Requests are paced** per host with random jitter instead of arriving in a burst.
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
| `youtube` | the channel feed, with fallbacks |
| `browser` | each vendor's public version history |
| `feed` | the feed itself |
| `webpage` | the page itself |

**The desktop app's Show icons** (off by default) adds requests for a picture for each target: Steam's store details and header image, a git repo owner's avatar (from the forge's API outside GitHub), a YouTube channel's page and picture, an itch game's page and cover, or a feed's image or its site's icon. They go through the same client and `settings.privacy` options as everything else, a few per check, and about once a month per target afterwards. npm, PyPI, Homebrew, and browsers have no icons and get no extra requests. The icons are kept in the history, so they can be encrypted, and turning the setting off deletes them.

**App diagnostic logs** (Settings → Advanced, off by default) writes errors to `%LOCALAPPDATA%\youpdated\app\app.log` on Windows, and is never sent anywhere. It holds the app, engine, and Windows versions, whether notifications could be shown, and errors with their stack traces, including what the engine prints when something fails. That can reveal what you follow, and file paths in it include your Windows user name, so read it before attaching it to a bug report. It isn't encrypted, even when your config and history are! Turning the setting off stops new writes but leaves the file; **Remove Youpdated from this PC** deletes it.

## The `settings.privacy` block

```yaml
settings:
  privacy:
    # proxy: socks5://127.0.0.1:9050 # Tor's default SOCKS port
    user_agent: rotate               # 'rotate', or a fixed UA string
    jitter: [0.5, 3.0]               # random gap between hits on one host
    concurrency: 4
    timeout: 20
```
