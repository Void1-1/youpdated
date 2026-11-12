# Engine contract, version 1

Used for the desktop apps and `youpdated api serve` to communicate. [contract.schema.json](contract.schema.json) has message shapes, and [fixtures/](fixtures/) has an exchange examples.

## Framing

- UTF-8 JSON, one object per line, ending with `\n`. stdout has the protocol; logs to stderr.
- The app writes requests to the engine's stdin. Closing stdin is considered `shutdown`.
- A request is `{"id": 2, "op": "check", ...}`: the op's params sit beside `id` and `op`. `id` is a positive integer the app picks, unique among active requests.
- A reply is `{"id": 2, "ok": true, ...}` with the op's fields, or `{"id": 2, "ok": false, "error": {"code": "...", "message": "..."}}`.
- An event has no `id`: `{"event": "progress", "done": 4, "of": 15}`. Only `check` sends events in v1: `progress` while it runs, and `icon` after it has replied.

`<op>.request` in the schema is a request without `id` and `op`, and `<op>.reply` is an ok reply without `id` and `ok`. Both are strict: an unknown param is `bad_request`.

## Order and concurrency

- Replies can arrive out of order. `check` runs in the background, and other ops are answered while it runs, so the inbox stays usable during a long check.
- One check at a time. A second `check` while one runs is `busy`. After a check replies it may still be fetching icons. A new `check` then waits for the current icon to be stored, stops the rest, and starts. If that takes over 30 seconds, it's `busy`.
- While a check runs, other ops share the history it has open. Otherwise the engine takes the history lock per op, so a `youpdated add` from a terminal doesn't wait for the app. When the CLI holds it for longer than the engine waits, the op fails with `history_busy`; the app can retry later.
- The engine re-reads the config on every op.

## Locking

`status.locked` is true while the config or the history is encrypted and `unlock` hasn't succeeded. Until then every op except `hello`, `status`, `unlock`, and `shutdown` fails with `locked`. The engine keeps the passphrase in memory and caches derived keys, so later ops don't each pay for scrypt.

## Errors

| Code | Means |
| --- | --- |
| `bad_request` | Params the op doesn't take, or a line that isn't JSON (`id` is null then) |
| `unknown_op` | An op this engine doesn't have; check `hello.ops` first |
| `locked` | Needs `unlock` |
| `wrong_passphrase` | `unlock` with the wrong passphrase |
| `encryption_unavailable` | The files are encrypted and this engine can't decrypt |
| `no_config` | No config yet; `config.init` makes one |
| `config_error` | The config doesn't parse. `message` for why |
| `history_busy` | Another Youpdated run holds the history lock |
| `busy` | A check is already running |
| `ambiguous` | A `targets.preview` value more than one source claims, or none claim; `choices` lists the sources that read it, then preview again with `source` |
| `unrecognized` | No source claims the value |
| `bad_entry` | The source rejects the entry |
| `not_found` | Nothing matches the request |
| `io_error` | A file couldn't be read or written |
| `proxy_unreachable` | `privacy.set` couldn't connect to the proxy; nothing was saved |
| `internal` | Bugs. `message` keeps the exception |

`message` is plain English for the user. A client treats a code it doesn't know as a generic failure.

## Versions

`hello` returns `contract: 1`. An app refuses an engine with a different contract major. Clients ignore reply fields and events they don't know.

## Other notes

- **Timestamps:** UTC and whole seconds (`2026-10-29T08:00:06Z`)
- **Item ids** are opaque and stable for as long as the item is in the inbox.
- **Baselines.** A target's first check records what exists without notification (as normal in cli). Its newest item goes into the inbox with `read: true` and `baseline: true`. After `targets.add`, the app checks the new `targets` with `check {"targets": [...]}`.
- **Alerts** are OS notifications the engine wants posted
  - High priority: one alert per target.
  - Normal: one combined alert per check. While a digest time is set there's none, and the items are `held` until `digest.send`. Turning the digest off sends what it held with the next check, in its `items` and `alerts`.
  - Low: no notification
  - `os_notifications: false` and `check {"notify": false}` leave `alerts` empty.
  - The same rules filter the config's `notify:` webhooks and commands.
- **`last_run_by`** is `app` for a check through `api`, and `cli` for `youpdated check`. When it says `cli`, a scheduled job is probably also checking, and the app shows a one-time notice (`cli_notice_shown`).
- **Snapshot privacy.** When the history is encrypted, `snapshot.write` writes only the unread count and the last check time (`redacted: true`). [snapshot.schema.json](snapshot.schema.json)
- **Privacy.** `privacy.get` reads the config's `settings.privacy` with defaults filled in. `privacy.set` changes `proxy` and `fast_mode` only. It edits the config text in place, an option set to off is commented out.
- **Icons** are off unless `show_icons` is set.
  - After replying, a check fetches icons for a few targets without one. After enabling, icons arrive over a check or two. Each is refreshed monthly.
  - `icon` events tell each target whose icon arrived
  - `icon.get` gives `null` if the setting is off, for sources without icons (npm, PyPI, Homebrew, browsers), and before an icon is fetched. The app will show a monogram for missing icons.
  - An icon is a PNG, JPEG, GIF, WebP, or ICO image, max 256 KiB, kept in history and encrypted (if enabled).
- **`Target.latest`** is the newest item for the target, CLI included. Pruning doesn't remove it, and an empty fetch with leaves it unchanged. `rm --wipe` to clear.
- **Retry.** A `source` problem's `key` includes the failed target; `check {"targets": [{"source": …, "key": …}]}` checks just it. `notify` and `config` problems have no target to retry, so their `key` is null.
- **Removing a target** with `wipe` forgets seen items, cached validators, inbox items, priority, latest item, and icon, like `youpdated rm --wipe`.

## Fixtures

`fixtures/<op>/<case>.json` has an exchange:

```json
{"description": "…", "request": {"id": 1, "op": "…"}, "events": [], "reply": {"id": 1, "ok": true}}
```

Optional `"after"` list with events sent after the reply. `fixtures/_errors/` has failures that aren't connected to one op's params, and `fixtures/_snapshot/` has example snapshot files.
