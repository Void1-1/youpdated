# youpdated-scanning

Finds what's installed on this machine and adds it to your [Youpdated](https://github.com/Void1-1/youpdated) config:

- `steam`: Steam games, from each library's `appmanifest_*.acf`, under `steam`
- `homebrew`: Homebrew formulae you installed and casks, under `homebrew`
- `winget`: Windows programs winget can identify, matched against winget's local index the way winget matches them, under `winget`
- `flatpak`: Flatpak apps from Flathub, under `flathub`
- `apps`: macOS apps in `/Applications` and `~/Applications` with a Sparkle feed, under `sparkle`, or from the App Store, under `appstore`

It isn't part of the default install. Add it with the `scanning` extra:

```sh
pipx install 'youpdated[scanning]'        # or: pipx inject youpdated youpdated-scanning
youpdated scan --test                     # show what would be added
youpdated scan
youpdated scan --only steam --only apps   # certain scanners
```

While the Youpdated release with `scan` is a beta, add `--pip-args=--pre` to `pipx install`.

Scanning is local only. It reads files, runs package managers already installed and Spotlight's `mdls`, and makes no network requests on its own.
winget isn't run, because `winget export` refreshes its index over the network when that's over 15 minutes old.
