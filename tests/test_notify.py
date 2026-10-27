"""`notify:` webhooks and per-update commands"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Sequence

import httpx
import pytest
import respx

from youpdated import crypto, notify
from youpdated.cli import main
from youpdated.config import ConfigError, NotifyConfig, Webhook, parse_config, split_command
from youpdated.models import Update
from youpdated.state import State

from .conftest import fixture

DISCORD = "https://discord.com/api/webhooks/123/s3cr3t-token"
LABELS = {("npm", "express"): "Express"}


def make_update(n: int = 1, **overrides) -> Update:
    fields: dict[str, Any] = dict(
        source="npm",
        target="express",
        uid=f"version:{n}",
        title=f"express 5.0.{n}",
        url=f"https://www.npmjs.com/package/express/v/5.0.{n}",
        published=datetime(2026, 10, n, tzinfo=timezone.utc),
        version=f"5.0.{n}",
        body="Fast, unopinionated, minimalist web framework",
        tags=("release",),
    )
    fields.update(overrides)
    return Update(**fields)


def parse_notify(raw):
    return parse_config({"sources": {"npm": ["x"]}, "notify": raw}).notify


# config


def test_notify_is_off_by_default():
    assert not parse_config({"sources": {"npm": ["x"]}}).notify.enabled


def test_webhook_formats_are_inferred_from_the_url():
    config = parse_notify({"webhooks": [
        DISCORD,
        "https://hooks.slack.com/services/T0/B0/xyz",
        "https://ntfy.sh/my-topic",
        {"url": "https://ntfy.example.org/updates", "format": "ntfy"},
        {"url": "https://example.com/hook", "headers": {"Authorization": "Bearer t"}},
    ]})
    assert [h.format for h in config.webhooks] == ["discord", "slack", "ntfy", "ntfy", "json"]
    assert config.webhooks[-1].headers == {"Authorization": "Bearer t"}


def test_a_single_webhook_needs_no_list():
    assert parse_notify({"webhooks": DISCORD}).webhooks == [Webhook(DISCORD, "discord")]


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com",
        {"webhooks": ["not-a-url"]},
        {"webhooks": [{"url": "https://x.org", "format": "teams"}]},
        {"webhooks": [{"url": "https://x.org", "headers": ["a"]}]},
        {"command": ""},
        {"command": "notify-send 'unbalanced"},
        {"timeout": 0},
        {"webhook": DISCORD},
        {"webhooks": ["http://[::1/x"]},
        {"webhooks": ["https:///no-host"]},
        # A typo cannot drop the auth header
        {"webhooks": [{"url": "https://x.org", "header": {"Authorization": "Bearer t"}}]},
        {"webhooks": [{"url": "https://x.org", "headers": {"Bad Header": "v"}}]},
        {"webhooks": [{"url": "https://x.org", "headers": {"X-V": "a\nb"}}]},
        {"webhooks": [{"url": "https://x.org", "headers": {"X-V": True}}]},
        {"webhooks": ["https://ntfy.sh/"]},
        {"webhooks": [{"url": "https://ntfy.example.org", "format": "ntfy"}]},
        {"timeout": "5"},
    ],
)
def test_bad_notify_config_is_rejected(raw):
    with pytest.raises(ConfigError):
        parse_notify(raw)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX quoting")
def test_command_strings_split_like_a_shell_without_running_one():
    assert split_command("notify-send 'New: {title}' {url}") == [
        "notify-send", "New: {title}", "{url}",
    ]
    assert parse_notify({"command": ["say", "{title}"]}).commands == [["say", "{title}"]]


def test_command_errors_name_where_the_command_came_from():
    with pytest.raises(ConfigError, match=r"^--notify-cmd: No closing quotation"):
        split_command("say 'oops", name="--notify-cmd")


# payloads


def test_discord_splits_at_ten_embeds_and_headlines_once():
    updates = [make_update(n) for n in range(1, 13)]
    first, second = notify.discord_payloads(updates, LABELS)
    assert len(first["embeds"]) == 10 and len(second["embeds"]) == 2
    assert first["content"] == "12 new updates" and "content" not in second
    embed = first["embeds"][0]
    assert embed["title"] == "Express: express 5.0.1"
    assert embed["url"].startswith("https://") and embed["timestamp"].startswith("2026-10-01")


def test_discord_omits_a_url_it_would_reject():
    (payload,) = notify.discord_payloads([make_update(url="")], LABELS)
    assert "url" not in payload["embeds"][0]


def test_slack_escapes_text_and_links_it():
    (payload,) = notify.slack_payloads([make_update(title="a <b> & c | d")], LABELS)
    lines = payload["text"].splitlines()
    assert lines[0] == "*1 new update*"
    assert lines[1] == (
        "• <https://www.npmjs.com/package/express/v/5.0.1|Express: a &lt;b&gt; &amp; c ¦ d>"
    )


def test_ntfy_posts_to_the_server_root_with_the_topic_in_the_body():
    hook = Webhook("https://ntfy.example.org/sub/path/my-topic", "ntfy")
    assert notify._post_url(hook) == "https://ntfy.example.org/sub/path/"

    single = notify.ntfy_payload(hook.url, [make_update()], LABELS)
    assert single == {
        "topic": "my-topic",
        "title": "Express",
        "message": "express 5.0.1",
        "click": "https://www.npmjs.com/package/express/v/5.0.1",
    }

    many = notify.ntfy_payload(hook.url, [make_update(n, title="x" * 500) for n in range(1, 21)],
                               LABELS)
    assert many["title"] == "Youpdated: 20 new updates"
    assert len(many["message"].encode()) < 4096
    assert many["message"].endswith("more")


def test_ntfy_keeps_the_access_token_in_the_query():
    hook = Webhook("https://ntfy.example.org/my-topic?auth=tk_secret", "ntfy")
    assert notify._post_url(hook) == "https://ntfy.example.org/?auth=tk_secret"
    assert notify.ntfy_payload(hook.url, [make_update()], LABELS)["topic"] == "my-topic"


def test_ntfy_without_a_topic_is_an_error():
    with pytest.raises(ValueError):
        notify.ntfy_payload("https://ntfy.sh/", [make_update()], LABELS)


# sending


@respx.mock
def test_send_posts_once_per_webhook(client):
    route = respx.post("https://example.com/hook").mock(return_value=httpx.Response(204))
    config = NotifyConfig(webhooks=[Webhook("https://example.com/hook", "json", {"X-Key": "k"})])

    errors = notify.send([make_update(1), make_update(2)], LABELS, config, client)

    assert errors == []
    request = route.calls.last.request
    assert request.headers["X-Key"] == "k"
    body = json.loads(request.content)
    assert body["count"] == 2
    assert [u["uid"] for u in body["updates"]] == ["version:1", "version:2"]
    assert body["updates"][0]["label"] == "Express"


@respx.mock
def test_a_failing_webhook_is_reported_without_its_secret(client):
    respx.post(DISCORD).mock(return_value=httpx.Response(401))
    config = NotifyConfig(webhooks=[Webhook(DISCORD, "discord")])

    (error,) = notify.send([make_update()], LABELS, config, client)

    assert error.source == "notify"
    assert error.target == "https://discord.com/…"
    assert error.message == "HTTP 401"  # the target already names the endpoint


@respx.mock
def test_send_does_nothing_without_updates(client):
    config = NotifyConfig(webhooks=[Webhook("https://example.com/hook")])
    assert notify.send([], LABELS, config, client) == []  # respx fails on any request


def _recorder(tmp_path):
    """A command that appends its argv, env, and stdin to a file"""
    script = tmp_path / "record.py"
    script.write_text(
        "import json, os, sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[2:], 'title': os.environ['YOUPDATED_TITLE'],"
        " 'stdin': json.load(sys.stdin)}) + '\\n')\n"
    )
    out = tmp_path / "calls.jsonl"
    return [sys.executable, str(script), str(out)], out


def test_commands_do_not_inherit_youpdateds_secrets(tmp_path, monkeypatch):
    for name in notify.SECRET_ENV:
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("UNRELATED_SETTING", "kept")
    out = tmp_path / "env.json"
    script = "import json, os, sys; json.dump(dict(os.environ), open(sys.argv[1], 'w'))"

    assert notify.run_command([sys.executable, "-c", script, str(out)], [make_update()], LABELS, 10) is None

    env = json.loads(out.read_text())
    assert not set(notify.SECRET_ENV) & set(env)
    assert env["UNRELATED_SETTING"] == "kept"
    assert env["YOUPDATED_TITLE"] == "express 5.0.1"


def test_command_runs_once_per_update_with_no_shell(tmp_path):
    argv, out = _recorder(tmp_path)
    hostile = make_update(2, title="$(touch pwned); `id` {url}")
    error = notify.run_command(
        argv + ["{label}", "{title}", "{nope}"], [make_update(1), hostile], LABELS, 10
    )

    assert error is None
    calls = [json.loads(line) for line in out.read_text().splitlines()]
    assert calls[0]["argv"] == ["Express", "express 5.0.1", "{nope}"]
    # Substituted text is never expanded again or handed to a shell
    assert calls[1]["argv"][1] == "$(touch pwned); `id` {url}"
    assert calls[1]["title"] == hostile.title
    assert calls[1]["stdin"]["uid"] == "version:2" and calls[1]["stdin"]["label"] == "Express"
    assert not (tmp_path / "pwned").exists()


def test_failing_commands_are_summed_up_in_one_error():
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"]
    error = notify.run_command(argv, [make_update(1), make_update(2)], LABELS, 10)
    assert error is not None
    assert error.message == "failed for 2 of 2 update(s); first: exit 3: boom"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="process groups")
def test_a_timed_out_command_takes_its_children_with_it(tmp_path):
    """`[sh, -c, ...]` must not leave what it started running"""
    pidfile = tmp_path / "child.pid"
    spawn = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    error = notify.run_command([sys.executable, "-c", spawn], [make_update()], LABELS, 2)

    assert error is not None and error.message.endswith("timed out after 2s")
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_a_missing_command_is_reported():
    error = notify.run_command(["youpdated-no-such-command"], [make_update()], LABELS, 10)
    assert error is not None and error.message == "command not found"


# check


def _setup(tmp_path, notify_block: str) -> list[str]:
    config = tmp_path / "youpdated.yaml"
    config.write_text(
        "privacy:\n  jitter: [0, 0]\n"
        f"{notify_block}"
        "sources:\n  github:\n    - python/cpython\n",
        encoding="utf-8",
    )
    return ["check", "-c", str(config), "--state", str(tmp_path / "state.db"), "--json"]


def _two_runs(args: list[str], extra: Sequence[str] = (), capsys=None) -> int:
    """Baseline from a feed missing its newest entry, then a run that finds it"""
    feed = respx.get("https://github.com/python/cpython/releases.atom")
    full = fixture("github_releases.atom").decode()
    start, end = full.index("<entry>"), full.index("</entry>") + len("</entry>")
    feed.mock(return_value=httpx.Response(200, text=full[:start] + full[end:]))
    assert main(args) == 0
    if capsys is not None:
        capsys.readouterr()
    feed.mock(return_value=httpx.Response(200, text=full))
    return main(args + list(extra))


@respx.mock
def test_check_notifies_about_new_updates_only(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    hook = respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
    args = _setup(tmp_path, "notify:\n  webhooks: [https://example.com/hook]\n")

    assert _two_runs(args) == 0

    assert hook.call_count == 1  # the baseline run reported nothing, so sent nothing
    assert json.loads(hook.calls.last.request.content)["count"] == 1


@respx.mock
@pytest.mark.parametrize(
    "extra,expected",
    [(["--no-notify"], 0), (["--all"], 0), (["--no-save"], 0), (["--no-save", "--notify"], 1)],
)
def test_check_notify_flags(tmp_path, monkeypatch, extra, expected):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    hook = respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))
    args = _setup(tmp_path, "notify:\n  webhooks: [https://example.com/hook]\n")

    assert _two_runs(args, extra) == 0

    assert hook.call_count == expected


@respx.mock
def test_notify_cmd_flag_and_failures_reach_the_report(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.post("https://example.com/hook/secret").mock(return_value=httpx.Response(403))
    argv, out = _recorder(tmp_path)
    args = _setup(tmp_path, "notify:\n  webhooks: [https://example.com/hook/secret]\n")
    command = " ".join(f'"{part}"' for part in argv) + " {version}"

    assert _two_runs(args, ["--notify-cmd", command, "--fail-on-error"], capsys) == 2

    report = json.loads(capsys.readouterr().out)
    (error,) = report["errors"]
    assert error["source"] == "notify" and "secret" not in json.dumps(error)
    (call,) = [json.loads(line) for line in out.read_text().splitlines()]
    assert call["argv"] == [report["updates"][0]["version"]]


@respx.mock
@pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")
def test_encrypted_state_is_saved_before_notifying(tmp_path, monkeypatch):
    """A run killed while a webhook hangs must not report, and notify, the same updates again"""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, "pw")
    args = _setup(tmp_path, "notify:\n  webhooks: [https://example.com/hook]\n")
    with State(tmp_path / "state.db", passphrase="pw") as state:
        state.set_last_run()  # creates the file encrypted
    recorded = []

    def send(updates, labels, config, client):
        # What a run killed at this moment leaves on disk. Read as a file: the run holds
        # the lock a State would wait for
        on_disk = sqlite3.connect(":memory:")
        on_disk.deserialize(crypto.decrypt((tmp_path / "state.db").read_bytes(), "pw"))
        recorded.extend(
            on_disk.execute(
                "SELECT 1 FROM seen WHERE source=? AND target=? AND uid=?", u.dedupe_key
            ).fetchone()
            is not None
            for u in updates
        )
        return []

    monkeypatch.setattr(notify, "send", send)
    assert _two_runs(args) == 0
    assert recorded == [True]
