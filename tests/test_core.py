"""Config, state/dedupe, HTTP behavior, and rendering"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from youpdated.config import ConfigError, parse_config
from youpdated.http import Client, FetchError, retry_after_seconds
from youpdated.models import Update
from youpdated.render import json_out, rss_out
from youpdated.runner import parse_since, run
from youpdated.state import State

from .conftest import fixture


def make_update(uid: str = "1", **kw) -> Update:
    base = dict(
        source="github",
        target="a/b",
        uid=uid,
        title=f"item {uid}",
        url="https://example.com/1",
        published=datetime.now(timezone.utc),
    )
    base.update(kw)
    return Update(**base)


# config


def test_minimal_config_parses():
    config = parse_config({"sources": {"npm": ["express"]}})
    assert config.sources == {"npm": ["express"]}
    assert config.privacy.user_agent == "rotate"


def test_privacy_settings_parse():
    config = parse_config(
        {
            "privacy": {"proxy": "socks5://127.0.0.1:9050", "jitter": [1, 2], "concurrency": 8},
            "sources": {"npm": ["express"]},
        }
    )
    assert config.privacy.proxy == "socks5://127.0.0.1:9050"
    assert config.privacy.jitter == (1.0, 2.0)
    assert config.privacy.concurrency == 8


@pytest.mark.parametrize(
    "raw",
    [
        [],                                             # not a mapping
        {},                                             # no sources
        {"sources": {}},                                # empty sources
        {"sources": {"npm": "express"}},                # not a list
        {"sources": {"npm": ["e"]}, "privacy": {"proxy": "localhost:9050"}},
        {"sources": {"npm": ["e"]}, "privacy": {"jitter": [5, 1]}},
        {"sources": {"npm": ["e"]}, "privacy": {"concurrency": 0}},
    ],
)
def test_invalid_configs_are_rejected(raw):
    with pytest.raises(ConfigError):
        parse_config(raw)


@pytest.mark.parametrize(
    "text,expected",
    [("30m", timedelta(minutes=30)), ("12h", timedelta(hours=12)), ("7d", timedelta(days=7))],
)
def test_parse_since(text, expected):
    assert parse_since(text) == expected


def test_parse_since_rejects_garbage():
    with pytest.raises(ValueError):
        parse_since("soon")


# state


def test_seen_items_are_not_new_twice(state):
    update = make_update()
    assert state.is_new(update)
    state.mark_seen([update])
    assert not state.is_new(update)


def test_dedupe_is_scoped_per_target(state):
    first = make_update(uid="x", target="a/b")
    second = make_update(uid="x", target="c/d")
    state.mark_seen([first])
    assert state.is_new(second)


def test_conditional_headers_round_trip(state):
    state.remember_validators("https://e.com/f", '"abc"', "Mon, 01 Jan 2024 00:00:00 GMT")
    headers = state.conditional_headers("https://e.com/f")
    assert headers["If-None-Match"] == '"abc"'
    assert headers["If-Modified-Since"].startswith("Mon,")


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "state.sqlite3"
    update = make_update()
    with State(path) as first:
        first.mark_seen([update])
    with State(path) as second:
        assert not second.is_new(update)


# http


@respx.mock
def test_304_returns_none_and_conditional_headers_are_sent(client, state):
    state.remember_validators("https://e.com/feed", '"tag"', None)
    route = respx.get("https://e.com/feed").mock(return_value=httpx.Response(304))

    assert client.get("https://e.com/feed", conditional=True) is None
    assert route.calls[0].request.headers["If-None-Match"] == '"tag"'


@respx.mock
def test_validators_are_stored_after_a_200(client, state):
    respx.get("https://e.com/feed").mock(
        return_value=httpx.Response(200, content=b"x", headers={"ETag": '"v2"'})
    )
    client.get("https://e.com/feed", conditional=True)
    assert state.conditional_headers("https://e.com/feed")["If-None-Match"] == '"v2"'


@respx.mock
def test_targets_sharing_a_url_fetch_it_once_and_all_get_the_body(state, client):
    """Every Brave channel is one feed, every Firefox channel one JSON.

    Without a run scope the first target stores a validator and the rest are
    answered 304 against it, so they report nothing at all.
    """
    route = respx.get("https://e.com/shared.json").mock(
        return_value=httpx.Response(200, content=b'{"v": 1}', headers={"ETag": '"v1"'})
    )
    with client.run_scope():
        bodies = [client.get("https://e.com/shared.json", conditional=True) for _ in range(3)]

    assert route.call_count == 1
    assert all(b is not None and b.content == b'{"v": 1}' for b in bodies)


@respx.mock
def test_a_304_across_runs_still_means_nothing_new(state, client):
    """Outside a run, and on a fresh run, 304 keeps its normal meaning."""
    respx.get("https://e.com/feed").mock(return_value=httpx.Response(304))
    state.remember_validators("https://e.com/feed", '"v1"', None)
    with client.run_scope():
        assert client.get("https://e.com/feed", conditional=True) is None


@respx.mock
def test_bodies_are_not_reused_outside_a_run_scope(state, client):
    """A client reused for a later run must not serve the earlier run's body."""
    route = respx.get("https://e.com/shared.json").mock(
        side_effect=[
            httpx.Response(200, content=b'{"v": 1}'),
            httpx.Response(200, content=b'{"v": 2}'),
        ]
    )
    with client.run_scope():
        first = client.get("https://e.com/shared.json", conditional=True)
    with client.run_scope():
        second = client.get("https://e.com/shared.json", conditional=True)

    assert route.call_count == 2
    assert first.content == b'{"v": 1}' and second.content == b'{"v": 2}'


