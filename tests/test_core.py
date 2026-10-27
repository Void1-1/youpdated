"""Config, state/dedupe, HTTP behavior, and rendering"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import respx

from youpdated.config import ConfigError, PrivacyConfig, find_config, parse_config
from youpdated.http import Client, FetchError, retry_after_seconds
from youpdated.models import Update
from youpdated.render import json_out, rss_out
from youpdated.runner import parse_since, run
from youpdated.state import State

from .conftest import fixture


def make_update(uid: str = "1", **kw) -> Update:
    base: dict[str, Any] = dict(
        source="git",
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


@pytest.fixture
def config_places(tmp_path, monkeypatch):
    """Separate working, tool, and per-user folders, each without a config yet."""
    places = {name: tmp_path / name for name in ("cwd", "tool", "user")}
    for folder in places.values():
        folder.mkdir()
    monkeypatch.chdir(places["cwd"])
    monkeypatch.setattr("youpdated.config.tool_dir", lambda: places["tool"])
    monkeypatch.setattr(
        "youpdated.config.default_config_path", lambda: places["user"] / "config.yaml"
    )
    return places


def test_config_beside_the_tool_is_found_from_elsewhere(config_places):
    (config_places["tool"] / "youpdated.yml").write_text("sources: {}\n")
    (config_places["user"] / "config.yaml").write_text("sources: {}\n")
    assert find_config() == config_places["tool"] / "youpdated.yml"


def test_config_in_the_working_directory_wins_over_the_tool_folder(config_places):
    (config_places["cwd"] / "youpdated.yaml").write_text("sources: {}\n")
    (config_places["tool"] / "youpdated.yaml").write_text("sources: {}\n")
    assert find_config() == config_places["cwd"] / "youpdated.yaml"


def test_per_user_config_is_the_fallback(config_places):
    assert find_config() is None
    (config_places["user"] / "config.yaml").write_text("sources: {}\n")
    assert find_config() == config_places["user"] / "config.yaml"


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

# redirects: httpx strips only Authorization off-host, everything else a caller passes needs to be dropped by youpdated

@respx.mock
def test_caller_headers_do_not_follow_a_redirect_to_another_host(client):
    respx.get("https://e.com/api").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.net/api"})
    )
    landed = respx.get("https://elsewhere.net/api").mock(return_value=httpx.Response(200))

    fetched = client.get(
        "https://e.com/api",
        headers={"PRIVATE-TOKEN": "glpat-secret", "Authorization": "Bearer t", "Accept": "a/b"},
    )

    sent = landed.calls.last.request.headers
    assert fetched.url == "https://elsewhere.net/api"
    assert "PRIVATE-TOKEN" not in sent and "Authorization" not in sent
    assert sent["Accept"] == "a/b" and sent["User-Agent"]


@respx.mock
def test_caller_headers_follow_a_redirect_on_the_same_origin(client):
    respx.get("http://e.com/old").mock(
        return_value=httpx.Response(301, headers={"Location": "https://e.com/new"})
    )
    landed = respx.get("https://e.com/new").mock(return_value=httpx.Response(200))

    client.get("http://e.com/old", headers={"PRIVATE-TOKEN": "glpat-secret"})

    assert landed.calls.last.request.headers["PRIVATE-TOKEN"] == "glpat-secret"


@respx.mock
def test_a_header_dropped_off_host_stays_dropped_on_the_way_back(client):
    respx.get("https://e.com/a").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.net/b"})
    )
    respx.get("https://elsewhere.net/b").mock(
        return_value=httpx.Response(302, headers={"Location": "https://e.com/c"})
    )
    back = respx.get("https://e.com/c").mock(return_value=httpx.Response(200))

    client.get("https://e.com/a", headers={"PRIVATE-TOKEN": "glpat-secret"})

    assert "PRIVATE-TOKEN" not in back.calls.last.request.headers


@respx.mock
def test_webhook_headers_do_not_follow_a_redirect_to_another_host(client):
    respx.post("https://hooks.e.com/x").mock(
        return_value=httpx.Response(307, headers={"Location": "https://elsewhere.net/x"})
    )
    landed = respx.post("https://elsewhere.net/x").mock(return_value=httpx.Response(204))

    client.post("https://hooks.e.com/x", json={"a": 1}, headers={"X-Gotify-Key": "secret"})

    assert "X-Gotify-Key" not in landed.calls.last.request.headers
    assert json.loads(landed.calls.last.request.content) == {"a": 1}


@respx.mock
def test_a_redirect_loop_gives_up(client):
    route = respx.get("https://e.com/loop").mock(
        return_value=httpx.Response(302, headers={"Location": "https://e.com/loop"})
    )
    with pytest.raises(FetchError, match="TooManyRedirects"):
        client.get("https://e.com/loop", retries=0)
    assert route.call_count == 21


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


_BRAVE_API = "https://api.github.com/repos/brave/brave-browser/releases?per_page=100"


def _brave_release(name: str) -> dict:
    return {"name": name, "tag_name": name.split()[-1], "html_url": "https://e.com",
            "published_at": "2026-09-01T00:00:00Z"}


def _brave(*channels: str):
    return parse_config(
        {"sources": {"browser": [{"browser": "brave", "channel": c} for c in channels]}}
    )


@respx.mock
def test_a_target_added_onto_a_shared_unchanged_document_still_records_a_baseline(
    state, client
):
    """A new Brave channel shares the releases document with a channel already
    watched. That document's validator would get it a 304 and nothing to record,
    so its next change reported every release the new channel had."""
    releases = [_brave_release(n) for n in ("Release v1.0", "Beta v1.1", "Beta v1.2")]
    route = respx.get(_BRAVE_API).mock(
        side_effect=lambda request: (
            httpx.Response(304) if request.headers.get("If-None-Match") == '"a"'
            else httpx.Response(200, json=releases, headers={"ETag": '"a"'})
        )
    )
    run(_brave("stable"), state, client)

    before = route.call_count
    added = run(_brave("stable", "beta"), state, client)
    assert [t.key for t in added.baselined] == ["brave/beta"]
    # The channels fetch on separate workers, so the beta request is not always last
    assert any("If-None-Match" not in c.request.headers for c in route.calls[before:])

    route.side_effect = None
    route.return_value = httpx.Response(
        200, json=[_brave_release("Beta v1.3"), *releases], headers={"ETag": '"b"'}
    )
    later = run(_brave("stable", "beta"), state, client)
    assert [u.title for u in later.updates] == ["Beta v1.3"]


def _links(state) -> set[tuple[str, str, str]]:
    return {tuple(r) for r in state._conn.execute("SELECT source, target, url FROM http_cache_targets")}


@respx.mock
def test_each_target_is_linked_to_the_urls_it_asked_for(state, client):
    respx.get(_BRAVE_API).mock(
        side_effect=lambda request: (
            httpx.Response(304) if request.headers.get("If-None-Match") == '"a"'
            else httpx.Response(200, json=[_brave_release("Release v1.0")], headers={"ETag": '"a"'})
        )
    )
    both = {("browser", "brave/stable", _BRAVE_API), ("browser", "brave/beta", _BRAVE_API)}
    run(_brave("stable", "beta"), state, client)
    assert _links(state) == both

    state._conn.execute("DELETE FROM http_cache_targets")
    run(_brave("stable", "beta"), state, client)
    assert _links(state) == both


@respx.mock
def test_a_run_that_does_not_save_links_nothing(state, client):
    respx.get(_BRAVE_API).mock(
        return_value=httpx.Response(200, json=[], headers={"ETag": '"a"'})
    )
    run(_brave("stable"), state, client, save=False)
    assert _links(state) == set()


@respx.mock
def test_unconditional_sends_no_validators_but_still_stores_them(state, client):
    state.remember_validators("https://e.com/feed", '"v1"', None)
    route = respx.get("https://e.com/feed").mock(
        return_value=httpx.Response(200, content=b"x", headers={"ETag": '"v2"'})
    )
    with client.unconditional():
        client.get("https://e.com/feed", conditional=True)
    client.get("https://e.com/feed", conditional=True)

    assert "If-None-Match" not in route.calls[0].request.headers
    assert route.calls[1].request.headers["If-None-Match"] == '"v2"'


_RSS = '<?xml version="1.0"?><rss version="2.0"><channel>{}</channel></rss>'


def _rss_items(*days: int) -> str:
    return _RSS.format(
        "".join(
            f"<item><title>post {d}</title><guid>id-{d}</guid>"
            f"<pubDate>{d:02d} Jan 2026 00:00:00 GMT</pubDate></item>"
            for d in days
        )
    )


def _etag_feed(route, body: str, etag: str) -> None:
    """Serve ``body`` under ``etag``, and 304 to a request that already has it."""
    route.side_effect = lambda request: (
        httpx.Response(304)
        if request.headers.get("If-None-Match") == etag
        else httpx.Response(200, text=body, headers={"ETag": etag})
    )


@respx.mock
def test_a_run_that_does_not_save_stores_no_validators(state, client):
    """`--no-save` previews must not turn the next real run's fetch into a 304"""
    config = parse_config({"sources": {"feed": ["https://e.com/feed"]}})
    route = respx.get("https://e.com/feed")
    _etag_feed(route, _rss_items(1), '"v1"')
    run(config, state, client)

    _etag_feed(route, _rss_items(1, 2), '"v2"')
    preview = run(config, state, client, save=False)
    assert [u.title for u in preview.updates] == ["post 2"]
    assert state.conditional_headers("https://e.com/feed")["If-None-Match"] == '"v1"'

    real = run(config, state, client)
    assert [u.title for u in real.updates] == ["post 2"]
    assert state.conditional_headers("https://e.com/feed")["If-None-Match"] == '"v2"'


