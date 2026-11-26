# youpdated-scanning

Finds what's installed on this machine and adds it to your [Youpdated](https://github.com/Void1-1/youpdated) config:

- Steam games, from each library's `appmanifest_*.acf` (`steam`)
- macOS apps in `/Applications` and `~/Applications`: Sparkle feeds (`sparkle`) and App Store apps (`appstore`)
- Windows packages known to winget (`winget`)
- Homebrew formulae and casks (`homebrew`)
- Flatpak apps from Flathub (`flathub`)

It isn't part of the default install. Add it with the `scanning` extra:

```sh
pipx install 'youpdated[scanning]'        # or: pipx inject youpdated youpdated-scanning
youpdated scan --test                     # show what would be added
youpdated scan
```

Scanning is local only. It reads files and runs the package managers already on this machine, and it makes no
network requests. The only requests are the ones `youpdated check` makes later, to the sources the scan added.
