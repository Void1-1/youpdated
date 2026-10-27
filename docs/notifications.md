# Notifications

Youpdated only reports when it runs. To hear about updates without reading a log, add a `notify:` block. Each [scheduled run](usage.md#running-it-on-schedule) then pushes whatever it found to a configured output:

```yaml
notify:
  webhooks:
    - https://ntfy.sh/my-private-topic                     # format guessed from the URL
    - https://discord.com/api/webhooks/123/abc
    - https://hooks.slack.com/services/T000/B000/xyz
    - url: https://example.com/youpdated
      format: json                                         # json | discord | slack | ntfy
      headers:
        Authorization: Bearer abc123
  command: notify-send "{label}" "{title}"                 # once per update
  timeout: 30                                              # seconds per command
```

Notifications are sent only for new updates, after the run is recorded. A baseline run or `--all` and `--no-save` don't send anything. Add `--notify` to override or `--no-notify` to skip.

## Webhooks

Webhooks get one request per run listing updates. Discord and Slack get a few requests when there are many updates, because they cap message size.

| Format | Picked for | Sends |
| --- | --- | --- |
| `discord` | `discord.com/api/webhooks/…` | an embed per update, 10 per message |
| `slack` | `hooks.slack.com` | a linked line per update |
| `ntfy` | `ntfy.sh/<topic>`; set `format: ntfy` for a self-hosted server | one push; a single update opens its link when tapped |
| `json` | anything else | `{"generated", "count", "updates": [...]}`, each update as in `--json` plus its `label` |

## Commands

Commands run once per new update. Placeholders `{source}`, `{target}`, `{label}`, `{title}`, `{url}`, `{version}`, `{published}`, `{tags}`, and `{body}` are filled in. The same values are in the environment as `YOUPDATED_TITLE`, `YOUPDATED_URL`, etc., and the update arrives as JSON on stdin. `--notify-cmd` adds a command from the command line, and can be repeated.

A command is never run through a shell. Titles come from the sites you watch, so a release named `$(rm -rf ~)` is plain text. If you need a shell, call one and read the environment, which is safe inside double quotes:

```yaml
notify:
  command: [sh, -c, 'echo "$YOUPDATED_LABEL: $YOUPDATED_TITLE" >> ~/updates.txt']
```

A command gets youpdated's environment without secrets: `YOUPDATED_PASSPHRASE`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, and `YOUTUBE_API_KEY`. A `token_env:` variable of your own is allowed through.

## When a notification fails

A failed webhook or command shows in the `problems` panel and counts for `--fail-on-error`. The updates are still recorded, so they are not sent again. Webhook URLs usually contain their secret, so errors and `-v` show only the host. Webhooks go through `privacy.proxy` like everything else.

A webhook is sent again only when it can't have arrived: connection failed, server answered `429` or `503`. A timeout or a `500`, `502`, or `504` may come after the server accepted, so that is reported as failure instead of trying a second notification. A redirect is followed only with `307` or `308`; `301`, `302`, `303` would resend it with no body, so it fails. An ntfy URL keeps its query, so `?auth=` works for a protected topic.