@respx.mock
def test_a_failed_target_stores_no_validators_for_its_other_urls(state, client):
    """A target's items are all dropped when any of its fetches fails, so are its validators."""
    config = parse_config(
        {"sources": {"github": [{"repo": "o/r", "watch": ["releases", "commits"]}]}}
    )
    releases = respx.get("https://github.com/o/r/releases.atom")
    commits = respx.get("https://github.com/o/r/commits.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_commits.atom"))
    )
    _etag_feed(releases, _rss_items(1), '"v1"')
    run(config, state, client)

    _etag_feed(releases, _rss_items(1, 2), '"v2"')
    commits.mock(return_value=httpx.Response(500))
    failed = run(config, state, client)
    assert failed.errors and not failed.updates

    commits.mock(return_value=httpx.Response(200, content=fixture("github_commits.atom")))
    recovered = run(config, state, client)
    assert [u.title for u in recovered.updates] == ["post 2"]


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
    assert "v1.2.3" in entries[0].findtext("a:summary", default="", namespaces=ns)


def test_rss_entry_ids_are_stable_across_renders():
    from xml.etree import ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}

    def ids():
        tree = ET.fromstring(rss_out.render(_result_with([make_update("1")])))
        return [e.findtext("a:id", namespaces=ns) for e in tree.findall("a:entry", ns)]

    assert ids() == ids()


