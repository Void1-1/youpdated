# The frozen engine

The desktop apps run `youpdated api serve`, but an app user may not have Python. So the engine is frozen with PyInstaller: Python, the `youpdated` package, and dependencies, in a folder that can run on its own.

```sh
python3 apps/engine/build.py --python /path/to/python3
```

The result is `build/engine/dist/youpdated-engine/`. The executable has the same commands as `youpdated`. The script builds from a copy of `youpdated/`, in a new venv with the packages in `requirements.txt`, then runs `smoke.py` on the result, which makes a few real requests unless given `--offline`.

To try it with the Mac app or its tests, point them at the executable:

```sh
YOUPDATED_LIVE_ENGINE="$PWD/build/engine/dist/youpdated-engine/youpdated-engine" apps/macos/scripts/test.sh --filter LiveEngineTests
```

## Which Python

A frozen engine carries its Python, so it runs only on the macOS versions that Python was built for. Homebrew's is built for the Mac it's installed on, and the script refuses the result if that's newer than the app's `MACOSX_DEPLOYMENT_TARGET`.

Use a [python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases) `install_only` build instead, built for macOS 11 on Apple silicon. Check it against the release's `SHA256SUMS`, unpack it anywhere, and pass its `python/bin/python3`.

## Notes

Built for macOS on Apple silicon, and for Windows x64 by `.github/workflows/windows-release.yml` with python.org's Python from `actions/setup-python`. Intel Macs haven't been built.
