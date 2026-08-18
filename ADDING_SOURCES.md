# Adding your own source

A source is one class with two methods. This guide builds a complete, working one: a `pypi` source that reports new releases of a Python package.

- [The contract](#the-contract)
- [Two ways to add one](#two-ways-to-add-one)
- [Step 1: normalize config into targets](#step-1-normalize-config-into-targets)
- [Step 2: fetch updates](#step-2-fetch-updates)
- [The complete source](#the-complete-source)
- [Notes](#notes)
- [Testing it](#testing-it)
- [Shipping it as a package](#shipping-it-as-a-package)
- [Checklist](#checklist)

## The contract

From [youpdated/sources/base.py](youpdated/sources/base.py):

```python
class Source(Protocol):
    name: ClassVar[str]      # the key users write under `sources:` in config
    summary: ClassVar[str]   # one line shown by `youpdated sources`

    def targets(self, entries: list[Any]) -> list[Target]:
        """Normalize raw config entries into targets."""

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        """Fetch current items for one target."""
```

The split matters: `targets()` is pure config parsing and never touches the network, so `youpdated check --test` can validate a config without sending a request. `fetch()` does the network work and is called once per target, possibly on several threads at once.

## Two ways to add one

**In-tree** for a source you want in the project itself:

1. Create `youpdated/sources/yourname.py`
2. Decorate the class with `@register`
3. Import it in [youpdated/sources/\_\_init\_\_.py](youpdated/sources/__init__.py)

**As a separate package**: for a source you want to distribute without forking. Declare a
`youpdated.sources` entry point and Youpdated finds it automatically; see
[Shipping it as a package](#shipping-it-as-a-package).

## Step 1: normalize config into targets

Every source accepts two config shapes: a bare value and a mapping:

```yaml
sources:
  pypi:
    - httpx                    # bare
    - package: rich            # mapping
      name: Rich
```

`entry_fields()` collapses both into a dict, so you never branch on the shape yourself:

```python
from youpdated.models import Target
from youpdated.sources.base import ConfigEntryError, entry_fields, require


def targets(self, entries: list[Any]) -> list[Target]:
    targets = []
    for entry in entries:
        # "httpx" becomes {"package": "httpx"}; a mapping passes through.
        fields = entry_fields(entry, "package", self.name)
        package = str(require(fields, "package", self.name)).strip()
        if "/" in package or not package:
            raise ConfigEntryError(f"sources.{self.name}: `{package}` is not a package name")
        targets.append(
            Target(source=self.name, key=package, label=fields.get("name"), params={})
        )
    return targets
```

A `Target` has four fields:

| Field | Purpose |
| --- | --- |
| `source` | Your source's `name`. |
| `key` | **Stable identity.** Scopes deduplication and appears in reports. Never let it vary between runs for the same thing. |
| `label` | Optional friendly name. Falls back to `key`. May be filled in during `fetch()` once you learn it. |
| `params` | Anything `fetch()` needs (resolved URLs, watch lists, channel names) |

Raise `ConfigEntryError` for anything you can't parse. The message is shown to the user verbatim,
so name the offending value and say what was expected.

## Step 2: fetch updates

`fetch()` gets one target and the shared `Client`. **Always use that client** never `httpx` or `requests` directly. It applies the user's proxy, rotates user agents, paces requests per host, and clears cookies.

```python
def fetch(self, target: Target, client: Client) -> Iterable[Update]:
    fetched = client.get(f"https://pypi.org/pypi/{target.key}/json", conditional=True)
    if fetched is None:          # 304 Not Modified, or --test mode
        return []
    doc = fetched.json()
    ...
```

`client.get()` returns a `Fetched` (with `.content`, `.text`, `.json()`, `.status`) or `None`.
Useful arguments:

| Argument | Use for |
| --- | --- |
| `conditional=True` | Send ETag/Last-Modified and get `None` on a 304. Use it for anything polled repeatedly. |
| `soft_statuses=(404,)` | Return the response instead of raising, when a status is expected. itch answers 404 for a game with no devlog. |
| `headers={...}` | Extra request headers. The user agent is added for you. |
| `retries=0` | Skip retries when you're probing a fallback and want to fail fast. |

Any other non-200 raises `FetchError`, which the runner catches and reports without killing.

If the feed is RSS or Atom, don't parse it yourself:

```python
from youpdated.sources.feed import parse_feed

return parse_feed(fetched.content, source=self.name, target=target.key, tags=("release",))
```

## The complete source

```python
"""A example Youpdated source for PyPI package releases."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from youpdated.http import Client
from youpdated.models import Target, Update
from youpdated.registry import register
from youpdated.sources.base import ConfigEntryError, entry_fields, require

MAX_VERSIONS = 10


@register
class PyPISource:
    name: ClassVar[str] = "pypi"
    summary: ClassVar[str] = "New releases of a PyPI package"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if "/" in package or not package:
                raise ConfigEntryError(f"sources.{self.name}: `{package}` is not a package name")
            targets.append(
                Target(source=self.name, key=package, label=fields.get("name"), params={})
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        fetched = client.get(f"https://pypi.org/pypi/{target.key}/json", conditional=True)
        if fetched is None:
            return []

        doc = fetched.json()
        latest = doc["info"]["version"]

        dated = []
        for version, files in (doc.get("releases") or {}).items():
            if not files or all(f.get("yanked") for f in files):
                continue
            dated.append((version, _parse_iso(files[0].get("upload_time_iso_8601"))))
        dated.sort(
            key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True
        )

        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"version:{version}",
                title=f"{target.key} {version}",
                url=f"https://pypi.org/project/{target.key}/{version}/",
                published=published,
                version=version,
                body=doc["info"].get("summary"),
                tags=("release",) + (("latest",) if version == latest else ()),
            )
            for version, published in dated[:MAX_VERSIONS]
        ]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
```

## Notes

### The uid is the deduplication system

`uid` decides whether an item is "new".

- **Stable** across runs for the same item. Derive it from an upstream id, version, or permalink.
- **Don't** derive it from anything that changes on its own: a timestamp, a position in a list, a relative date, a view count, `hash()` (randomized per process).
- Unique **within a target** only. Deduplication is scoped to `(source, target, uid)`, so two packages can both use `version:1.0`.

```python
uid=f"version:{version}"        # good: stable, tied to the item
uid=f"{datetime.now()}"         # broken: new every run, reports forever
uid=str(index)                  # broken: shifts as items are added
```

When an upstream gives you no id at all, fingerprint the content: hash the fields that define the item. [itch.py](youpdated/sources/itch.py) does this for game builds: filenames, sizes, and the update timestamp hash into one uid.

### Filling in a label during fetch

Sometimes the friendly name is only available from the response. Assign it to `target.label`; the renderers pick it up:

```python
if not target.label:
    target.label = doc["info"].get("name")
```

### Caching a resolved value

`client.state` is a small key-value store for things that are expensive to resolve and never change: the Steam source caches appid→name there, so it looks it up once ever:

```python
cached = client.state.cache_get("my_namespace", key) if client.state else None
if cached is None:
    cached = expensive_lookup()
    if client.state:
        client.state.cache_set("my_namespace", key, cached)
```

`client.state` may be `None`

### Thread

Targets are fetched concurrently. Keep everything in local variables or on the `Target`; don't mutate shared state on the source instance.

## Testing it

The suite is offline. `respx` intercepts requests and fails on anything unmocked.

Save a real payload as a fixture:

```sh
curl -s https://pypi.org/pypi/httpx/json -o tests/fixtures/pypi_httpx.json
```

Then test against it. The `client` fixture from [tests/conftest.py](tests/conftest.py) gives you a zero-jitter client with in-memory state:

```python
import httpx
import respx

from youpdated.registry import get_source
from .conftest import fixture


@respx.mock
def test_pypi_releases(client):
    respx.get("https://pypi.org/pypi/httpx/json").mock(
        return_value=httpx.Response(200, content=fixture("pypi_httpx.json"))
    )
    source = get_source("pypi")
    (target,) = source.targets(["httpx"])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.uid == f"version:{u.version}" for u in updates)
    assert sum("latest" in u.tags for u in updates) == 1
    dated = [u.published for u in updates if u.published]
    assert dated == sorted(dated, reverse=True)      # newest first
```

Worth covering explicitly:

- Both config shapes normalize to the same `key`
- Bad entries raise `ConfigEntryError`
- Uids are stable: fetch the same payload twice, compare uids
- Any expected-error path (a soft 404 returning `[]` rather than raising)

Run with `PYTHONPATH=$PWD .venv/bin/python -m pytest` (on Windows,
`$env:PYTHONPATH=$PWD; .venv\Scripts\python -m pytest`).

## Shipping it as a package

Youpdated discovers sources through the `youpdated.sources` entry point group, so a plugin needs
no changes to this repo:

```toml
# pyproject.toml of your plugin package
[project]
name = "youpdated-pypi"
version = "0.1.0"
dependencies = ["youpdated"]

[project.entry-points."youpdated.sources"]
pypi = "youpdated_pypi:PyPISource"
```

Install it alongside Youpdated and it appears immediately:

```console
$ pip install youpdated-pypi
$ youpdated sources
  ...
  pypi       New releases of a PyPI package
```

Users then configure it like any built-in source. A plugin that fails to import is skipped.

## Checklist

- [ ] `name` and `summary` set as `ClassVar`s
- [ ] `targets()` does no network I/O
- [ ] Both config shapes accepted via `entry_fields()`
- [ ] Bad config raises `ConfigEntryError` naming the bad value
- [ ] `key` is stable across runs
- [ ] `uid` is stable and derived from the item, never from the clock or list position
- [ ] All requests go through `client.get()`
- [ ] `conditional=True` on anything polled repeatedly
- [ ] Expected non-200s handled with `soft_statuses`; real failures left to raise
- [ ] Returns `[]` rather than inventing an update
- [ ] `published` is timezone-aware UTC (or `None`)
- [ ] Tests cover parsing, both config shapes, and uid stability
- [ ] Registered — `@register` plus an import, or an entry point
