# Youpdated for Windows

A tray app with a feed window. Built with Fluent controls in C# with WinUI 3 (Windows App SDK 2.6) on .NET 10. Unpackaged and self-contained for x64, and runs on Windows 10 22H2 and Windows 11. Drives the bundled engine, `youpdated-engine\youpdated-engine.exe api serve`, through contract v1 in [apps/contract/v1/](../contract/v1/README.md).

## Layout

| Path | What |
| --- | --- |
| `src/Youpdated.Core` | Contract models (System.Text.Json source generation), engine connection and lifecycle, the scheduler, and the view models, including feed sections and wording. |
| `src/Youpdated.App` | WinUI 3 app: tray, window, toasts, and Windows integrations. |
| `tests/Youpdated.Core.Tests` | xUnit v3. Decodes contract fixtures, replays them through a fake engine, and drives view models. |

Features shared with macOS are in Core. Colors, initials, labels, and "Since ..." wording tested against [apps/shared/presentation-cases.json](../shared/presentation-cases.json):

| Rule | Location in Core |
| --- | --- |
| "3 new updates", "Since this morning · Checked 9:41", checking, paused | `ViewModels/Feed.cs`: `FeedHeading`, `FeedText` |
| Sections, one card per target, compact-rows, self-update card, and View options | `ViewModels/Feed.cs`: `FeedBuilder`, `ViewOptions` |
| Expanding marks read and holds the card in place | `ViewModels/FeedViewModel.cs` |
| Debounced preview, ambiguous choices, Follow with priorities | `ViewModels/SourcesViewModel.cs`: `AddSourceViewModel` |
| Ctrl+V with a link opens Add | `ViewModels/PasteToAdd.cs` |
| Following grouped by label, with `Target.latest` | `ViewModels/Following.cs` |
| One label per source and forge | `ViewModels/Labels.cs` |
| Monogram colors and initials, icons through `icon.get` and `icon` events | `ViewModels/Icons.cs` |
| Release notes as markdown | `ViewModels/Notes.cs` |

## Build, run, test

On Windows, with .NET 10 SDK and VS' "WinUI application development" (or the SDK), from `apps\windows`:

```powershell
dotnet build Youpdated.sln -c Debug
dotnet test --project tests\Youpdated.Core.Tests
```

To run it against the engine from source rather than a frozen one, with a setup of its own:

```powershell
$env:YOUPDATED_ENGINE = "C:\src\youpdated\.venv\Scripts\python.exe -m youpdated"
$env:YOUPDATED_ENGINE_CONFIG = "$env:TEMP\youpdated-dev\config.yaml"
$env:YOUPDATED_ENGINE_STATE  = "$env:TEMP\youpdated-dev\state.sqlite3"
.\src\Youpdated.App\bin\x64\Debug\net10.0-windows10.0.19041.0\win-x64\Youpdated.exe
```

Without `YOUPDATED_ENGINE`, it runs `youpdated-engine\youpdated-engine.exe` beside `Youpdated.exe`. Freeze one with [apps/engine](../engine/README.md) (`python apps\engine\build.py --python <python.exe>`) and copy `build\engine\dist\youpdated-engine` there.

On macOS or Linux, only Core and its tests build, from `apps/windows`:

```sh
dotnet test --project tests/Youpdated.Core.Tests
```

## Releasing

Releases are built on GitHub's Windows runner by `.github/workflows/windows-release.yml`, for an `app-windows-v*` tag. It checks the tag against `<Version>` in `Directory.Build.props`, runs the tests, builds the app, freezes the engine, launches the result, and keeps `Youpdated-<version>-Windows-x64.zip` and its SHA-256 as the run's artifact.

```sh
git tag -s app-windows-v<version> -m "app-windows-v<version>"
git verify-tag app-windows-v<version>
git push origin app-windows-v<version>

gh run watch <run id>            # the windows-release run for the tag
gh run download <run id> --dir build/windows-release
cat build/windows-release/*/*.sha256    # goes in the release notes

gh release create app-windows-v<version> --verify-tag --draft --latest=false --title "Youpdated for Windows <version>" --notes-file <notes>
gh release upload app-windows-v<version> build/windows-release/*/Youpdated-<version>-Windows-x64.zip
gh release edit app-windows-v<version> --draft=false --latest=false
```

- **Upload while it's a draft: publishing makes a release's files permanent.**
- `--latest=false` keeps the CLI's release as the repo's latest.
- Use a plain version number, as the Mac app does: the app follows its own repo with `ignore: [prerelease]`.
- Tag with `app-windows-v`: the app takes only those as its updates, and the asset name has to be `Youpdated-<version>-Windows-x64.zip`.

Link [docs/windows-app.md](../../docs/windows-app.md) in the notes.
