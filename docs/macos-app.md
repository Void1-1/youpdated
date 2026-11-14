# Youpdated for Mac (beta)

Youpdated for Mac lives in your menu bar, and tells you when the things you follow have updates automatically. It's an early test version, so expect rough edges and please [report any issues](#reporting-a-problem).

It needs macOS 15 or newer on Apple silicon (M1 or later). Python isn't required.

## Install

1. Download `Youpdated-<version>-macOS.dmg` from the newest Mac release on the [releases page](https://github.com/Void1-1/youpdated/releases). The newest release listed may be for the command-line tool, so look for a tag starting with `app-macos-v`.
2. Open the `.dmg` and drag Youpdated to Applications.
3. Open Youpdated. macOS will say it can't verify the app. Click **Done**, not Move to Trash.
4. Open **System Settings → Privacy & Security**, scroll down to Security, and click **Open Anyway** beside the message about Youpdated. Confirm with your password or Touch ID. (signing with Apple is currently not possible)

You will see a warning because this app isn't notarized by Apple. To check that your download is the published (and open source) one, compare its checksum with the one in the release notes:

```sh
shasum -a 256 ~/Downloads/Youpdated-<version>-macOS.dmg
```

Youpdated has no Dock icon. Look for the bell in your menu bar.

## Things to try

- Follow something: paste a link or a name into +.
- Get a notification, and try its buttons.
- Give targets different priorities, and set a daily digest in Settings.
- Right-click something you follow and choose **Filters...** to see only some of its versions.
- Turn on **Start at login**, then restart your Mac.
- Select a link in another app, then use **Watch with Youpdated** from the Services menu.
- Use the Youpdated actions in Shortcuts.
- Encrypt your setup, and try **Remember on this computer**.

## Not in this version

- **Intel Macs** aren't supported.
- **The widget and Share extension** aren't included (signing restrictions).
- **Updates aren't automatic.** The app tells you when there's a new version. Download it and replace the app in Applications. Your updates and settings will stay, but you may need to re-verify through System Settings → Privacy & Security.

Youpdated's own entry follows only the Mac app's releases. To also see the command-line tool, tick `v…` in its **Filters...**. If you followed Youpdated with 0.9.0, the entry has no filter yet, and every release will show up.

## If you also use the command line

The app and the `youpdated` command share a config and history, so what you follow in one should appear in the other.

Use version 0.5.0 or newer of the command-line tool (`youpdated --version` to check). It sends a webhook or command only the updates at its `priority:` and above, using the priorities you set in the app. It has no digest, so it ignores `digest:` and sends updates as it finds them. Older versions stop with an error if your config has either option under `notify:`. Everything the app writes itself is safe.

## Privacy

The app has no accounts or analytics, and sends requests only to the sites your sources need. On a first launch it also follows Youpdated's GitHub repo, which is how it hears about new versions (disableable). **Settings → Advanced** can hide follow suggestions, and save errors to a local log (both off by default). See [Privacy](privacy.md) for more information.

## Reporting a problem

[Open a bug report](https://github.com/Void1-1/youpdated/issues/new?template=bug_report.yml) and choose **Mac app** under Where it happened. It helps to include:

- The **App version** and **Engine version**, from the bottom of Settings.
- What you did, and what happened instead.
- For a source that fails, the text from **Copy Details** in the Problems list. Check it for tokens and private information first.
- For anything else, turn on **Settings → Advanced → Save diagnostic logs**, do the problematic action, and attach the file from **Show Log in Finder**. Check it first: [Privacy](privacy.md#what-each-source-contacts) for content logged.

## Remove it

1. Turn off **Start at login** in Settings.
2. Choose **Quit Youpdated** from the menu bar, and move the app from Applications to the Trash.

That leaves your data, which the command-line tool also uses. To remove that:

- **Your follows and history:** delete `~/Library/Application Support/youpdated/`. This also removes the command-line tool's config and history.
- **The app's display settings:** run `defaults delete io.github.void1-1.Youpdated`.
- **A diagnostic log:** delete `~/Library/Logs/Youpdated/`.
- **A passphrase:** delete the `Youpdated` item in the Passwords section of Keychain Access.
