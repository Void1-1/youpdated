# Youpdated for Windows (beta)

Youpdated for Windows lives in your notification area (system tray), and tells you when the things you follow have updates automatically. It's an early test version, so expect rough edges and please [report any issues](#reporting-a-problem).

It needs Windows 10 22H2 or Windows 11. It's built for x64 PCs, and Arm PCs run it through Windows' x64 emulation. Python isn't required.

## Install

1. Download `Youpdated-<version>-Windows-x64.zip` from the newest Windows release on the [releases page](https://github.com/Void1-1/youpdated/releases). The newest release listed may be for the command-line tool, so look for a tag starting with `app-windows-v`.
2. Right-click the zip, choose **Extract All**, and move the `Youpdated` folder somewhere it can stay, such as `%LOCALAPPDATA%\Programs`. Starting when you sign in and `youpdated://` links point at this copy, so don't run it from inside the zip or from Downloads.
3. Open `Youpdated.exe` in that folder. Windows might say it protected your PC, because the app isn't signed. Click **More info**, then **Run anyway**.

To check that your download is the published (and open source) one, compare its checksum with the one in the release notes, in PowerShell:

```powershell
Get-FileHash ~\Downloads\Youpdated-<version>-Windows-x64.zip
```

Closing the window keeps Youpdated running. Look for the bell in the notification area; it may be under the **^** arrow at first. The app offers to add itself to the Start menu.

## Things to try

- Follow something: paste a link or a name into +, or paste with a link copied.
- Get a notification, and try its buttons.
- Give targets different priorities, and set a daily digest in Settings.
- Turn on **Start Youpdated when you sign in**, then sign out and back in.
- Right-click Youpdated on the taskbar for **Check now** and **Add**.
- Open a `youpdated://add?url=<link>` link from your browser.
- Encrypt your setup, and try **Remember passphrase on this computer**.

## Not in this version

- **Updates aren't automatic.** The app tells you when there's a new version and can download its zip to your Downloads folder. Quit Youpdated, then replace the files in your `Youpdated` folder with the new ones. Your updates and settings are kept.
- **No notifications while running as administrator.** Windows might not show notifications from an app running as administrator, which is every app if User Account Control is turned off.
- **No installer.** The app is a folder you keep and run from.

Releases of the command-line tool will also show up in your feed, unless filtered out.

## If you also use the command line

The app and the `youpdated` command share a config and history, so what you follow in one should appear in the other.

Use version 0.5.0 or newer of the command-line tool (`youpdated --version` to check). It sends a webhook or command only the updates at its `priority:` and above, using the priorities you set in the app. It has no digest, so it ignores `digest:` and sends updates as it finds them. Older versions stop with an error if your config has either option under `notify:`. Everything the app writes itself is safe.

## Privacy

The app has no accounts or analytics, and sends requests only to the sites your sources need. On a first launch it also offers to follow Youpdated's GitHub repo, which is how it hears about new versions. Under advanced settings there is also an option to log errors locally for bug reporting, which is off by default. See [Privacy](privacy.md) for more information.

## Reporting a problem

[Open a bug report](https://github.com/Void1-1/youpdated/issues/new?template=bug_report.yml) and choose **Windows app** under Where it happened. It helps to include:

- The **Version** and **Engine** lines from About, at the bottom of Settings.
- What you did, and what happened instead.
- For a source that fails, the text from **Copy Details** in the Problems list. Check it for tokens and private information first.
- For anything else, turn on **Settings → Advanced → Save a diagnostic log**, make it happen again, and attach the file from **Show the log file**. Check it first: [Privacy](privacy.md#what-each-source-contacts) lists what it holds.

## Remove it

1. In Settings, choose **Remove Youpdated from this PC**. That stops it starting when you sign in, and removes `youpdated://` links, notifications, the Start menu shortcut, and any stored passphrase. You can have it delete your follows and history too.
2. Delete the `Youpdated` folder.

Your follows and history, if you kept them, are in `%LOCALAPPDATA%\youpdated`, shared with the command-line tool.
