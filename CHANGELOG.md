# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/Void1-1/youpdated/releases/tag/v0.1.0
