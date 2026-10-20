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
git clone https://github.com/Void1-1/youpdated
cd youpdated
python3 -m venv .venv
.venv/bin/pip install ".[dev]"
PYTHONPATH=$PWD .venv/bin/python -m pytest
```

`PYTHONPATH=$PWD` makes your working tree take effect without reinstalling. On Windows: `$env:PYTHONPATH=$PWD; .venv\Scripts\python -m pytest`.

Run the CLI from your working tree:

```sh
PYTHONPATH=$PWD .venv/bin/python -m youpdated check --test -v
```

The install also puts a `youpdated` command at `.venv/bin/youpdated` on macOS/Linux and `.venv\Scripts\youpdated.exe` on Windows. It runs the code as of the last install, so reinstall with `.venv/bin/pip install .` after changing source code. Call it by that full path, or put the directory on your `PATH`:

```sh
export PATH="$PWD/.venv/bin:$PATH"           # macOS / Linux
```

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"   # Windows
```

A `youpdated.yaml` at the root of the checkout is picked up from any directory, so a development config can live there.

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

TLDR: implement `targets()` and `fetch()` from the protocol in [youpdated/sources/base.py](youpdated/sources/base.py), decorate the class with `@register`, and import it in [youpdated/sources/\_\_init\_\_.py](youpdated/sources/__init__.py). Reuse `entry_fields()` so your source accepts both the bare-value and mapping config shapes, and `parse_feed()` from [youpdated/sources/feed.py](youpdated/sources/feed.py) if the upstream is RSS/Atom. Third-party packages can register a source through a `youpdated.sources` entry point instead, without modifying this repo.

## Building a release

Publishing is done by [.github/workflows/release.yml](.github/workflows/release.yml) when a GitHub Release is published. It runs the full test matrix, builds, checks the tag matches the version in `pyproject.toml`, installs the built wheel to make sure it runs, and uploads to PyPI through trusted publishing. No PyPI token is stored.

1. Bump `version` in `pyproject.toml` and add the matching `CHANGELOG.md` entry in the same commit.
   **A version is permanent:** PyPI refuses to replace an existing version even after a delete.
2. Optionally check the build locally first:

   ```sh
   .venv/bin/pip install ".[publish]"
   rm -rf dist build *.egg-info
   .venv/bin/python -m build          # -> dist/*.whl and dist/*.tar.gz
   .venv/bin/twine check dist/*       # metadata and README render check
   ```

   Running the workflow by hand (`workflow_dispatch`) with `dry_run` on does the same in CI without uploading.
3. Once that commit is on `main`, create a **signed** tag, verify it, and push it:

   ```sh
   git tag -s v0.4.0 -m "v0.4.0"
   git verify-tag v0.4.0
   git push origin v0.4.0
   ```

4. Publish the release from that tag. `--verify-tag` aborts rather than letting `gh` create an unsigned tag of its own:

   ```sh
   gh release create v0.4.0 --verify-tag --notes-from-tag
   ```

## Style

Match the surrounding code rather than a style guide, but generally:

- Type annotations throughout, with `from __future__ import annotations`
- Comments should explain why, especially where the code looks odd because an upstream service is odd.
  The Brave source reads the REST API instead of the atom feed for a reason and that reason is a comment.
- Errors that reach the user name the offending value and say what was expected
- A source raises on genuine failure: the runner catches it and reports without killing the run and returns `[]` when it's working but has nothing to report

## Reporting things

Issue templates cover bugs, feature requests, and new-source requests. For security, see [SECURITY.md](SECURITY.md) and use private vulnerability reporting rather than a public issue.

## Use of AI

If you want to use AI to code for you or aid in your coding when contributing here, that's fine, just actually read it's output before submitting anything. No bloat or bad code is a goal you need to try to keep to.

Also, if you could acknowledge what AI did, that would be great!