# remote text


def test_remote_text_loses_control_characters_and_lone_surrogates():
    update = make_update(
        title="\x1b]8;;https://evil.example\x07click\x1b]8;;\x07 me\x0b\x9b2J",
        body="line one\nline\ttwo\r\x00",
        url="https://e.com/\x1b[2J",
        version="v1\ud800",
    )
    assert update.title == "]8;;https://evil.exampleclick]8;; me2J"
    assert update.body == "line one\nline\ttwo"
    assert update.url == "https://e.com/[2J"
    assert update.version == "v1"


def test_a_non_string_title_becomes_text():
    assert make_update(title=12345).title == "12345"


def test_a_lone_surrogate_in_a_uid_can_still_be_recorded(state):
    update = make_update("version:\ud800")
    state.mark_seen([update])
    assert not state.is_new(update)
    # alias is looked up too, which would raise before it was cleaned
    assert state.is_new(make_update("other", aliases=("tag:\ud800",)))
    assert not state.is_new(make_update("other", aliases=("version:\ud800",)))


def test_xml_noncharacters_go_and_a_surrogate_uid_keeps_a_marker():
    assert make_update(title=f"a{chr(0xFFFE)}b{chr(0xFFFF)}c").title == "abc"
    assert make_update(f"id{chr(0xD800)}").uid == f"id{chr(0xFFFD)}"


