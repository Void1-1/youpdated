"""`track_latest:` asks for the latest release where busier ones can push it off the list"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from youpdated.config import ConfigError, parse_config
from youpdated.runner import build_targets

from .conftest import fixture, get_source

RELEASES = "https://github.com/python/cpython/releases.atom"
LATEST = "https://github.com/python/cpython/releases/latest"
API = "https://api.github.com/repos/python/cpython/releases"
GL_API = "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli"
CB_API = "https://codeberg.org/api/v1/repos/forgejo/forgejo"
STEAM_FEED = "https://store.steampowered.com/feeds/news/app/440/"
STEAM_EVENTS = "https://store.steampowered.com/events/ajaxgetadjacentpartnerevents/"
# In steam_news.xml
LISTED_POST = "715661485108363783"


def _git(entry, *, latest=True):
    (target,) = get_source("git").targets([entry])
    target.track_latest = latest
    return target


def _uids(updates):
    return [u.uid for u in updates]


# config


def test_track_latest_is_off_unless_set():
    config = parse_config({"sources": {"npm": ["x"]}})
    assert config.track_latest is False
    (target,), _ = build_targets(config)
    assert target.track_latest is False


def test_track_latest_reaches_every_target():
    config = parse_config({"sources": {"npm": ["x"], "git": ["a/b"]}, "track_latest": True})
    targets, _ = build_targets(config)
    assert [t.track_latest for t in targets] == [True, True]


@pytest.mark.parametrize("value", ["yes", 1, None, ["git"]])
def test_track_latest_must_be_a_boolean(value):
    with pytest.raises(ConfigError, match="`track_latest` must be true or false"):
        parse_config({"sources": {"npm": ["x"]}, "track_latest": value})


# redirects


@respx.mock
def test_a_redirect_can_be_read_instead_of_followed(client):
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(302, headers={"Location": "https://example.com/b"})
    )
    page = respx.get("https://example.com/b").mock(return_value=httpx.Response(200, text="b"))

    fetched = client.get("https://example.com/a", follow_redirects=False, soft_statuses=(302,))

    assert fetched is not None and fetched.status == 302
    assert fetched.headers["location"] == "https://example.com/b"
    assert not page.called
    assert client.get("https://example.com/a").text == "b"


# GitHub without a token: where /releases/latest redirects


def _atom(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )


@respx.mock
def test_github_adds_the_latest_release_when_the_feed_has_pushed_it_out(client, monkeypatch):
    _atom(monkeypatch)
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://github.com/python/cpython/releases/tag/v3.9.25"}
        )
    )
    updates = list(get_source("git").fetch(_git("python/cpython"), client))

    assert len(updates) == 11
    latest = updates[-1]
    assert latest.uid == "release:v3.9.25" and latest.version == "v3.9.25"
    assert latest.url == "https://github.com/python/cpython/releases/tag/v3.9.25"
    assert latest.tags == ("release",) and latest.published is None


@respx.mock
def test_github_latest_already_listed_is_not_added_twice(client, monkeypatch):
    _atom(monkeypatch)
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://github.com/python/cpython/releases/tag/v3.14.7"}
        )
    )
    updates = list(get_source("git").fetch(_git("python/cpython"), client))

    assert len(updates) == 10
    assert _uids(updates).count("release:v3.14.7") == 1


@respx.mock
def test_github_without_releases_adds_nothing(client, monkeypatch):
    _atom(monkeypatch)
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://github.com/python/cpython/releases"}
        )
    )
    assert len(list(get_source("git").fetch(_git("python/cpython"), client))) == 10


@respx.mock
def test_without_track_latest_nothing_more_is_asked(client, monkeypatch):
    _atom(monkeypatch)
    latest = respx.get(LATEST)
    list(get_source("git").fetch(_git("python/cpython", latest=False), client))
    assert not latest.called


@respx.mock
def test_tags_and_commits_have_no_latest_to_ask_for(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get("https://github.com/python/cpython/tags.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    latest = respx.get(LATEST)
    list(get_source("git").fetch(_git({"repo": "python/cpython", "watch": ["tags"]}), client))
    assert not latest.called


# GitHub with a token: the API


def _release(tag, **extra):
    return {
        "id": 1,
        "tag_name": tag,
        "name": tag,
        "html_url": f"https://github.com/python/cpython/releases/tag/{tag}",
        "published_at": "2026-10-01T00:00:00Z",
        **extra,
    }


@respx.mock
def test_github_api_adds_the_latest_release(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    respx.get(f"{API}?per_page=20").mock(
        return_value=httpx.Response(200, json=[_release("v3.16.0a1", prerelease=True)])
    )
    latest = respx.get(f"{API}/latest").mock(
        return_value=httpx.Response(200, json=_release("v3.15.4"))
    )
    updates = list(get_source("git").fetch(_git("python/cpython"), client))

    assert _uids(updates) == ["release:v3.16.0a1", "release:v3.15.4"]
    assert updates[1].published == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert latest.calls[0].request.headers["Authorization"] == "Bearer t"


@respx.mock
def test_github_api_without_releases_adds_nothing(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    respx.get(f"{API}?per_page=20").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/latest").mock(return_value=httpx.Response(404, json={}))
    assert list(get_source("git").fetch(_git("python/cpython"), client)) == []


# GitLab and Forgejo


@respx.mock
def test_gitlab_follows_the_latest_permalink(client):
    respx.get(f"{GL_API}/releases?per_page=20").mock(
        return_value=httpx.Response(200, content=fixture("gitlab_releases.json"))
    )
    respx.get(f"{GL_API}/releases/permalink/latest").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://gitlab.com/api/v4/projects/1/releases/v1.90.0"}
        )
    )
    respx.get("https://gitlab.com/api/v4/projects/1/releases/v1.90.0").mock(
        return_value=httpx.Response(
            200, json={"tag_name": "v1.90.0", "released_at": "2026-01-01T00:00:00Z"}
        )
    )
    updates = list(get_source("git").fetch(_git("https://gitlab.com/gitlab-org/cli"), client))

    assert _uids(updates)[-1] == "release:v1.90.0"
    assert updates[-1].url == "https://gitlab.com/gitlab-org/cli/-/releases/v1.90.0"


@respx.mock
def test_forgejo_adds_the_latest_release_once(client):
    respx.get(f"{CB_API}/releases?limit=20").mock(
        return_value=httpx.Response(200, content=fixture("forgejo_releases.json"))
    )
    latest = respx.get(f"{CB_API}/releases/latest").mock(
        return_value=httpx.Response(200, json={"tag_name": "v14.0.2"})
    )
    target = _git("codeberg.org/forgejo/forgejo")
    assert _uids(get_source("git").fetch(target, client))[-1] == "release:v14.0.2"

    latest.mock(return_value=httpx.Response(200, json={"tag_name": "v17.0.0"}))
    assert _uids(get_source("git").fetch(target, client)).count("release:v17.0.0") == 1


@respx.mock
def test_forgejo_without_releases_adds_nothing(client):
    respx.get(f"{CB_API}/releases?limit=20").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{CB_API}/releases/latest").mock(return_value=httpx.Response(404, json={}))
    assert list(get_source("git").fetch(_git("codeberg.org/forgejo/forgejo"), client)) == []


# Steam: the newest post marked as an update


def _steam(client, events, latest=True):
    respx.get(url__startswith="https://store.steampowered.com/api/appdetails").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(url__startswith=STEAM_FEED).mock(
        return_value=httpx.Response(200, content=fixture("steam_news.xml"))
    )
    route = respx.get(url__startswith=STEAM_EVENTS).mock(
        return_value=httpx.Response(200, json={"success": 1, "events": events})
    )
    source = get_source("steam")
    (target,) = source.targets([440])
    target.track_latest = latest
    return list(source.fetch(target, client)), route


@respx.mock
def test_steam_adds_the_latest_update_post_when_the_feed_has_pushed_it_out(client):
    event = {
        "gid": "1",
        "event_name": "Team Fortress 2 Update Released",
        "announcement_body": {"posttime": 1792179520},
    }
    updates, route = _steam(client, [event])

    assert "event_type_filter=12,13,14" in str(route.calls[0].request.url)
    latest = updates[-1]
    assert latest.uid == latest.url == "https://store.steampowered.com/news/app/440/view/1"
    assert latest.title == "Team Fortress 2 Update Released" and latest.tags == ("news",)
    assert latest.published == datetime.fromtimestamp(1792179520, timezone.utc)


@respx.mock
def test_steam_latest_already_in_the_feed_is_not_added_twice(client):
    updates, _ = _steam(client, [{"gid": LISTED_POST, "event_name": "x"}])
    assert len(updates) == 10


@pytest.mark.parametrize("events", [[], [None], [{"event_name": "no gid"}]])
@respx.mock
def test_steam_without_an_update_post_adds_nothing(client, events):
    updates, _ = _steam(client, events)
    assert len(updates) == 10


@respx.mock
def test_steam_without_track_latest_asks_for_no_events(client):
    _, route = _steam(client, [], latest=False)
    assert not route.called
