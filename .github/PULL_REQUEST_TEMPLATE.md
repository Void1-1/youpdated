<!-- Keep this short. Delete anything that doesn't apply. -->

## What this changes

<!-- One or two sentences. Link an issue if there is one: "Fixes #12". -->

## Why

<!-- What was wrong or missing. -->

## How it was verified

<!-- Say what you actually ran, not what should work. -->

- [ ] `PYTHONPATH=$PWD .venv/bin/python -m pytest` passes
- [ ] Tested against the real service (say which, and what you saw)

## If this adds or changes a source

- [ ] `uid` is stable across runs and derived from the item, not the clock or list position
- [ ] `key` is stable across runs
- [ ] Both config shapes work: a bare value and a mapping
- [ ] Bad config raises `ConfigEntryError` naming the bad value
- [ ] All requests go through `client.get()`, never `httpx` directly
- [ ] Expected non-200s use `soft_statuses`; real failures are left to raise
- [ ] A fixture was captured and tests added (see [ADDING_SOURCES.md](../ADDING_SOURCES.md))

## Notes

<!-- Tradeoffs you made, anything you're unsure about, anything you deliberately left out. Flagging a gap is more useful than leaving it to be found. -->