@respx.mock
def test_use_conditional_false_skips_validators(state):
    state.remember_validators("https://e.com/feed", '"tag"', None)
    route = respx.get("https://e.com/feed").mock(return_value=httpx.Response(200, content=b"x"))
    with Client(state=state, use_conditional=False) as client:
        client.get("https://e.com/feed", conditional=True)
    assert "If-None-Match" not in route.calls[0].request.headers


@respx.mock
def test_unexpected_status_raises_but_soft_status_does_not(client):
    respx.get("https://e.com/a").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/b").mock(return_value=httpx.Response(404, content=b"nope"))

    with pytest.raises(FetchError):
        client.get("https://e.com/a")
    assert client.get("https://e.com/b", soft_statuses=(404,)).status == 404


@pytest.mark.parametrize(
    "value, expected",
    [
        ("120", 120.0),
        (" 5 ", 5.0),
        ("Wed, 07 Oct 2026 12:00:30 GMT", 30.0),
        ("Wed, 07 Oct 2026 11:00:00 GMT", 0.0),  # past
        (None, None),
        ("", None),
        ("-5", None),
        ("soon", None),
    ],
)
def test_retry_after_parses_seconds_and_dates(value, expected):
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert retry_after_seconds(value, now=now) == expected


def _record_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return sleeps


@respx.mock
def test_a_429_waits_as_long_as_retry_after_asks(client, monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    route = respx.get("https://e.com/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, content=b"ok"),
        ]
    )

    assert client.get("https://e.com/x").content == b"ok"
    assert route.call_count == 2
    assert sleeps == [pytest.approx(7, abs=0.5)]


def test_a_held_host_delays_every_request_to_it_but_no_other(client, monkeypatch):
    """Other threads hitting a rate-limited host wait too, not only the one told."""
    sleeps = _record_sleeps(monkeypatch)

    client._hold_host("e.com", 7)
    client._pace("e.com")
    client._pace("other.com")
    assert sleeps == [pytest.approx(7, abs=0.5)]