def test_labels_and_error_messages_are_cleaned_too():
    from youpdated.models import RunError, Target

    assert Target(source="feed", key="k", label="Blog\x1b[31m").display == "Blog[31m"
    error = RunError(source="feed", target="t\x1b", message="boom\x9b")
    assert (error.target, error.message) == ("t", "boom")


def test_rss_stays_valid_xml_with_control_characters_and_surrogates():
    from xml.etree import ElementTree as ET

    result = _result_with([make_update("1\ud800", title="a\x0bb\x08c\ud800")])
    tree = ET.fromstring(rss_out.render(result).encode("utf-8"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert tree.findtext("a:entry/a:title", default="", namespaces=ns).endswith("abc")


def test_json_output_encodes_as_utf8_with_a_lone_surrogate():
    json_out.render(_result_with([make_update(title="x\ud800")])).encode("utf-8")


def test_markup_in_labels_and_errors_is_printed_literally():
    from rich.console import Console

    from youpdated.models import RunError, Target
    from youpdated.render import terminal
    from youpdated.runner import RunResult

    result = RunResult(
        updates=[make_update("1", title="[bold]x[/]")],
        targets=[Target(source="git", key="a/b", label="[/oops]")],
        errors=[RunError(source="feed", target="[/b]", message="bad [/i] reply")],
    )
    console = Console(width=200, record=True)
    terminal.render(result, console=console)
    text = console.export_text()
    assert "[/oops]" in text and "[bold]x[/]" in text and "[/b]: bad [/i] reply" in text


# limits and odd input


@pytest.mark.parametrize("raw", ["3000y", "999999999999d", "101y"])
def test_durations_past_a_century_are_refused(raw):
    with pytest.raises(ValueError, match="longer than 100y"):
        parse_since(raw)
    with pytest.raises(ConfigError, match=r"\(100y max\)"):
        parse_config({"sources": {"npm": ["e"]}, "expiry": raw})


def test_a_century_is_still_a_duration():
    assert parse_since("100y") == timedelta(days=36500)
    State(None, expiry=parse_since("100y")).prune()


def test_a_config_that_is_not_utf8_is_a_config_error(tmp_path):
    from youpdated.config import load_config

    path = tmp_path / "youpdated.yaml"
    path.write_bytes("sources:\n  feed: [https://e.com/caf\xe9.xml]\n".encode("latin-1"))
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_config(path)


@pytest.mark.parametrize("command", [[True], ["notify-send", ""], ["notify-send", False]])
def test_list_commands_refuse_booleans_and_empty_strings(command):
    from youpdated.config import split_command

    with pytest.raises(ConfigError):
        split_command(command)


# cookies and webhooks


@respx.mock
def test_cookies_are_never_kept_between_requests(client):
    route = respx.get("https://e.com/a").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "id=1; Path=/"})
    )
    client.get("https://e.com/a")
    client.get("https://e.com/a")
    assert "cookie" not in route.calls.last.request.headers


@respx.mock
def test_a_cookie_set_on_a_redirect_is_not_sent_to_the_next_hop(client):
    respx.get("https://e.com/a").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://e.com/b", "Set-Cookie": "id=1; Path=/"}
        )
    )
    landed = respx.get("https://e.com/b").mock(return_value=httpx.Response(200))
    client.get("https://e.com/a")
    assert "cookie" not in landed.calls.last.request.headers


