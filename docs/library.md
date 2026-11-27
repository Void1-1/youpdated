# Using Youpdated as a library

Youpdated can run inside another Python program without a CLI, config file, or data directory. A program can use it as its internal dependency tracker, ship it as part of a product, or use it to self-update. Programs written in other languages can drive the same engine as a subprocess with `youpdated api serve` (see [apps/contract/v1](../apps/contract/v1/README.md)). This page covers the in-process Python side.

```sh
pip install youpdated                 # add youpdated[encryption] for encrypted configs or history
```

## What you can rely on

Only names in `youpdated.__all__`, imported from `youpdated` itself, are the library API. They follow [semantic versioning](https://semver.org/): a breaking change to anything this section promises is only allowed with a new major version. Every other module (`youpdated.cli`, `youpdated.edit`, `youpdated.render`, ...), and any member not listed below, is internal and can change in any release, see the [CHANGELOG.md](https://github.com/Void1-1/youpdated/blob/main/CHANGELOG.md).

| Name | What's covered |
| --- | --- |
| `newer()`, `NoVersionsError` | Its arguments, what it returns, and what it raises, as documented [below](#example-is-there-a-newer-release-of-my-program) |
| `parse_config()`, `load_config()` | Their arguments. The YAML they read is documented in [configuration](configuration.md) |
| `Config` | `sources`, `privacy`, `warnings`, and passing it to `run()` and `State(expiry=config.expiry)` |
| `PrivacyConfig` | Every field, the same as [`settings.privacy`](privacy.md#the-settingsprivacy-block). Its `user_agent` defaults to `youpdated`, where a config file's defaults to `rotate` |
| `State` | `State(path=None, passphrase=None, *, expiry=None, read_only=False)`, `close()`, and use as a context manager. For sources: `cache_get()` and `cache_set()` |
| `Client` | `Client(privacy=None, state=None, *, product=None)`, `close()`, and use as a context manager. For sources: `state`, and `get()` with the arguments in [ADDING_SOURCES.md](../ADDING_SOURCES.md#step-2-fetch-updates) |
| `Fetched` | What `Client.get()` returns: `url`, `status`, `content`, `headers`, `text`, and `json()` |
| `run()` | `run(config, state, client, *, only_sources=None, save=True, progress=None, by="library")` |
| `record()` | `record(state, result, by="library")` |
| `RunResult` | `updates`, `errors`, `warnings`, `baseline`, `baselined`, `ok` |
| `ProgressReporter` | Its three methods |
| `Update` | Every field, `dedupe_key`, and `to_dict()` |
| `Target` | `source`, `key`, `label`, `params`, and `display`. For sources: `track_latest` |
| `RunError` | `source`, `target`, `message`, and `key` |
| `Source`, `register()`, `all_sources()`, `get_source()` | The source contract in [ADDING_SOURCES.md](../ADDING_SOURCES.md): `name`, `summary`, `targets()`, `fetch()`, `caches`, and `shared_caches` |
| `entry_fields()`, `require()`, `parse_iso()`, `url_parts()`, `parse_feed()` | Helpers for writing a source, with the arguments [ADDING_SOURCES.md](../ADDING_SOURCES.md) shows |
| `YoupdatedError`, `ConfigError`, `ConfigEntryError`, `StateError`, `StateBusy`, `FetchError`, `ProxyUnavailable`, `EncryptionError`, `WrongPassphrase` | That they're raised where documented, and how they're related: every one is a `YoupdatedError`, `StateBusy` is a `StateError`, `WrongPassphrase` is an `EncryptionError`, and `ProxyUnavailable` is a `FetchError`. The message text may change |

`PrivacyConfig`, `Update`, `Target`, and `RunError` take keyword arguments only. A minor release may add a field or an optional argument (always with a default), a member, a source, a tag, or a subclass of an error, none of which should break code using the above.

Any other method, field, or argument, such as `Client(transport=...)`, `State.mark_seen()`, or `RunResult.total_fetched`, can change without notice. If you need one of them, open an issue so it can be made public.

The rest of this page is promised too, as far as it describes behavior: what's under [No unexpected side effects](#no-unexpected-side-effects) and [History files](#history-files). Some details are only how things work today. These are not promised:

- Which releases a source lists, and how far back, and how it asks its site for them.
- The text of errors, warnings, and the User-Agent, apart from your product token coming first.
- The words that mark a prerelease in a version PEP 440 can't read. More may be added.
- The hooks a source can add for `youpdated add` and the apps: `claim()`, `fallback`, `resolves()`, `resolve()`, and `instead()`, and the `accepted()` helper for `claim()`. They're described in ADDING_SOURCES.md, and can change in a minor release.

## History files

A `State` file written by any 1.x release opens in every later 1.x release. A later release may upgrade the file when it opens it, after which an older release may not be able to open it. History from any 0.x release must be opened once with the last 0.x release, which upgrades it to the full release version (v1.0.0).

## No unexpected side effects

- **Nothing is read or written unless you point at it.** The library never looks for `youpdated.yaml` or the CLI's config and history in the platform's directories. `load_config()` reads only the path given. `State()` with no path keeps history in memory, and `State(path)` writes only that file (and a `.lock` next to it).
- **Nothing is printed.** Requests are logged at `DEBUG` to the `youpdated` logger, so `logging.basicConfig(level=logging.DEBUG)` displays them. Config problems that aren't errors are returned in `Config.warnings` and `RunResult.warnings`.
- **`import youpdated` is cheap.** Each name is imported on first use, and `rich` is never imported, since it's only used in the CLI. Type checkers still see every name with its type hints, and flag a name that isn't in `__all__`.
- **Requests name your program.** The CLI sends a rotating browser User-Agent by default, but the library sends `Youpdated/<version> (+https://github.com/Void1-1/youpdated)` and puts your product token first: `Client(product="MyApp/1.2 (+https://myapp.example)")` which sends `MyApp/1.2 (+https://myapp.example) Youpdated/<version> (+...)`. The token is plain ASCII: a name and optional version, then optionally more of those or a `(comment)`. Anything else raises `ValueError` when the `Client` is made. That's the default for `Client()`, `PrivacyConfig()`, and every config the library parses. A config or `PrivacyConfig` that sets `user_agent` to `rotate` or a fixed string is followed, and `product` is then left out. The CLI's confirmation step for `user_agent: youpdated` on an encrypted, proxied setup doesn't apply here.
- **One HTTP path.** Every request still goes through `Client`, with its pacing, proxy support, and cookie removal. With `settings.privacy.proxy` set, `run()` and `newer()` first check that the proxy is reachable and raise `ProxyUnavailable` (a `FetchError`) if not, before any request. `run()` raises `ValueError` for a `Client` that doesn't use the config's proxy, so make it with `Client(config.privacy, ...)`.

Optional API keys are read from the environment, the same as for the CLI: `GITHUB_TOKEN`, `GITLAB_TOKEN`, an entry's `token_env:`, and `YOUTUBE_API_KEY`.

## Example: is there a newer release of my program?

```python
import youpdated
from myapp import __version__

try:
    found = youpdated.newer("pypi", "myapp", current=__version__, product=f"MyApp/{__version__}")
except (youpdated.FetchError, youpdated.NoVersionsError) as exc:
    found = []                       # offline, proxy down, an unreadable answer, or nothing to compare
if found:
    print(f"MyApp {found[0].version} is out: {found[0].url}")
```

`newer(source, entry, *, current, versions=None, ignore=None, prereleases=False, client=None, product=None)` fetches once and returns every release the source lists that's newer than `current`, highest first. An empty list means up to date. It keeps no history, even with a client that has one, so unlike `run()` there's no first-run baseline, and a later `run()` with the same client still reports what `newer()` saw.

- `source` and `entry` are what a config lists under `sources:`: `newer("github", "owner/repo", ...)`, `newer("npm", "@scope/pkg", ...)`, or a dict entry like `{"package": "myapp", "versions": {"match": "2.*"}}`.
- `versions` takes the entry's [`versions:` rules](configuration.md). `{"bump": "minor"}` only reports releases that raise the minor number over `current`, and `{"min": "2.0"}` skips anything older. `ignore` takes the entry's tags to ignore. A dict entry's own rules override these one rule at a time, as in a config: an entry's `match` replaces a `match` given here, but a `min` given here still applies.
- Versions are compared by [PEP 440](https://peps.python.org/pep-0440/) when both fit that style (`1.3.0rc1 < 1.3.0 < 1.3.0.post1`). Anything else is compared by its numbers. Prereleases are left out unless `prereleases=True`. A release is a prerelease when its source tags it, when PEP 440 says so, or, for a version PEP 440 can't read, when a word like `beta`, `rc`, or `nightly` stands on its own in it. A final release is newer than its prerelease, so `1.3.0` is reported as after `1.3.0rc1`.
- A source lists only its recent releases, so the newest release is always there, but when `current` is far behind, older releases between it and the newest ones may be left out. How many a source lists isn't set permanently (these sites may change how they function). Today, PyPI and npm list their ten newest versions and ten newest final releases, and a feed lists its latest entries.
- `client` reuses your `Client`, which is left open. Without one, a client is made with `product` and closed afterwards. Calls sharing a client, from one thread or several, are independent: each fetches its own copy.
- It raises `ConfigError` for an unknown source or bad rules, `ConfigEntryError` for an entry the source can't read, `NoVersionsError` when nothing fetched has a version, and `FetchError` when the fetch fails: offline, error status, an answer the source can't read, or `ProxyUnavailable`. A source's own exception is chained as the `FetchError`'s `__cause__`. A failure is never reported as "up to date", and catching `YoupdatedError` catches all.

Youpdated never checks for its own updates by default. `newer()` checks only what your program names.

## Example: tracking dependencies

```python
import youpdated

config = youpdated.parse_config({
    "sources": {
        "pypi": ["httpx", "rich"],
        "github": ["owner/repo"],
    },
    "versions": {"pypi": {"bump": "minor"}},
})

with (
    youpdated.State("/var/lib/myapp/youpdated.sqlite3", expiry=config.expiry) as state,
    youpdated.Client(config.privacy, state, product="MyApp/1.2") as client,
):
    result = youpdated.run(config, state, client, by="myapp")

for update in result.updates:
    print(update.source, update.target, update.version, update.url)
for error in result.errors:
    print(f"{error.source} {error.target}: {error.message}")
```

- **The first run of a target is a baseline.** Its items are recorded, not reported, so a newly added dependency doesn't flood you with history. Those targets are in `result.baselined`, and `result.baseline` is true when every target was new. Use `newer()` when you need an answer on the first call.
- **History.** Keep the `State` file somewhere your program owns. `State()` with no path is in memory and forgets everything when closed (like for tests). `State(path, passphrase=...)` encrypts it (requires `youpdated[encryption]`). Opening an encrypted file without its passphrase raises `EncryptionError`, and with the wrong one, `WrongPassphrase`.
- **Saving.** `run(..., save=False)` reports without recording. Call `youpdated.record(state, result)` later to record it. `by` names what ran the check, `library` unless given, and is recorded as the history's last run.
- **Config from a file.** `youpdated.load_config("/etc/myapp/deps.yaml", passphrase=None)` takes the same YAML as the CLI, with `user_agent` defaulting to `youpdated` when unset. Like `parse_config()`, it accepts a config that watches nothing unless given `allow_empty=False`.
- **Failures.** A failing target doesn't stop the others. Its error is in `result.errors`, and `result.ok` is false. An unreachable proxy is the exception: `run()` raises `ProxyUnavailable` before fetching anything, and records nothing.
- **Progress.** Pass `progress=` an object with `run_started(total)`, `target_started(target)`, and `target_finished(target, error)`.
- **One run per client at a time.** During a run, a `Client` reuses a document that several targets share and stops asking a host that's down or rate limiting. Runs overlapping on one client share that memory until the last one ends, so give each concurrent `run()` its own `Client`. A source may call `newer()` with the run's client, and it then reuses the run's copies.

## Your own sources

A source added with `@youpdated.register`, or shipped as a `youpdated.sources` entry point, works with both `run()` and `newer()`. See [ADDING_SOURCES.md](../ADDING_SOURCES.md). Under `newer()` and in-memory use, `client.state` can be `None`, so a source must cope without its cache.
