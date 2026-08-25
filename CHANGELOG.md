# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] — 2026-08-25

### Fixed

- **The release workflow's `attach` job failed on v0.2.0 and has been removed.** It copied the
  built artifacts onto the GitHub Release, but this repo has immutable releases enabled, which
  freezes assets when the release is published — and the job runs after that, so it could only ever
  fail (`HTTP 422: Cannot upload assets to an immutable release`). Publishing to PyPI was
  unaffected; 0.2.0 shipped correctly. Releases from now on will not carry attached `.whl`/`.tar.gz`
  files. Get them from PyPI, where they are covered by a signed PEP 740 attestation binding each
  digest to this repository and workflow — a stronger guarantee than the copy the job was making.

- **Targets sharing one upstream document lost their updates**
  Every Brave channel is served by one GitHub releases document, and every Firefox channel by one
  Mozilla JSON. Each channel fetched it separately, so the first stored an ETag and the rest were
  answered `304 Not Modified` against that ETag moments later and reported *nothing*. With the
  default `jitter`, per-host pacing serializes those requests, which is the case that loses: a
  config watching five Firefox channels and three Brave channels reported 2 of 8. Under
  concurrency it was non-deterministic: is a channel reported depended on thread timing, so a
  loaded machine changed the result.

  A run now reuses each document across the targets that share it (`Client.run_scope()`), so the
  document is fetched once and every target sees it. The same config went from 8 requests
  reporting 2 of 8 targets, to 2 requests reporting 8 of 8. (And 147 ms to 43 ms of wall time)
  Outside a run the client is unchanged: one GET per call, and a 304 still means "nothing new".

- **The YouTube official-feed path never set the channel label.** `_label_and_parse` did not do
  the labelling it was supposed to, so a channel that answered on the primary path was reported by
  its raw `@handle` while the Invidious and Data API fallbacks named it properly. The label now
  comes from the feed on all three paths.

- **`feed` sources parsed every feed twice**
  A `feed:` entry given as a bare URL had no label, so `FeedSource.fetch` parsed the document once
  to read the feed's own `<title>` and then handed the same bytes to `parse_feed`, which parsed
  them again. Feed parsing is the most expensive part of a run, so this ~doubled the
  CPU cost of every bare-URL feed. One parse now feeds both the label and the entries.

  `parse_feed()` is unchanged for callers that only need entries; it is now a wrapper over the new
  `parse_document()` / `parse_entries()` split in `youpdated.sources.feed`.

## [0.2.0] — 2026-08-19

### Added

- **Encryption at rest for the config and history** ([#5](https://github.com/Void1-1/youpdated/issues/5)).
  `youpdated encrypt` (alias `set-encrypted`) converts an existing setup in place; `youpdated decrypt`
  converts it back. Every command detects an encrypted setup on its own and asks for the passphrase
  once, or reads it from `YOUPDATED_PASSPHRASE` for unattended runs.

  Files are encrypted whole with AES-256-GCM under a scrypt-derived key (n=2¹⁶, r=8, p=1), with the
  KDF parameters authenticated as additional data so they cannot be downgraded. Decryption happens
  **in memory**: the config is parsed from a decrypted buffer and the SQLite database is
  deserialized into an in-memory database and written back encrypted when the run ends, so no
  plaintext copy is put on disk even mid-run. A read-only run leaves the file byte-for-byte alone.

  Needs the `cryptography` package: `pip install 'youpdated[encryption]'`. Installs without it
  behave exactly as before.

- **`youpdated init --encrypt`** writes the starter config already encrypted, so a setup that is
  meant to be private never has a plaintext config on disk at all — unlike `init` then `encrypt`,
  which leaves the original blocks in free space.

- **A proxy preflight.** When `privacy.proxy` is set, the proxy is checked before the run starts
  and the run is refused with exit `1` if it is unreachable. `--test` reports it instead of
  aborting.

- **`youpdated.crypto` is a documented standalone module.** `from youpdated import crypto` gives
  you the container directly; everything in its `__all__` is a supported surface. It stays an
  optional *dependency* rather than a separate distribution on purpose: nothing in it imports
  `cryptography` at module scope, so a plain install already pays nothing for it, and shipping the
  container format apart from the code that reads it would risk version skew on files that are the
  user's only copy.

### Changed

- **A dead proxy now stops the run instead of failing every target.** Requests already failed
  closed (httpx routes everything through the proxy and never falls back to a direct connection)
  but with Tor off, a run would fail each target separately, record an empty baseline, and still
  exit `0`. In a cron log that is indistinguishable from "nothing new". It now exits `1` before the
  state database is even opened, so nothing is recorded.

### Fixed

- **A broken SOCKS handshake escaped the retry path.** `socksio` raises `ProtocolError`, which is
  not an `httpx.HTTPError`, so a proxy port answering with something that is not SOCKS5 bypassed
  the retries and surfaced as a raw exception rather than a `FetchError`. The client now treats
  `SOCKSError` as a network error like any other.

- **`tests/test_cleanup.py` could delete a real `./youpdated.yaml`.** The fixture redirected the
  config and data directories but not the working directory, so `find_traces()` picked up the
  project config of whoever ran the suite from a directory that had one, and `remove_traces()`
  deleted it. The fixture now chdirs to the temp directory.

## [0.1.1] — 2026-08-18

### Fixed

- **`browser` / Brave: a server error killed the target instead of falling back.** The Brave source
  reads the GitHub REST API and keeps the `.atom` feed as a fallback, but only a 403 or 429 reached
  it. A 5xx: a timeout, or a DNS failure, was retried, then raised, and the whole target was
  reported as failed. Observed against `api.github.com` returning 504. Any failure the HTTP client
  gives up on now falls back to the atom feed; a genuine outage of *both* still reports an error.

### Changed

- Retry backoff is configurable on the HTTP client (`retry_backoff`), so the test suite no longer spends real seconds exercising retry paths. The suite went from ~4.9s to ~0.4s.

### Added

- A `release` workflow that publishes to PyPI via trusted publishing when a GitHub Release is
  published, gated on the full 13-job test matrix and on the tag matching the version in
  `pyproject.toml`.

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
- **Config**: every source entry takes a bare value for the common case or a
  mapping for advanced options
- **Incremental reporting**: a SQLite history so each run reports what changed. First run records a baseline.
- **Three output formats**: terminal report, `--json`, and `--rss`
- **Privacy controls**: optional SOCKS/HTTP proxy covering every request, user-agent rotation, per-host request pacing with jitter, per-request cookie clearing, and conditional GETs. `--test` prints URLs without sending
- **`youpdated uninstall`** to remove every file from the tool. Refuses directories it didn't create or have other files.

### Known issues

- YouTube's RSS endpoint throttles occassionaly and 404s valid URLs; fallback covers, but a run can still fail all three. Retry or set `privacy.proxy`.
- Some itch games publish no "Updated" timestamp, so build updates are reported undated.
- Firefox publishes current versions, so it reports one item per channel.
- Edge exposes release notes only for the stable and beta channels. (But like, it's Edge, why do you want to know when it updates?)

[0.2.1]: https://github.com/Void1-1/youpdated/releases/tag/v0.2.1
[0.2.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.2.0
[0.1.1]: https://github.com/Void1-1/youpdated/releases/tag/v0.1.1
[0.1.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.1.0