@respx.mock
def test_a_retry_after_past_the_limit_fails_at_once(client, monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    route = respx.get("https://e.com/x").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"})
    )

    with pytest.raises(FetchError, match="retry after 3600s"):
        client.get("https://e.com/x", retries=2)
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_a_429_without_retry_after_falls_back_to_backoff(client, monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    client.retry_backoff = 1.5
    respx.get("https://e.com/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(429), httpx.Response(200)]
    )

    client.get("https://e.com/x", retries=2)
    assert sleeps == [1.5, 3.0]


@respx.mock
def test_rotating_user_agent_is_a_real_browser_string(client):
    route = respx.get("https://e.com/x").mock(return_value=httpx.Response(200, content=b""))
    client.get("https://e.com/x")
    assert route.calls[0].request.headers["User-Agent"].startswith("Mozilla/5.0")


@respx.mock
def test_test_mode_sends_nothing_but_records_the_url(state):
    route = respx.get("https://e.com/x").mock(return_value=httpx.Response(200))
    with Client(state=state, test=True) as client:
        assert client.get("https://e.com/x") is None
    assert not route.called
    assert client.requested_urls == ["https://e.com/x"]


# runner


@respx.mock
def test_first_run_records_a_baseline_then_reports_only_new(state, client):
    config = parse_config({"sources": {"npm": ["express"]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "express",
                "dist-tags": {"latest": "5.0.0"},
                "time": {"5.0.0": "2024-01-01T00:00:00.000Z"},
                "versions": {"5.0.0": {"description": "d"}},
            },
        )
    )

    first = run(config, state, client)
    assert first.baseline and first.updates == [] and first.total_fetched == 1

    second = run(config, state, client)
    assert not second.baseline and second.updates == []

    # A newly published version shows up on the next run
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "express",
                "dist-tags": {"latest": "5.1.0"},
                "time": {
                    "5.0.0": "2024-01-01T00:00:00.000Z",
                    "5.1.0": "2024-02-01T00:00:00.000Z",
                },
                "versions": {"5.1.0": {"description": "d"}},
            },
        )
    )
    third = run(config, state, client)
    assert [u.version for u in third.updates] == ["5.1.0"]


def _npm(name: str, *versions: str, status: int = 200):
    """Mock the registry document for ``name`` publishing ``versions``"""
    respx.get(f"https://registry.npmjs.org/{name}").mock(
        return_value=httpx.Response(
            status,
            json={
                "name": name,
                "dist-tags": {"latest": versions[-1]} if versions else {},
                "time": {v: f"2024-01-{i + 1:02d}T00:00:00.000Z" for i, v in enumerate(versions)},
                "versions": {v: {} for v in versions},
            },
        )
    )


@respx.mock
def test_a_target_added_later_records_its_own_baseline(state, client):
    _npm("express", "5.0.0")
    run(parse_config({"sources": {"npm": ["express"]}}), state, client)

    # A second target joins the config, and express publishes something new
    _npm("express", "5.0.0", "5.1.0")
    _npm("react", "18.0.0", "19.0.0")
    result = run(parse_config({"sources": {"npm": ["express", "react"]}}), state, client)

    assert not result.baseline
    assert [u.version for u in result.updates] == ["5.1.0"]
    assert [t.key for t in result.baselined] == ["react"]

    _npm("react", "18.0.0", "19.0.0", "19.1.0")
    later = run(parse_config({"sources": {"npm": ["express", "react"]}}), state, client)
    assert [u.version for u in later.updates] == ["19.1.0"]
    assert later.baselined == []


@respx.mock
def test_a_target_that_failed_its_first_run_is_baselined_once_it_works(state, client):
    config = parse_config({"sources": {"npm": ["express", "react"]}})
    _npm("express", "5.0.0")
    _npm("react", status=500)
    first = run(config, state, client)
    assert first.baseline and len(first.errors) == 1

    _npm("react", "18.0.0", "19.0.0")
    second = run(config, state, client)
    assert second.updates == []
    assert [t.key for t in second.baselined] == ["react"]


@respx.mock
def test_a_first_run_limited_to_one_source_leaves_the_rest_unbaselined(state, client):
    config = parse_config({"sources": {"npm": ["express"], "itch": ["https://u.itch.io/g"]}})
    _npm("express", "5.0.0")
    run(config, state, client, only_sources=["npm"])

    respx.get("https://u.itch.io/g/devlog.rss").mock(
        return_value=httpx.Response(200, content=fixture("itch_devlog.rss"))
    )
    respx.get("https://u.itch.io/g").mock(return_value=httpx.Response(404))
    result = run(config, state, client)
    assert result.updates == []
    assert [t.source for t in result.baselined] == ["itch"]


@respx.mock
def test_a_target_whose_first_fetch_was_empty_reports_its_first_item(state, client):
    config = parse_config({"sources": {"npm": ["express"]}})
    _npm("express")
    assert run(config, state, client).baseline

    _npm("express", "5.0.0")
    assert [u.version for u in run(config, state, client).updates] == ["5.0.0"]


