# Contributing

Thanks for looking. This is a small project with a few constraints.

## The constraints

These are the reasons the tool exists, so a change that breaks one needs a strong argument:

1. **No accounts, no required API keys.** Every source works unauthenticated. Keys are read from the environment when present, only to raise rate limits, and are never written to disk.
2. **Nothing leaves the machine** except requests to the sources the user configured. No telemetry, no crash reporting, no update checks for Youpdated itself.
3. **One HTTP path.** Every request goes through `client.get()` in [youpdated/http.py](youpdated/http.py).
4. **Files stay in the platform's config and data directories.** Nowhere else.

## Setting up

```sh
python3 -m venv .venv
.venv/bin/pip install ".[dev]"
PYTHONPATH=$PWD .venv/bin/python -m pytest
```

`PYTHONPATH=$PWD` makes your working tree take effect without reinstalling. On Windows:
`$env:PYTHONPATH=$PWD; .venv\Scripts\python -m pytest`.

## Sending a change

`main` requires a pull request with passing CI:

```sh
git switch -c my-change
# work, commit
git push -u origin my-change
gh pr create --fill
```

CI runs the suite on Linux, macOS, and Windows across Python 3.11–3.14. All 13 jobs must be green, and the branch must be up to date with `main`. No review approvals are required.

## Tests

The suite is **fully offline**. `respx` intercepts HTTP and fails on any unmocked request, so a green run proves the parsers work rather than that the network happened to be up. Keep it that way: capture a real payload into [tests/fixtures/](tests/fixtures/) and test against that.

```sh
curl -s https://example.com/api/thing -o tests/fixtures/thing.json
```

Trim large fixtures to the part that matters.

## Adding a source

Read [ADDING_SOURCES.md](ADDING_SOURCES.md), it builds a complete working source.

## Style

Match the surrounding code rather than a style guide. Concretely:

- Type annotations throughout, with `from __future__ import annotations`
- Comments should explain why, especially where the code looks odd because an upstream service is odd.
  The Brave source reads the REST API instead of the atom feed for a reason and that reason is a comment.
- Errors that reach the user name the offending value and say what was expected
- A source raises on genuine failure: the runner catches it and reports without killing the run and returns `[]` when it's working but has nothing to report

## Reporting things

Issue templates cover bugs, feature requests, and new-source requests. For security, see
[SECURITY.md](SECURITY.md) and use private vulnerability reporting rather than a public issue.

## Use of AI

If you want to use AI to code for you or aid in your coding when contributing here, that's fine, just actually read it's output before submitting anything. No bloat or bad code is a goal you need to try to keep to.

Also, if you could acknowledge what AI did, that would be great!
