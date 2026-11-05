"""`youpdated api`"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from youpdated import api, crypto
from youpdated.cli import main
from youpdated.state import State, inbox_id

from .fake_source import ITEMS_ENV, write_items

ROOT = Path(__file__).parent.parent
CONTRACT = ROOT / "apps" / "contract" / "v1"
SCHEMAS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in CONTRACT.glob("*.schema.json")}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
BASE = SCHEMAS["contract.schema.json"]["$id"]
encryption = pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{BASE}#/$defs/{name}"}, registry=REGISTRY)


def check_reply(op: str, reply: dict[str, Any]) -> None:
    """``reply``, with or without its id, follows the contract"""
    if reply["ok"]:
        schema(f"{op}.reply").validate({k: v for k, v in reply.items() if k not in ("id", "ok")})
    else:
        schema("ErrorReply").validate({"id": 1, **reply})


def check_event(event: dict[str, Any]) -> None:
    schema("Event").validate(event)


# contract files


def _fixtures() -> list[Path]:
    return sorted((CONTRACT / "fixtures").rglob("*.json"))


def test_the_schemas_are_valid_json_schema():
    for document in SCHEMAS.values():
        Draft202012Validator.check_schema(document)


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: str(p.relative_to(CONTRACT / "fixtures")))
def test_every_fixture_follows_the_schema(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    kind = path.parent.name
    if kind == "_snapshot":
        Draft202012Validator(SCHEMAS["snapshot.schema.json"], registry=REGISTRY).validate(data)
        return
    request, reply = data["request"], data["reply"]
    schema("Request").validate(request)
    assert request["id"] == reply["id"]
    if kind != "_errors":
        assert request["op"] == kind
        schema(f"{kind}.request").validate(
            {k: v for k, v in request.items() if k not in ("id", "op")}
        )
    # `after` = events that follow the reply
    for event in data["events"] + data.get("after", []):
        check_event(event)
    check_reply(request["op"], reply)


def test_the_fixtures_cover_every_op():
    covered = {p.parent.name for p in _fixtures()}
    assert set(api.OPS) <= covered
    hello = json.loads((CONTRACT / "fixtures" / "hello" / "ok.json").read_text(encoding="utf-8"))
    assert hello["reply"]["ops"] == list(api.OPS)


# in-process engine


def make_config(tmp_path: Path, body: str = "", entries: tuple[str, ...] = ("alpha",)) -> Path:
    config = tmp_path / "config.yaml"
    listed = "".join(f"    - {e}\n" for e in entries)
    config.write_text(
        f"privacy:\n  jitter: [0, 0]\n{body}sources:\n  fake:\n{listed}", encoding="utf-8"
    )
    return config


class Engine(api.Engine):
    """Check every reply and event against the contract"""

    def __init__(self, *args, **kwargs):
        self.events: list[dict[str, Any]] = []
        super().__init__(*args, emit=self._event, **kwargs)

    def _event(self, event: dict[str, Any]) -> None:
        check_event(event)
        self.events.append(event)

    def __call__(self, op: str, **params: Any) -> dict[str, Any]:
        reply = self.call({"op": op, **params})
        check_reply(op, reply)
        self.follow_up()
        return reply


@pytest.fixture
def engine(tmp_path, fake):
    return Engine(make_config(tmp_path), tmp_path / "state.db")


def item(uid: str, **fields: Any) -> dict[str, Any]:
    return {"uid": uid, "published": f"2026-10-{int(uid.split('.')[-1]) + 1:02d}T12:00:00Z", **fields}


def test_a_new_targets_newest_item_lands_read(engine, fake):
    write_items(fake, {"alpha": [item("1.1"), item("1.2")], "_labels": {"alpha": "Alpha"}})

    reply = engine("check")

    assert reply["new"] == 0 and reply["alerts"] == []
    (baseline,) = reply["items"]
    assert baseline["title"] == "alpha 1.2" and baseline["read"] and baseline["baseline"]
    assert baseline["display"] == "Alpha"
    assert reply["baselined"] == [{"source": "fake", "key": "alpha", "display": "Alpha"}]
    assert [e["done"] for e in engine.events] == [0, 1]


def test_new_items_come_in_unread_and_alert(engine, fake):
    write_items(fake, {"alpha": [item("1.1")]})
    engine("check")
    write_items(fake, {"alpha": [item("1.1"), item("1.2"), item("1.3")]})

    reply = engine("check")

    assert reply["new"] == 2 and reply["unread"] == 2
    assert [i["title"] for i in reply["items"]] == ["alpha 1.3", "alpha 1.2"]
    (alert,) = reply["alerts"]
    assert alert["priority"] == "normal" and alert["target"] is None
    assert alert["item_ids"] == [i["id"] for i in reply["items"]]

    assert engine("check")["items"] == []


def test_priorities_decide_alerts(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta", "gamma"))
    write_items(fake, {})
    engine("check")
    for key, priority in (("alpha", "high"), ("gamma", "low")):
        assert engine("priority.set", source="fake", key=key, priority=priority)["ok"]
    write_items(fake, {name: [item("1.1")] for name in ("alpha", "beta", "gamma")})

    alerts = engine("check")["alerts"]

    assert [(a["priority"], a["target"] and a["target"]["key"]) for a in alerts] == [
        ("high", "alpha"),
        ("normal", None),
    ]
    assert alerts[1]["item_ids"] == [inbox_id("fake", "beta", "1.1")]


def test_no_alerts_with_os_notifications_off(engine, fake):
    engine("check")
    engine("settings.set", settings={"os_notifications": False})
    write_items(fake, {"alpha": [item("1.1")]})
    reply = engine("check")
    assert reply["new"] == 1 and reply["alerts"] == []


def test_inbox_lists_marks_and_pages(engine, fake):
    engine("check")
    write_items(fake, {"alpha": [item(f"1.{n}") for n in range(1, 6)]})
    engine("check")

    first = engine("inbox.list", limit=2)
    assert first["total"] == 5 and first["unread"] == 5 and first["cursor"]
    rest = engine("inbox.list", limit=10, cursor=first["cursor"])
    titles = [i["title"] for i in first["items"] + rest["items"]]
    assert titles == [f"alpha 1.{n}" for n in range(5, 0, -1)]
    assert rest["cursor"] is None

    opened = first["items"][0]["id"]
    assert engine("inbox.mark", read=True, ids=[opened]) == {"ok": True, "changed": 1, "unread": 4}
    assert engine("inbox.list", unread=True)["total"] == 4
    assert engine("inbox.mark", read=False, ids=[opened])["unread"] == 5
    assert engine("inbox.mark", read=True, all=True)["changed"] == 5
    assert engine("inbox.list", unread=True)["items"] == []


def test_inbox_sorts(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta"))
    engine("check")
    write_items(fake, {
        "alpha": [item("1.1")],
        "beta": [item("1.2")],
        "_labels": {"alpha": "Zed", "beta": "Ant"},
    })
    engine("check")
    engine("priority.set", source="fake", key="alpha", priority="high")

    def order(sort):
        return [i["target"] for i in engine("inbox.list", sort=sort)["items"]]

    assert order("newest") == ["beta", "alpha"]
    assert order("priority") == ["alpha", "beta"]
    assert order("name") == ["beta", "alpha"]
    assert engine("inbox.list", target={"source": "fake", "key": "alpha"})["total"] == 1


def test_snooze_is_kept_until_read(engine, fake):
    engine("check")
    write_items(fake, {"alpha": [item("1.1")]})
    (found,) = engine("check")["items"]

    until = engine("inbox.snooze", ids=[found["id"]], minutes=30)["until"]

    (listed,) = engine("inbox.list")["items"]
    assert listed["snoozed_until"] == until
    engine("inbox.mark", read=True, ids=[found["id"]])
    assert engine("inbox.list")["items"][0]["snoozed_until"] is None
    assert engine("inbox.snooze", ids=["i_nope"])["error"]["code"] == "not_found"


def test_bad_requests(engine):
    assert engine("inbox.list", limit=0)["error"] == {
        "code": "bad_request", "message": "limit must be between 1 and 500",
    }
    assert engine("inbox.list", nope=1)["error"]["code"] == "bad_request"
    assert engine("inbox.mark", read=True)["error"]["code"] == "bad_request"
    assert engine("settings.set", settings={"check_interval_hours": 5})["error"]["message"] == (
        "check_interval_hours must be one of 1, 3, 6, 12, 24"
    )
    assert engine("settings.set", settings={"color": "red"})["error"]["code"] == "bad_request"
    assert engine("check", sources=["nope"])["error"]["code"] == "bad_request"
    assert engine("inbox.purge")["error"] == {
        "code": "unknown_op", "message": "unknown op `inbox.purge`",
    }


def test_settings_have_defaults_and_keep_what_is_set(engine):
    assert engine("settings.get")["settings"] == api.DEFAULT_SETTINGS
    reply = engine("settings.set", settings={"digest_time": "18:00", "check_interval_hours": 12})
    assert reply["settings"]["digest_time"] == "18:00"
    assert engine("settings.get")["settings"]["check_interval_hours"] == 12


def test_unknown_kv_keys_are_left_alone(engine, tmp_path):
    with State(tmp_path / "state.db") as state:
        state.cache_set("app", "from_a_newer_app", "x")
    engine("settings.set", settings={"os_notifications": False})
    with State(tmp_path / "state.db") as state:
        assert state.cache_get("app", "from_a_newer_app") == "x"


def test_targets_list(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "broken"))
    write_items(fake, {"alpha": [item("1.1")], "broken": "fail", "_labels": {"alpha": "Alpha"}})
    engine("check")
    engine("priority.set", source="fake", key="alpha", priority="low")

    alpha, broken = engine("targets.list")["targets"]

    assert alpha["display"] == "Alpha" and alpha["priority"] == "low"
    assert alpha["last_checked"] is not None and alpha["last_error"] is None
    assert broken["last_checked"] is None and "broken is down" in broken["last_error"]
    (problem,) = engine("problems.list")["problems"]
    assert problem["kind"] == "source" and problem["target"] == "broken"


def test_targets_remember_their_latest_item(engine, fake, tmp_path):
    engine("check")
    assert engine("targets.list")["targets"][0]["latest"] is None
    write_items(fake, {"alpha": [item("1.1"), item("1.3", title="Alpha 1.3"), item("1.2")]})
    engine("check")

    (alpha,) = engine("targets.list")["targets"]
    assert alpha["latest"] == {"version": "1.3", "title": "Alpha 1.3", "published": "2026-10-04T12:00:00Z"}

    # kept apart from the inbox, which is pruned
    engine("inbox.mark", read=True, all=True)
    with State(tmp_path / "state.db") as state:
        state._conn.execute("DELETE FROM inbox")
        state._conn.commit()
    write_items(fake, {"alpha": []})
    engine("check")
    assert engine("targets.list")["targets"][0]["latest"]["version"] == "1.3"

    # the CLI's checks count too
    write_items(fake, {"alpha": [item("1.4")]})
    assert main(["check", "-c", str(engine.config_override), "--state", str(tmp_path / "state.db")]) == 0
    assert engine("targets.list")["targets"][0]["latest"]["version"] == "1.4"

    engine("targets.remove", source="fake", key="alpha", wipe=True)
    with State(tmp_path / "state.db") as state:
        assert state.latest() == {}


@respx.mock
def test_problems_name_the_target_to_retry(tmp_path, fake):
    config = make_config(
        tmp_path, _hooks("  webhooks: [https://example.com/hook]\n"), entries=("alpha", "broken")
    )
    respx.post("https://example.com/hook").mock(return_value=httpx.Response(502))
    engine = Engine(config, tmp_path / "state.db")
    engine("check")
    write_items(fake, {"alpha": [item("1.1")], "broken": "fail"})

    checked = engine("check")

    assert [(e["kind"], e["key"]) for e in checked["errors"]] == [("source", "broken")]
    assert [(e["kind"], e["key"]) for e in checked["notify_failures"]] == [("notify", None)]
    assert {p["key"] for p in engine("problems.list")["problems"]} == {"broken", None}
    # retry just it
    write_items(fake, {"alpha": [item("1.1")], "broken": [item("2.1")]})
    retried = engine("check", targets=[{"source": "fake", "key": checked["errors"][0]["key"]}])
    assert retried["errors"] == [] and [i["target"] for i in retried["items"]] == ["broken"]
    assert engine.events[-1]["of"] == 1 and engine.events[-1]["target"]["key"] == "broken"


def test_problems_kept_before_keys_still_read(engine, tmp_path):
    engine("status")
    with State(tmp_path / "state.db") as state:
        old = {"kind": "source", "source": "fake", "target": "alpha", "message": "boom"}
        state.cache_set("app", "problems", json.dumps({"checked_at": "2026-10-29T08:00:06Z", "problems": [old]}))
    assert engine("problems.list")["problems"] == [{**old, "key": None}]
    assert engine("targets.list")["targets"][0]["last_error"] == "boom"


def test_status(engine, fake, tmp_path):
    status = engine("status")
    assert status["counts"] == {"targets": 1, "unread": 0, "problems": 0}
    assert status["self_watch"] == "absent" and status["last_run"] is None
    engine("check")
    status = engine("status")
    assert status["last_run_by"] == "app" and status["last_run"] is not None
    engine("settings.set", settings={"self_watch_declined": True})
    assert engine("status")["self_watch"] == "declined"

    missing = Engine(tmp_path / "none.yaml", tmp_path / "none.db")("status")
    assert not missing["config_exists"] and missing["counts"] is None
    assert not (tmp_path / "none.db").exists()


def test_last_run_by_follows_whoever_checked(engine, fake, tmp_path):
    engine("check")
    assert main(["check", "-c", str(engine.config_override), "--state", str(tmp_path / "state.db")]) == 0
    assert engine("status")["last_run_by"] == "cli"
    engine("check")
    assert engine("status")["last_run_by"] == "app"


def test_check_only_some_targets(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta"))
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("2.1")]})

    reply = engine("check", targets=[{"source": "fake", "key": "beta"}])

    assert [t["key"] for t in reply["baselined"]] == ["beta"]
    assert [t["key"] for t in engine("check")["baselined"]] == ["alpha"]


def test_a_second_check_is_busy(engine, fake):
    write_items(fake, {"alpha": {"sleep": 1, "items": [item("1.1")]}})
    replies = []
    first = threading.Thread(target=lambda: replies.append(engine("check")))
    first.start()
    while not engine.checking:
        time.sleep(0.01)
    # other ops share the check's history meanwhile
    assert engine("inbox.list")["ok"]
    assert engine("check")["error"]["code"] == "busy"
    first.join()
    assert replies[0]["ok"]


def test_the_cli_holding_the_history_is_history_busy(engine, tmp_path):
    engine.lock_timeout = 0.2
    with State(tmp_path / "state.db"):
        assert engine("inbox.list")["error"]["code"] == "history_busy"


def test_add_preview_and_remove(engine, fake, tmp_path):
    write_items(fake, {"beta": [item("2.1"), item("2.2")], "_labels": {"beta": "Beta"}})

    preview = engine("targets.preview", value="fake:beta")
    assert preview["source"] == "fake" and preview["entry"] == "beta"
    assert preview["display"] == "Beta" and not preview["already_watching"]
    assert preview["newest"]["title"] == "beta 2.2"
    assert not (tmp_path / "state.db").exists()  # nothing recorded

    added = engine("targets.add", source="fake", entries=["beta"])
    assert added["added"] == ["beta"] and added["targets"][0]["key"] == "beta"
    assert engine("targets.add", source="fake", entries=["beta"])["skipped"] == ["beta"]
    assert engine("targets.preview", value="fake:beta")["already_watching"]

    engine("check", targets=[{"source": "fake", "key": "beta"}])
    write_items(fake, {"beta": [item("2.1"), item("2.2"), item("2.3")]})
    engine("check")
    engine("priority.set", source="fake", key="beta", priority="high")

    removed = engine("targets.remove", source="fake", key="beta", wipe=True)
    assert removed["removed"] == ["beta"]
    assert removed["wiped"]["inbox"] == 2 and removed["wiped"]["seen"] == 3
    assert "beta" not in engine.config_override.read_text()
    with State(tmp_path / "state.db") as state:
        assert state.priorities() == {}
    assert engine("targets.remove", source="fake", key="beta")["error"]["code"] == "not_found"


def test_removing_without_wipe_drops_unread_items(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta"))
    engine("check")
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("2.1")]})
    engine("check")

    assert engine("targets.remove", source="fake", key="beta")["wiped"] is None

    assert [i["target"] for i in engine("inbox.list")["items"]] == ["alpha"]


def test_a_cli_rm_takes_unread_items_on_the_next_check(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta"))
    engine("check")
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("2.1")]})
    engine("check")
    assert main(["rm", "-c", str(engine.config_override), "fake", "beta"]) == 0

    engine("check")

    assert [i["target"] for i in engine("inbox.list")["items"]] == ["alpha"]


def test_preview_says_which_source_is_meant(tmp_path):
    engine = Engine(tmp_path / "config.yaml", tmp_path / "state.db")
    ambiguous = engine("targets.preview", value="express")
    assert ambiguous["error"]["code"] == "ambiguous"
    assert ambiguous["error"]["choices"] == ["homebrew", "npm", "pypi"]
    assert engine("targets.preview", value="hello there")["error"]["code"] == "unrecognized"


def test_preview_reports_a_failed_fetch(engine, fake):
    write_items(fake, {"down": "fail"})
    reply = engine("targets.preview", value="down", source="fake")
    assert reply["newest"] is None and "down is down" in reply["fetch_error"]


def test_import_preview_and_apply(engine, tmp_path):
    engine.config_override.write_text(
        "privacy:\n  jitter: [0, 0]\nsources:\n  pypi:\n    - httpx\n", encoding="utf-8"
    )
    content = "httpx>=0.27\nrich\n-e ./local\n"

    preview = engine("import.preview", content=content, format="requirements.txt")
    assert preview["add"] == [{"source": "pypi", "entries": ["rich"]}]
    assert preview["already"] == [{"source": "pypi", "entries": ["httpx"]}]
    assert preview["skipped"]
    assert "rich" not in engine.config_override.read_text()

    applied = engine("import.apply", content=content, format="requirements.txt")
    assert applied["added"] == [{"source": "pypi", "entries": ["rich"]}]
    assert applied["targets"] == [{"source": "pypi", "key": "rich", "display": "rich"}]
    assert "- rich" in engine.config_override.read_text()

    bad = engine("import.preview", path=str(tmp_path / "nothing.txt"))
    assert bad["error"]["code"] == "bad_request"


def test_config_init_writes_only_the_self_watch(tmp_path):
    engine = Engine(tmp_path / "new" / "config.yaml", tmp_path / "state.db")
    assert engine("status")["config_exists"] is False

    assert engine("config.init") == {
        "ok": True,
        "config_path": str(tmp_path / "new" / "config.yaml"),
        "created": True,
        "self_watch_added": True,
    }

    (target,) = engine("targets.list")["targets"]
    assert target["starred"] and target["key"] == "github.com/Void1-1/youpdated"
    assert engine("status")["self_watch"] == "watching"
    assert engine("config.init")["self_watch_added"] is False


def test_config_init_adds_the_self_watch_to_a_cli_config(engine, tmp_path):
    before = engine.config_override.read_text()

    assert engine("config.init", self_watch=True)["self_watch_added"]

    after = engine.config_override.read_text()
    assert after.startswith(before.rstrip("\n"))
    assert "- {repo: Void1-1/youpdated, ignore: [prerelease]}" in after
    (git,) = [t for t in engine("targets.list")["targets"] if t["source"] == "git"]
    assert git["starred"]


# digest and notifications


def _hooks(body: str) -> str:
    return "notify:\n" + body


@respx.mock
def test_endpoints_take_the_priorities_they_ask_for(tmp_path, fake):
    config = make_config(
        tmp_path,
        _hooks(
            "  webhooks:\n"
            "    - https://example.com/default\n"
            "    - {url: https://example.com/high, priority: high}\n"
            "    - {url: https://example.com/everything, priority: low}\n"
        ),
        entries=("alpha", "beta", "gamma"),
    )
    routes = {
        name: respx.post(f"https://example.com/{name}").mock(return_value=httpx.Response(204))
        for name in ("default", "high", "everything")
    }
    engine = Engine(config, tmp_path / "state.db")
    engine("check")
    engine("priority.set", source="fake", key="alpha", priority="high")
    engine("priority.set", source="fake", key="gamma", priority="low")
    write_items(fake, {name: [item("1.1")] for name in ("alpha", "beta", "gamma")})

    assert engine("check")["notify_failures"] == []

    def got(name):
        return sorted(u["target"] for u in json.loads(routes[name].calls.last.request.content)["updates"])

    assert got("default") == ["alpha", "beta"]
    assert got("high") == ["alpha"]
    assert got("everything") == ["alpha", "beta", "gamma"]


@respx.mock
def test_a_digest_holds_what_it_would_send(tmp_path, fake):
    config = make_config(
        tmp_path,
        _hooks(
            "  webhooks:\n"
            "    - https://example.com/digest\n"
            "    - {url: https://example.com/now, digest: false}\n"
        ),
        entries=("alpha", "beta"),
    )
    digest = respx.post("https://example.com/digest").mock(return_value=httpx.Response(204))
    now = respx.post("https://example.com/now").mock(return_value=httpx.Response(204))
    engine = Engine(config, tmp_path / "state.db")
    engine("check")
    engine("priority.set", source="fake", key="alpha", priority="high")
    engine("settings.set", settings={"digest_time": "18:00"})
    write_items(fake, {"alpha": [item("1.1")], "beta": [item("2.1")]})

    checked = engine("check")

    assert checked["held"] == 1
    assert [a["priority"] for a in checked["alerts"]] == ["high"]
    assert [u["target"] for u in json.loads(digest.calls.last.request.content)["updates"]] == ["alpha"]
    assert len(json.loads(now.calls.last.request.content)["updates"]) == 2

    sent = engine("digest.send")

    assert [i["target"] for i in sent["items"]] == ["beta"]
    assert sent["alerts"] == [
        {"priority": "normal", "target": None, "item_ids": [inbox_id("fake", "beta", "2.1")]}
    ]
    assert [u["target"] for u in json.loads(digest.calls.last.request.content)["updates"]] == ["beta"]
    assert now.call_count == 1  # it had them already
    assert engine("digest.send")["items"] == []


@respx.mock
def test_turning_the_digest_off_sends_what_it_held_next_check(tmp_path, fake):
    config = make_config(
        tmp_path,
        _hooks(
            "  webhooks:\n"
            "    - https://example.com/digest\n"
            "    - {url: https://example.com/now, digest: false}\n"
        ),
    )
    digest = respx.post("https://example.com/digest").mock(return_value=httpx.Response(204))
    now = respx.post("https://example.com/now").mock(return_value=httpx.Response(204))
    engine = Engine(config, tmp_path / "state.db")
    engine("check")
    engine("settings.set", settings={"digest_time": "08:30"})
    write_items(fake, {"alpha": [item("1.1")]})
    assert engine("check")["held"] == 1
    assert digest.call_count == 0 and now.call_count == 1

    engine("settings.set", settings={"digest_time": None})
    # nothing goes out until the next check
    assert digest.call_count == 0

    checked = engine("check")

    held = inbox_id("fake", "alpha", "1.1")
    assert checked["new"] == 0 and [i["id"] for i in checked["items"]] == [held]
    assert checked["alerts"] == [{"priority": "normal", "target": None, "item_ids": [held]}]
    assert [u["uid"] for u in json.loads(digest.calls.last.request.content)["updates"]] == ["1.1"]
    assert now.call_count == 1  # it had it already
    assert engine("check")["alerts"] == [] and digest.call_count == 1
    assert engine("inbox.list", unread=True)["total"] == 1


def test_held_items_wait_out_a_check_that_sends_nothing(engine, fake):
    engine("check")
    engine("settings.set", settings={"digest_time": "08:30"})
    write_items(fake, {"alpha": [item("1.1")]})
    engine("check")
    engine("settings.set", settings={"digest_time": None})

    assert engine("check", notify=False)["alerts"] == []

    assert len(engine("check")["alerts"]) == 1


def test_an_empty_config_watches_nothing(tmp_path, fake):
    config = tmp_path / "config.yaml"
    engine = Engine(config, tmp_path / "state.db")
    assert engine("config.init", self_watch=False)["created"]

    status = engine("status")
    assert status["config_error"] is None and status["counts"]["targets"] == 0
    assert status["self_watch"] == "absent"
    assert engine("targets.list")["targets"] == []
    checked = engine("check")
    assert checked["items"] == [] and checked["errors"] == [] and checked["new"] == 0

    assert engine("targets.add", source="fake", entries=["alpha"])["added"] == ["alpha"]
    assert [t["key"] for t in engine("targets.list")["targets"]] == ["alpha"]


@pytest.mark.parametrize("text", ["", "sources:\n", "sources: {}\n", "sources:\n  npm:\n"])
def test_every_form_of_empty_is_fine(tmp_path, text):
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    engine = Engine(config, tmp_path / "state.db")
    assert engine("status")["counts"] == {"targets": 0, "unread": 0, "problems": 0}
    assert engine("check")["ok"]


def test_removing_the_last_entry_leaves_an_empty_config(tmp_path, fake):
    engine = Engine(tmp_path / "config.yaml", tmp_path / "state.db")
    engine("config.init")
    engine("targets.add", source="fake", entries=["alpha"])

    engine("targets.remove", source="fake", key="alpha")
    starred = engine("targets.remove", source="git", key="github.com/Void1-1/youpdated")

    assert starred["removed"] == ["github.com/Void1-1/youpdated"]
    assert engine("targets.list")["targets"] == []
    assert engine("status")["config_error"] is None
    assert engine("check")["ok"]
    # the app can watch itself once more
    assert engine("config.init")["self_watch_added"]
    (target,) = engine("targets.list")["targets"]
    assert target["starred"]


def test_the_cli_still_says_an_empty_config_checks_nothing(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("sources: {}\n", encoding="utf-8")
    assert main(["check", "-c", str(config), "--state", str(tmp_path / "state.db")]) == 1
    assert "Nothing to check" in capsys.readouterr().out


def _recorder(tmp_path: Path) -> tuple[list[str], Path]:
    script = tmp_path / "record.py"
    script.write_text(
        "import json, sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write(json.dumps(json.load(sys.stdin)['target']) + '\\n')\n"
    )
    out = tmp_path / "calls.jsonl"
    return [sys.executable, str(script), str(out)], out


def test_a_command_takes_its_own_priority_and_digest(tmp_path, fake):
    argv, out = _recorder(tmp_path)
    config = make_config(
        tmp_path,
        _hooks(f"  command:\n    run: {json.dumps(argv)}\n    priority: low\n    digest: false\n"),
        entries=("alpha", "gamma"),
    )
    engine = Engine(config, tmp_path / "state.db")
    engine("check")
    engine("priority.set", source="fake", key="gamma", priority="low")
    engine("settings.set", settings={"digest_time": "18:00"})
    write_items(fake, {"alpha": [item("1.1")], "gamma": [item("3.1")]})

    engine("check")

    assert sorted(json.loads(line) for line in out.read_text().splitlines()) == ["alpha", "gamma"]


@respx.mock
def test_the_cli_applies_priorities_and_ignores_the_digest(tmp_path, fake):
    config = make_config(
        tmp_path, _hooks("  webhooks: [https://example.com/hook]\n"), entries=("alpha", "gamma")
    )
    hook = respx.post("https://example.com/hook").mock(return_value=httpx.Response(204))
    args = ["check", "-c", str(config), "--state", str(tmp_path / "state.db"), "--json"]
    assert main(args) == 0
    with State(tmp_path / "state.db") as state:
        state.set_priority("fake", "gamma", "low")
        state.cache_set("app", "digest_time", json.dumps("18:00"))
    write_items(fake, {"alpha": [item("1.1")], "gamma": [item("3.1")]})

    assert main(args) == 0

    assert [u["target"] for u in json.loads(hook.calls.last.request.content)["updates"]] == ["alpha"]


# pruning, wiping


def test_read_items_expire_and_unread_ones_stay(engine, fake, tmp_path):
    engine("check")
    write_items(fake, {"alpha": [item("1.1"), item("1.2")]})
    first, second = engine("check")["items"]
    engine("inbox.mark", read=True, ids=[first["id"]])
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("UPDATE inbox SET found_at = '2020-01-01T00:00:00Z'")
    db.commit()
    db.close()

    engine("check")

    assert [i["id"] for i in engine("inbox.list")["items"]] == [second["id"]]


def test_rm_wipe_clears_the_inbox(engine, fake, tmp_path):
    engine("check")
    write_items(fake, {"alpha": [item("1.1")]})
    engine("check")
    engine("priority.set", source="fake", key="alpha", priority="high")

    assert main(["rm", "-c", str(engine.config_override), "--wipe", "--state", str(tmp_path / "state.db"), "fake", "alpha"]) == 0

    with State(tmp_path / "state.db") as state:
        assert state.inbox_unread() == 0 and state.priorities() == {}


# encryption


@encryption
def test_locked_until_unlocked(tmp_path, fake, monkeypatch):
    config = make_config(tmp_path)
    crypto.write_private(config, crypto.encrypt(config.read_bytes(), "pw"))
    engine = Engine(config, tmp_path / "state.db")

    status = engine("status")
    assert status["locked"] and status["counts"] is None
    assert engine("inbox.list")["error"]["code"] == "locked"
    assert engine("unlock", passphrase="nope")["error"]["code"] == "wrong_passphrase"
    assert engine("unlock", passphrase="pw")["encrypted"] == {"config": True, "history": False}

    write_items(fake, {"alpha": [item("1.1")]})
    assert engine("check")["ok"]
    assert crypto.is_encrypted_file(tmp_path / "state.db")
    assert engine("inbox.list")["total"] == 1

    snapshot = tmp_path / "group" / "snapshot.json"
    assert engine("snapshot.write", path=str(snapshot))["redacted"]
    written = json.loads(snapshot.read_text())
    assert written["redacted"] and written["items"] == [] and written["unread"] == 0
    Draft202012Validator(SCHEMAS["snapshot.schema.json"], registry=REGISTRY).validate(written)


def test_a_plain_history_snapshot_has_titles(engine, fake, tmp_path):
    engine("check")
    write_items(fake, {"alpha": [item("1.1"), item("1.2")]})
    engine("check")

    reply = engine("snapshot.write", path=str(tmp_path / "snapshot.json"))

    written = json.loads((tmp_path / "snapshot.json").read_text())
    Draft202012Validator(SCHEMAS["snapshot.schema.json"], registry=REGISTRY).validate(written)
    assert not reply["redacted"] and reply["items"] == 2
    assert [i["title"] for i in written["items"]] == ["alpha 1.2", "alpha 1.1"]


@encryption
def test_the_key_cache_derives_once(monkeypatch):
    calls = []
    derive = crypto._derive

    def counting(*args):
        calls.append(args)
        return derive(*args)

    monkeypatch.setattr(crypto, "_derive", counting)
    crypto.cache_keys()
    try:
        blob = crypto.encrypt(b"one", "pw")
        again = crypto.encrypt(b"two", "pw")
        assert crypto.decrypt(blob, "pw") == b"one" and crypto.decrypt(again, "pw") == b"two"
        assert len(calls) == 1
        assert blob[:29] == again[:29] and blob[29:41] != again[29:41]
        with pytest.raises(crypto.WrongPassphrase):
            crypto.decrypt(blob, "wrong")
    finally:
        crypto.cache_keys(False)
    crypto.decrypt(blob, "pw")
    assert len(calls) == 3


_BOOT = (
    "import sys; from tests import fake_source; fake_source.register(); "
    "from youpdated.cli import main; sys.exit(main(['api', 'serve'] + sys.argv[1:]))"
)


class Served:
    """`youpdated api serve` in a child process"""

    def __init__(self, config: Path, state: Path, env: dict[str, str]):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _BOOT, "--config", str(config), "--state", str(state)],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **env},
        )
        self.next_id = 0
        self.events: list[dict[str, Any]] = []
        self.replies: dict[int, dict[str, Any]] = {}
        self.ops: dict[int, str] = {}
        self.order: list[int] = []

    def send(self, op: str, **params: Any) -> int:
        self.next_id += 1
        self.ops[self.next_id] = op
        self.write({"id": self.next_id, "op": op, **params})
        return self.next_id

    def write(self, message: Any) -> None:
        assert self.proc.stdin is not None
        line = message if isinstance(message, bytes) else json.dumps(message).encode() + b"\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def reply(self, rid: int) -> dict[str, Any]:
        while rid not in self.replies:
            self._read()
        return self.replies[rid]

    def __call__(self, op: str, **params: Any) -> dict[str, Any]:
        return self.reply(self.send(op, **params))

    def _read(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read().decode() if self.proc.stderr else "engine exited"
        message = json.loads(line)
        if "event" in message:
            check_event(message)
            self.events.append(message)
        elif message["id"] is None:
            schema("ErrorReply").validate(message)
            self.replies[0] = message
        else:
            check_reply(self.ops[message["id"]], message)
            self.replies[message["id"]] = message
            self.order.append(message["id"])
        return message

    def close(self) -> int:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()
        return self.proc.returncode


@pytest.fixture
def served(tmp_path):
    items = tmp_path / "items.json"
    write_items(items, {})
    started: list[Served] = []

    def start(config: Path | None = None) -> Served:
        server = Served(config or make_config(tmp_path), tmp_path / "state.db", {ITEMS_ENV: str(items)})
        started.append(server)
        return server

    start.items = items  # type: ignore[attr-defined]
    yield start
    for server in started:
        server.close()


def test_serve_speaks_the_contract(served, tmp_path):
    engine = served()

    hello = engine("hello", client={"name": "Youpdated", "version": "1.0.0", "platform": "macos"})
    assert hello["contract"] == 1 and hello["ops"] == list(api.OPS)
    assert engine("status")["counts"] == {"targets": 1, "unread": 0, "problems": 0}
    engine("check")
    write_items(served.items, {"alpha": [item("1.1"), item("1.2")]})
    checked = engine("check")
    assert checked["new"] == 2
    assert [e["done"] for e in engine.events] == [0, 1, 0, 1]
    assert engine.events[-1]["target"] == {"source": "fake", "key": "alpha", "display": "alpha"}
    assert engine("inbox.mark", read=True, all=True)["changed"] == 2
    assert engine("targets.list")["targets"][0]["key"] == "alpha"
    assert engine("snapshot.write", path=str(tmp_path / "snapshot.json"))["items"] == 0
    assert engine("shutdown") == {"id": engine.next_id, "ok": True}
    assert engine.proc.wait(10) == 0


def test_serve_answers_while_a_check_runs(served):
    write_items(served.items, {"alpha": {"sleep": 1.5, "items": [item("1.1")]}})
    engine = served()

    check = engine.send("check")
    busy = engine.send("check")
    listed = engine.send("inbox.list")

    assert engine.reply(busy)["error"]["code"] == "busy"
    assert engine.reply(listed)["ok"]
    assert engine.reply(check)["ok"]
    assert engine.order.index(listed) < engine.order.index(check)


def test_serve_survives_bad_lines_and_stops_when_stdin_closes(served):
    engine = served()
    engine.write(b"not json\n")
    engine._read()
    assert engine.replies[0]["error"]["code"] == "bad_request"
    engine.write({"op": "hello"})  # no id
    engine.replies.pop(0)
    engine._read()
    assert engine.replies[0]["error"]["code"] == "bad_request"
    assert engine("inbox.purge")["error"]["code"] == "unknown_op"

    assert engine.proc.stdin is not None
    engine.proc.stdin.close()
    assert engine.proc.wait(10) == 0


@encryption
def test_serve_unlocks_once(served, tmp_path):
    config = make_config(tmp_path)
    crypto.write_private(config, crypto.encrypt(config.read_bytes(), "pw"))
    engine = served(config)

    assert engine("status")["locked"]
    assert engine("check")["error"]["code"] == "locked"
    assert engine("unlock", passphrase="pw")["ok"]
    assert engine("check")["ok"]
    assert engine("status")["counts"] is not None


def test_one_op_from_the_command_line(tmp_path, fake, capsys):
    files = ["--config", str(make_config(tmp_path)), "--state", str(tmp_path / "state.db")]

    params = '{"settings": {"check_interval_hours": 3}}'
    assert main(["api", "settings.set", *files, "--json", "--params", params]) == 0
    reply = json.loads(capsys.readouterr().out)
    check_reply("settings.set", reply)
    assert reply["settings"]["check_interval_hours"] == 3

    assert main(["api", "inbox.list", *files, "--params", '{"limit": 0}']) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_request"
