"""Push a run's new updates to webhooks and local commands (``notify:``)"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import NotifyConfig, Webhook
from .http import Client, FetchError, redact_url
from .models import RunError, Update

#: Discord allows 10 embeds/message and 6000 characters
DISCORD_EMBEDS = 10
SLACK_LINES = 20
#: ntfy rejects messages over 4096 bytes
NTFY_MESSAGE_BYTES = 3800
#: Kept short for a notification, and env
BODY_LIMIT = 300
ENV_BODY_LIMIT = 4000

_PLACEHOLDER_RE = re.compile(r"\{(source|target|label|title|url|version|published|tags|body)\}")


def send(
    updates: list[Update],
    labels: dict[tuple[str, str], str],
    notify: NotifyConfig,
    client: Client,
) -> list[RunError]:
    """Notify every configured endpoint about ``updates``. Failures returned"""
    if not updates or not notify.enabled:
        return []
    errors = []
    for hook in notify.webhooks:
        url = _post_url(hook)
        try:
            for payload in webhook_payloads(hook, updates, labels):
                client.post(url, json=payload, headers=hook.headers)
        except (FetchError, ValueError) as exc:
            # The target already names the endpoint
            message = str(exc).removeprefix(f"{redact_url(url)}: ")
            errors.append(RunError(source="notify", target=redact_url(hook.url), message=message))
        except Exception as exc:  # the run is already saved
            # Only the type: other messages may quote the URL
            errors.append(
                RunError(source="notify", target=redact_url(hook.url), message=type(exc).__name__)
            )
    for argv in notify.commands:
        error = run_command(argv, updates, labels, notify.timeout)
        if error is not None:
            errors.append(error)
    return errors


def describe(notify: NotifyConfig) -> list[str]:
    """One line per endpoint, for ``check --test``"""
    lines = [f"{hook.format} webhook {redact_url(hook.url)}" for hook in notify.webhooks]
    lines += [f"command {' '.join(argv)}" for argv in notify.commands]
    return lines

# webhooks

def _label(update: Update, labels: dict[tuple[str, str], str]) -> str:
    return labels.get((update.source, update.target), update.target)


def _trim(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _headline(count: int) -> str:
    return f"{count} new update{'s' if count != 1 else ''}"


def _post_url(hook: Webhook) -> str:
    """ntfy takes JSON at the server root, with the topic in the body"""
    if hook.format != "ntfy":
        return hook.url
    parts = urlsplit(hook.url)
    server = parts.path.rstrip("/").rpartition("/")[0]
    return urlunsplit((parts.scheme, parts.netloc, server + "/", "", ""))


def webhook_payloads(
    hook: Webhook, updates: list[Update], labels: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    """The JSON body of each request ``hook`` needs"""
    if hook.format == "discord":
        return discord_payloads(updates, labels)
    if hook.format == "slack":
        return slack_payloads(updates, labels)
    if hook.format == "ntfy":
        return [ntfy_payload(hook.url, updates, labels)]
    return [json_payload(updates, labels)]


def json_payload(updates: list[Update], labels: dict[tuple[str, str], str]) -> dict[str, Any]:
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(updates),
        "updates": [{**u.to_dict(), "label": _label(u, labels)} for u in updates],
    }


def discord_payloads(
    updates: list[Update], labels: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    payloads = []
    for start in range(0, len(updates), DISCORD_EMBEDS):
        embeds = []
        for update in updates[start : start + DISCORD_EMBEDS]:
            embed: dict[str, Any] = {
                "title": _trim(f"{_label(update, labels)}: {update.title}", 256),
                "footer": {"text": update.source},
            }
            if update.url.startswith(("http://", "https://")):
                embed["url"] = update.url
            if update.body:
                embed["description"] = _trim(update.body, BODY_LIMIT)
            if update.published:
                embed["timestamp"] = update.published.isoformat()
            embeds.append(embed)
        payload: dict[str, Any] = {"username": "Youpdated", "embeds": embeds}
        if start == 0:
            payload["content"] = _headline(len(updates))
        payloads.append(payload)
    return payloads


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payloads(
    updates: list[Update], labels: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    payloads = []
    for start in range(0, len(updates), SLACK_LINES):
        lines = [f"*{_headline(len(updates))}*"] if start == 0 else []
        for update in updates[start : start + SLACK_LINES]:
            # `|` ends the link text early
            text = _slack_escape(f"{_label(update, labels)}: {update.title}").replace("|", "¦")
            if update.url.startswith(("http://", "https://")):
                lines.append(f"• <{update.url}|{text}>")
            else:
                lines.append(f"• {text}")
        payloads.append({"text": "\n".join(lines)})
    return payloads


def ntfy_payload(
    url: str, updates: list[Update], labels: dict[tuple[str, str], str]
) -> dict[str, Any]:
    topic = urlsplit(url).path.rstrip("/").rpartition("/")[2]
    if not topic:
        raise ValueError("ntfy webhook URL has no topic, e.g. https://ntfy.sh/my-topic")
    if len(updates) == 1:
        (update,) = updates
        payload: dict[str, Any] = {
            "topic": topic,
            "title": _label(update, labels),
            "message": update.title,
        }
        if update.url.startswith(("http://", "https://")):
            payload["click"] = update.url
        return payload

    lines: list[str] = []
    size = 0
    for index, update in enumerate(updates):
        line = f"• {_label(update, labels)}: {update.title}"
        size += len(line.encode("utf-8")) + 1
        if size > NTFY_MESSAGE_BYTES:
            lines.append(f"+{len(updates) - index} more")
            break
        lines.append(line)
    return {
        "topic": topic,
        "title": f"Youpdated: {_headline(len(updates))}",
        "message": "\n".join(lines),
    }

# commands

def _fields(update: Update, label: str) -> dict[str, str]:
    return {
        "source": update.source,
        "target": update.target,
        "label": label,
        "title": update.title,
        "url": update.url,
        "version": update.version or "",
        "published": update.published.isoformat() if update.published else "",
        "tags": ",".join(update.tags),
        "body": _trim(update.body, ENV_BODY_LIMIT),
    }


def expand(argv: list[str], fields: dict[str, str]) -> list[str]:
    """Fill ``{title}``-style placeholders. Other braces are left alone"""
    return [_PLACEHOLDER_RE.sub(lambda m: fields[m.group(1)], part) for part in argv]


def _run(args: list[str], stdin: bytes, env: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """``subprocess.run`` but timeout or interrupt ends everything the command started

    ``[sh, -c, ...]`` would otherwise leave children running after ``sh`` is killed
    """
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        # Own process group, so it can be killed as one
        start_new_session=os.name != "nt",
    ) as proc:
        try:
            _, stderr = proc.communicate(stdin, timeout=timeout)
        except BaseException:
            _kill(proc)
            raise
        return proc.returncode, stderr


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    try:
        proc.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def run_command(
    argv: list[str],
    updates: list[Update],
    labels: dict[tuple[str, str], str],
    timeout: float,
) -> RunError | None:
    """run ``argv`` once per update"""
    failures: list[str] = []
    for update in updates:
        label = _label(update, labels)
        fields = _fields(update, label)
        env = dict(os.environ)
        # NUL can't be passed through the environment or argv
        env.update({f"YOUPDATED_{k.upper()}": v.replace("\0", "") for k, v in fields.items()})
        stdin = json.dumps({**update.to_dict(), "label": label}, ensure_ascii=False)
        args = [part.replace("\0", "") for part in expand(argv, fields)]
        try:
            returncode, stderr = _run(args, stdin.encode("utf-8"), env, timeout)
        except FileNotFoundError:
            return RunError(source="notify", target=argv[0], message="command not found")
        except subprocess.TimeoutExpired:
            failures.append(f"timed out after {timeout:g}s")
            continue
        except OSError as exc:
            return RunError(source="notify", target=argv[0], message=str(exc))
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
            failures.append(f"exit {returncode}" + (f": {detail[-1]}" if detail else ""))

    if not failures:
        return None
    return RunError(
        source="notify",
        target=argv[0],
        message=f"failed for {len(failures)} of {len(updates)} update(s); first: {failures[0]}",
    )
