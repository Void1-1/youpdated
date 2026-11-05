# Youpdated for macOS

| Path | What |
| --- | --- |
| `Sources/YoupdatedKit/` | contract models, engine connection and lifecycle, schedule, and view models |
| `Sources/YoupdatedUI/` | The window (feed, Following, Add, Problems, and states) and Settings. Its logic is in YoupdatedKit's `Window/` |
| `Sources/Youpdated/` | The app: the menu bar item, notifications, URL scheme, Services, wake, and Shortcuts actions |
| `Tools/YoupdatedScreens/` | Renders the window with the contract fixtures, light and dark: `scripts/screens.sh [folder]` |
| `Extensions/Widget/`, `Extensions/Share/` | The widget and the Share extension. Not in the app for now: see [Signing](#signing-team-and-app-group) |
| `Tests/YoupdatedKitTests/` | Tests against the contract fixtures and a fake engine that replays them |
| `Youpdated.xcodeproj` | The app, widget, Share extension, and a test target, all over synchronized folders |
| `Config/Youpdated.xcconfig` | The bundle id, team, app group, and version |
| `Resources/Assets.xcassets/AppIcon.appiconset` | The app icon, as PNGs rendered from [apps/shared/app-icon.svg](../shared/app-icon.svg) |

## Build and run

Open `Youpdated.xcodeproj` and run the **Youpdated** scheme, or from a terminal:

```sh
xcodebuild -project Youpdated.xcodeproj -scheme Youpdated CODE_SIGNING_ALLOWED=NO build
```

Running from Xcode sets the development override below in the scheme: the engine from this checkout's `.venv`, against a scratch config and history in `~/Library/Caches/Youpdated/dev/`.

`scripts/make-dev-app.sh --open` builds the same app with SwiftPM, wraps it in an ad-hoc signed `Youpdated.app`, and opens it. That build doesn't have the Shortcut actions. It runs the engine from the checkout against a temporary config and history in `~/Library/Caches/Youpdated/dev/`. `--real` uses your config and history instead.

## Release build

`scripts/make-release.sh` builds the Release app for Apple silicon, adds the engine from [apps/engine](../engine/README.md) to `Contents/Resources/`, ad-hoc signs it, and writes `build/Youpdated-<version>-macOS.dmg` at the top of the repo. Freeze the engine first.

It builds a copy under `/tmp`, because compilers record source paths in what they build, and stops if the result contains your home folder's path or your username.

## Release

Set `MARKETING_VERSION` in `Config/Youpdated.xcconfig`, merge, then from the repo's root on an up-to-date `main`:

```sh
python3 apps/engine/build.py --python /path/to/python3 # records this commit
apps/macos/scripts/make-release.sh
shasum -a 256 build/Youpdated-<version>-macOS.dmg # goes in the release notes

git tag -s app-v<version> -m "app-v<version>"
git verify-tag app-v<version>
git push origin app-v<version>

gh release create app-v<version> --verify-tag --draft --latest=false --title "Youpdated for Mac <version>" --notes-file <notes>
gh release upload app-v<version> build/Youpdated-<version>-macOS.dmg
gh release edit app-v<version> --draft=false --latest=false
```

- **Upload while it's a draft: publishing makes a release's files permanent.**
- `--latest=false` keeps the CLI's release as the repo's latest.
- Use a plain version number, with no `-beta` and without marking the release as a prerelease. The app follows its own repo with `ignore: [prerelease]`, so testers wouldn't hear about one.
- `release.yml` skips `app-v*` releases, so this doesn't publish to PyPI.

Link [docs/macos-app.md](../../docs/macos-app.md) in the notes.

## Test

```sh
scripts/test.sh # swift test
xcodebuild -project Youpdated.xcodeproj -scheme Youpdated CODE_SIGNING_ALLOWED=NO test
YOUPDATED_LIVE_ENGINE="$PWD/../../.venv/bin/python -m youpdated" scripts/test.sh --filter LiveEngineTests
```

The scripts build under `~/Library/Caches/Youpdated/`.

## Which engine is used

The engine is `Contents/Resources/youpdated-engine/youpdated-engine api serve`. For development, either of the following overrides:

- `YOUPDATED_ENGINE` (a command, like `python3 -m youpdated`), and optional `YOUPDATED_ENGINE_CONFIG` + `YOUPDATED_ENGINE_STATE`.
- `YoupdatedEngine` in Info.plist: `command` (an array), `pythonPath`, `config`, and `state`.

The passphrase never goes through the environment: the app sends it over the pipe with `unlock`.

## Signing, team, and app group

`Config/Youpdated.xcconfig` has `YOUPDATED_BUNDLE_ID` (`io.github.void1-1.Youpdated`) and an empty `DEVELOPMENT_TEAM`. Releases are ad-hoc signed, with no team, so there's no app group. The widget and Share extension need one, so they're off until signing is possible: their targets are still in the project, but the app doesn't build or embed them.

To turn them back on, set the team, add both targets back to the Youpdated target's dependencies and an Embed Foundation Extensions phase, and put `com.apple.security.application-groups` with `$(YOUPDATED_APP_GROUP)` back in `Support/Youpdated.entitlements`. The app already handles having no group.

## To do

- `apps-release.yml`.
- Fill `Resources/Localizable.xcstrings`: building in Xcode collects every `String(localized:)`.