@respx.mock
def test_state_from_before_per_target_baselines_is_not_baselined_again(state, client):
    """Older state has seen rows and a last run, but no per target markers"""
    state.mark_seen([make_update("version:5.0.0", source="npm", target="express")])
    state.set_last_run()

    _npm("express", "5.0.0", "5.1.0")
    result = run(parse_config({"sources": {"npm": ["express"]}}), state, client)
    assert result.baselined == []
    assert [u.version for u in result.updates] == ["5.1.0"]


@respx.mock
def test_show_all_reports_a_new_target_instead_of_baselining_it(state, client):
    _npm("express", "5.0.0", "5.1.0")
    result = run(parse_config({"sources": {"npm": ["express"]}}), state, client, show_all=True)
    assert not result.baseline and result.baselined == []
    assert len(result.updates) == 2


@respx.mock
def test_one_failing_source_does_not_sink_the_run(state, client):
    config = parse_config({"sources": {"npm": ["express"], "itch": ["https://u.itch.io/g"]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "express",
                "dist-tags": {"latest": "5.0.0"},
                "time": {"5.0.0": "2024-01-01T00:00:00.000Z"},
                "versions": {},
            },
        )
    )
    respx.get("https://u.itch.io/g/devlog.rss").mock(return_value=httpx.Response(500))

    result = run(config, state, client, show_all=True)
    assert [u.version for u in result.updates] == ["5.0.0"]
    assert len(result.errors) == 1 and result.errors[0].source == "itch"


def test_unknown_source_is_reported_not_raised(state, client):
    config = parse_config({"sources": {"nope": ["x"]}})
    result = run(config, state, client)
    assert result.errors and "unknown source" in result.errors[0].message


@respx.mock
def test_since_filters_out_older_items(state, client):
    config = parse_config({"sources": {"npm": ["express"]}})
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "express",
                "dist-tags": {"latest": "5.1.0"},
                "time": {"5.0.0": old, "5.1.0": new},
                "versions": {},
            },
        )
    )
    result = run(config, state, client, show_all=True, since=timedelta(days=7))
    assert [u.version for u in result.updates] == ["5.1.0"]


@respx.mock
def test_no_save_leaves_state_untouched(state, client):
    config = parse_config({"sources": {"npm": ["express"]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "express",
                "dist-tags": {"latest": "5.0.0"},
                "time": {"5.0.0": "2024-01-01T00:00:00.000Z"},
                "versions": {},
            },
        )
    )
    run(config, state, client, save=False)
    assert state.seen_count() == 0
    assert state.last_run() is None


# renderers


def _result_with(updates):
    from youpdated.runner import RunResult

    return RunResult(updates=list(updates))


def test_json_output_is_parseable_and_complete():
    result = _result_with([make_update("1"), make_update("2")])
    payload = json.loads(json_out.render(result))
    assert payload["counts"]["new"] == 2
    assert {u["uid"] for u in payload["updates"]} == {"1", "2"}
    assert payload["generated"]


def test_output_names_targets_baselined_mid_run(capsys):
    from rich.console import Console

    from youpdated.models import Target
    from youpdated.render import terminal

    result = _result_with([make_update("1")])
    result.targets = [Target(source="github", key="a/b"), Target(source="npm", key="react")]
    result.baselined = [result.targets[1]]

    terminal.render(result, console=Console(width=200))
    assert "Baseline recorded for 1 new target(s): npm:react" in capsys.readouterr().out

    payload = json.loads(json_out.render(result))
    assert payload["baselined"] == [{"source": "npm", "key": "react"}]


def test_rss_output_is_valid_atom():
    from xml.etree import ElementTree as ET

    result = _result_with([make_update("1", version="v1.2.3")])
    tree = ET.fromstring(rss_out.render(result))
    ns = {"a": "http://www.w3.org/2005/Atom"}

    assert tree.tag == "{http://www.w3.org/2005/Atom}feed"
    entries = tree.findall("a:entry", ns)
    assert len(entries) == 1
    # Every Atom entry needs id, title, and updated.
    for required in ("a:id", "a:title", "a:updated"):
        assert entries[0].find(required, ns) is not None
    assert "v1.2.3" in entries[0].find("a:summary", ns).text


def test_rss_entry_ids_are_stable_across_renders():
    from xml.etree import ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}

    def ids():
        tree = ET.fromstring(rss_out.render(_result_with([make_update("1")])))
        return [e.find("a:id", ns).text for e in tree.findall("a:entry", ns)]

    assert ids() == ids()