@respx.mock
@pytest.mark.parametrize(
    "first",
    [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("gone"), httpx.Response(500),
     httpx.Response(502), httpx.Response(504)],
)
def test_a_webhook_that_may_have_been_delivered_is_not_sent_again(client, first):
    route = respx.post("https://hooks.e.com/x").mock(side_effect=[first, httpx.Response(200)])
    with pytest.raises(FetchError):
        client.post("https://hooks.e.com/x", json={})
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    "first", [httpx.ConnectError("refused"), httpx.Response(429), httpx.Response(503)]
)
def test_a_webhook_the_server_never_took_is_retried(client, first):
    route = respx.post("https://hooks.e.com/x").mock(side_effect=[first, httpx.Response(204)])
    assert client.post("https://hooks.e.com/x", json={}).status == 204
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize("status", [301, 302, 303])
def test_a_redirect_that_would_drop_a_webhooks_body_is_a_failure(client, status):
    respx.post("https://hooks.e.com/x").mock(
        return_value=httpx.Response(status, headers={"Location": "https://hooks.e.com/y"})
    )
    page = respx.get("https://hooks.e.com/y").mock(return_value=httpx.Response(200))
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        client.post("https://hooks.e.com/x", json={"a": 1})
    assert not page.called


# state file


def test_a_second_state_on_the_same_file_waits_for_the_first(tmp_path):
    from youpdated.state import StateError

    path = tmp_path / "state.sqlite3"
    with State(path):
        with pytest.raises(StateError, match="in use by another youpdated run"):
            State(path, lock_timeout=0.2)
    with State(path, lock_timeout=0.2):
        pass


def test_a_state_file_that_is_not_a_database_is_a_clean_error(tmp_path, capsys):
    from youpdated.cli import main
    from youpdated.state import StateError

    path = tmp_path / "state.sqlite3"
    path.write_text("not a database, just some text that is long enough " * 20)
    with pytest.raises(StateError, match="not a youpdated history database"):
        State(path)
    # The failed open let go of its lock
    with pytest.raises(StateError, match="not a youpdated history database"):
        State(path, lock_timeout=0.2)

    config = tmp_path / "youpdated.yaml"
    config.write_text("sources:\n  npm: [express]\n")
    assert main(["check", "--test", "-c", str(config), "--state", str(path)]) == 1
    assert "History error" in capsys.readouterr().out


def test_forget_clears_shared_namespaces_whole(state):
    state.cache_set("youtube_invidious", "list", "https://inv.example")
    state.cache_set("youtube_channel_ids", "@kept", "UC")
    state.forget("youtube", ["@gone"], ("youtube_channel_ids",), shared=("youtube_invidious",))
    assert state.cache_get("youtube_invidious", "list") is None
    assert state.cache_get("youtube_channel_ids", "@kept") == "UC"


# runner


@respx.mock
def test_one_bad_entry_does_not_drop_the_rest_of_its_source(state, client):
    config = parse_config({"sources": {"npm": [{"package": ".nope"}, "express"]}})
    _npm("express", "5.0.0")

    result = run(config, state, client, show_all=True)

    assert [t.key for t in result.targets] == ["express"]
    assert [e.source for e in result.errors] == ["npm"]


@respx.mock
def test_new_items_left_out_by_since_stay_new(state, client):
    config = parse_config({"sources": {"npm": ["express"]}})
    now = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
        "name": "express", "dist-tags": {}, "versions": {},
        "time": {"4.0.0": (now - timedelta(days=90)).isoformat()},
    }
    route = respx.get("https://registry.npmjs.org/express")
    route.mock(return_value=httpx.Response(200, json=doc))
    run(config, state, client)  # baseline

    doc["time"] |= {
        "5.0.0": (now - timedelta(days=30)).isoformat(),
        "5.1.0": (now - timedelta(hours=1)).isoformat(),
    }
    route.mock(return_value=httpx.Response(200, json=doc))
    within = run(config, state, client, since=timedelta(days=7))
    assert [u.version for u in within.updates] == ["5.1.0"]
    assert [u.version for u in run(config, state, client).updates] == ["5.0.0"]


@respx.mock
def test_a_repo_listed_twice_with_different_rules_is_not_refetched_whole(
    state, client, monkeypatch
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = parse_config({"sources": {
        "git": ["python/cpython"],
        "github": [{"repo": "python/cpython", "ignore": ["prerelease"]}],
    }})
    route = respx.get("https://github.com/python/cpython/releases.atom").mock(
        return_value=httpx.Response(
            200, content=fixture("github_releases.atom"), headers={"ETag": '"v1"'}
        )
    )
    run(config, state, client)
    route.mock(return_value=httpx.Response(304))
    before = len(route.calls)

    run(config, state, client)

    second = list(route.calls)[before:]
    assert second and all(call.request.headers.get("If-None-Match") == '"v1"' for call in second)


def test_ignore_keys_that_cover_nothing_are_warned_about():
    from youpdated.runner import ignore_warnings

    config = parse_config({
        "sources": {"git": ["python/cpython"], "npm": ["express"]},
        "ignore": {"*": ["x"], "npm": ["x"], "github": ["prerelease"], "pipy": ["x"]},
    })
    moved, typo = ignore_warnings(config)
    assert "`ignore.github`" in moved and "need `ignore.git`" in moved
    assert "`ignore.pipy` covers nothing" in typo


def test_a_test_run_reports_config_problems_but_not_unanswered_fetches(state):
    config = parse_config({"sources": {"youtube": ["@NASA"], "git": ["not a repo"]}})
    with Client(PrivacyConfig(jitter=(0.0, 0.0)), state, test=True) as dry:
        result = run(config, state, dry, save=False)
    # @NASA can't be resolved without a reply; only the bad entry is the config's problem
    assert [e.source for e in result.errors] == ["git"]


# check: report, then record


def _check_setup(tmp_path) -> list[str]:
    config = tmp_path / "youpdated.yaml"
    config.write_text("privacy:\n  jitter: [0, 0]\nsources:\n  npm: [express]\n")
    return ["check", "-c", str(config), "--state", str(tmp_path / "state.sqlite3")]


@respx.mock
def test_a_report_that_fails_to_render_leaves_its_updates_new(tmp_path, monkeypatch, capsys):
    from youpdated.cli import main
    from youpdated.render import terminal

    args = _check_setup(tmp_path)
    _npm("express", "5.0.0")
    assert main(args) == 0
    _npm("express", "5.0.0", "5.1.0")

    def broken(*a, **kw):
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(terminal, "render", broken)
    with pytest.raises(RuntimeError):
        main(args)
    monkeypatch.undo()

    capsys.readouterr()
    assert main([*args, "--json"]) == 0
    assert [u["version"] for u in json.loads(capsys.readouterr().out)["updates"]] == ["5.1.0"]


@respx.mock
def test_an_rss_file_that_cannot_be_written_leaves_its_updates_new(tmp_path, capsys):
    from youpdated.cli import main

    args = _check_setup(tmp_path)
    _npm("express", "5.0.0")
    assert main(args) == 0
    _npm("express", "5.0.0", "5.1.0")
    (tmp_path / "file").write_text("")

    assert main([*args, "--rss", str(tmp_path / "file" / "feed.xml")]) == 1
    assert "cannot write" in capsys.readouterr().out

    assert main([*args, "--json"]) == 0
    assert [u["version"] for u in json.loads(capsys.readouterr().out)["updates"]] == ["5.1.0"]


def test_check_test_shows_config_problems_and_fails_on_them(tmp_path, capsys):
    from youpdated.cli import main

    config = tmp_path / "youpdated.yaml"
    config.write_text("sources:\n  git: [not a repo, python/cpython]\n  nope: [x]\n")
    args = ["check", "--test", "-c", str(config), "--state", str(tmp_path / "s.sqlite3")]

    assert main(args) == 0
    assert "problems" in capsys.readouterr().out
    assert main([*args, "--fail-on-error"]) == 2
    capsys.readouterr()
    assert main([*args, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert {e["source"] for e in report["errors"]} == {"git", "nope"}
