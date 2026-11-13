"""`versions: {match:}` keeps only versions a pattern fits"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from youpdated.config import Config, ConfigError, PrivacyConfig, parse_config
from youpdated.runner import build_targets, run
from youpdated.sources import git
from youpdated.versions import VersionRules

from .conftest import get_source

REPO = "acme/mono"
FEED = f"https://github.com/{REPO}/releases.atom"
LATEST = f"https://github.com/{REPO}/releases/latest"
API = f"https://api.github.com/repos/{REPO}/releases"
GL_API = "https://gitlab.com/api/v4/projects/acme%2Fmono"
CB_API = "https://codeberg.org/api/v1/repos/acme/mono"
MORE = {"Link": '<https://example.com/?page=2>; rel="next"'}

DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _feed(tags: list[str]) -> bytes:
    """A releases feed listing ``tags`` newest first"""
    entries = "".join(
        f"<entry><id>tag:github.com,2008:Repository/1/{tag}</id><title>{tag}</title>"
        f'<link rel="alternate" href="https://github.com/{REPO}/releases/tag/{tag}"/>'
        f"<updated>{(DAY - timedelta(days=age)).isoformat()}</updated></entry>"
        for age, tag in enumerate(tags)
    )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


def _engine(first: int, count: int = git.ATOM_PAGE) -> list[str]:
    """``count`` engine tags counting down from ``v1.<first>.0``"""
    return [f"v1.{n}.0" for n in range(first, first - count, -1)]


def _config(entry, **top) -> Config:
    config = parse_config({"sources": {"git": [entry]}, **top})
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    return config


def _target(match, repo=REPO, **entry):
    config = _config({"repo": repo, "versions": {"match": match}, **entry})
    (target,), errors = build_targets(config)
    assert not errors
    return target


def _versions(updates) -> list[str | None]:
    return [u.version for u in updates]


@pytest.fixture(autouse=True)
def _no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)


@pytest.mark.parametrize(
    "match,version,taken",
    [
        (("app-v*",), "app-v1.2.0", True),
        (("app-v*",), "v1.2.0", False),
        (("v3.12.*",), "v3.12.4", True),
        (("v3.12.*",), "v3.13.0", False),
        (("*-alpine",), "3.20-alpine", True),
        (("*-alpine",), "3.20", False),
        (("llvmorg-*", "swift-*"), "swift-6.0.3-RELEASE", True),
        # the whole version
        (("3.12.*",), "v3.12.4", False),
        (("V*",), "v1.0.0", False),
        (("nightly",), "nightly", True),
        (("v?.0.0",), "v2.0.0", True),
    ],
)
def test_a_version_is_taken_when_a_pattern_fits_it(match, version, taken):
    assert VersionRules(match=match).matches(version) is taken


def test_an_update_without_a_version_is_always_taken():
    assert VersionRules(match=("app-v*",)).matches(None)


def test_without_match_every_version_is_taken():
    assert VersionRules().matches("anything")


# config


@pytest.mark.parametrize(
    "value,globs",
    [("app-v*", ("app-v*",)), (["v3.12.*", " *-lts "], ("v3.12.*", "*-lts"))],
)
def test_match_is_a_pattern_or_a_list_of_them(value, globs):
    assert _target(value).versions.match == globs


@pytest.mark.parametrize("value", [3.12, 3, True, "", "  ", [], ["v*", 3.12], {"a": "b"}])
def test_match_must_be_text(value):
    with pytest.raises(ConfigError, match=r"`versions\.git\.match` must be a pattern in quotes"):
        parse_config({"sources": {"git": ["a/b"]}, "versions": {"git": {"match": value}}})


def test_a_bad_match_on_an_entry_fails_only_that_entry():
    config = _config({"repo": "a/b", "versions": {"match": 3.12}})
    targets, errors = build_targets(config)
    assert targets == [] and "`versions.match` must be a pattern" in errors[0].message


def test_an_entry_replaces_the_match_of_its_source():
    config = parse_config({
        "sources": {"git": ["a/b", {"repo": "c/d", "versions": {"match": "*"}}]},
        "versions": {"git": {"match": "v2.*", "min": 2}},
    })
    (first, second), _ = build_targets(config)
    assert first.versions == VersionRules(min=(2,), match=("v2.*",))
    assert second.versions == VersionRules(min=(2,), match=("*",))


# checks


@respx.mock
def test_only_matching_versions_are_recorded_and_reported(state, client):
    route = respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(["v1.5.0", "app-v1.0.0", "v1.4.0"]))
    )
    config = _config({"repo": REPO, "versions": {"match": "app-v*"}})

    first = run(config, state, client)
    assert first.baseline and _versions(first.baseline_items) == ["app-v1.0.0"]
    assert first.ignored == 2 and state.seen_count() == 1

    route.mock(
        return_value=httpx.Response(
            200, content=_feed(["app-v1.1.0", "v1.6.0", "v1.5.0", "app-v1.0.0", "v1.4.0"])
        )
    )
    assert _versions(run(config, state, client).updates) == ["app-v1.1.0"]
    assert state.seen_count() == 2


@respx.mock
def test_loosening_match_reports_what_it_dropped(state, client):
    """Like `ignore:`, and unlike `bump:` and `min:`"""
    respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(["v1.5.0", "app-v1.0.0"]))
    )
    run(_config({"repo": REPO, "versions": {"match": "app-v*"}}), state, client)

    assert _versions(run(_config(REPO), state, client).updates) == ["v1.5.0"]


@respx.mock
def test_a_changed_match_reads_the_whole_list_again(state, client):
    route = respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(304)
        if "If-None-Match" in request.headers
        else httpx.Response(200, content=_feed(["v1.5.0", "app-v1.0.0"]), headers={"ETag": '"a"'})
    )
    run(_config({"repo": REPO, "versions": {"match": "app-v*"}}), state, client)
    run(_config({"repo": REPO, "versions": {"match": "app-v*"}}), state, client)
    assert "If-None-Match" in route.calls[-1].request.headers

    changed = run(_config({"repo": REPO, "versions": {"match": "v*"}}), state, client)
    assert "If-None-Match" not in route.calls[-1].request.headers
    assert _versions(changed.updates) == ["v1.5.0"]


@respx.mock
def test_a_bump_is_measured_against_matching_versions_only(state, client):
    """Engine v1.5.0 isn't the app's highest version, so app-v1.1.0 is still a minor bump"""
    route = respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(["v1.5.0", "app-v1.0.0"]))
    )
    config = _config({"repo": REPO, "versions": {"match": "app-v*", "bump": "minor"}})
    run(config, state, client)
    assert state.highest_version("git", f"github.com/{REPO}") == "1|1"

    route.mock(
        return_value=httpx.Response(
            200, content=_feed(["app-v1.1.0", "v1.6.0", "v1.5.0", "app-v1.0.0"])
        )
    )
    assert _versions(run(config, state, client).updates) == ["app-v1.1.0"]


@respx.mock
def test_two_entries_of_one_repo_each_keep_their_own(state, client):
    route = respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(["v1.5.0", "app-v1.0.0", "docs-1"]))
    )
    config = parse_config({
        "sources": {
            "git": [
                {"repo": REPO, "versions": {"match": "app-v*"}},
                {"repo": REPO, "versions": {"match": "v*"}},
            ]
        }
    })
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    run(config, state, client)
    assert state.seen_count() == 2

    route.mock(
        return_value=httpx.Response(
            200, content=_feed(["docs-2", "app-v1.1.0", "v1.6.0", "v1.5.0", "app-v1.0.0"])
        )
    )
    reported = run(config, state, client).updates
    assert sorted(u.version or "" for u in reported) == ["app-v1.1.0", "v1.6.0"]


# a list holding no match: GitHub's feed goes on from its last tag


@respx.mock
def test_github_reads_on_until_a_release_matches(client):
    respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200,
            content=_feed(
                {
                    None: _engine(30),
                    "v1.21.0": _engine(20),
                    "v1.11.0": ["v1.10.0", "app-v1.1.0", "v1.9.0", "app-v1.0.0", *_engine(8, 6)],
                }[request.url.params.get("after")]
            ),
        )
    )
    updates = list(get_source("git").fetch(_target("app-v*"), client))

    # the newest one only, after the list itself
    assert _versions(updates) == [*_engine(30), "app-v1.1.0"]
    assert updates[-1].uid == "release:app-v1.1.0"
    assert [call.request.url.params.get("after") for call in respx.calls] == [
        None, "v1.21.0", "v1.11.0",
    ]


@respx.mock
def test_github_reads_no_further_when_the_list_has_a_match(client):
    route = respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(["app-v1.0.0", *_engine(30, 9)]))
    )
    assert len(list(get_source("git").fetch(_target("app-v*"), client))) == 10
    assert route.call_count == 1


@respx.mock
def test_github_reads_no_further_without_match(client):
    route = respx.get(FEED).mock(return_value=httpx.Response(200, content=_feed(_engine(30))))
    (target,) = get_source("git").targets([REPO])
    assert len(list(get_source("git").fetch(target, client))) == 10
    assert route.call_count == 1


@respx.mock
def test_github_reads_no_further_when_the_list_is_every_release(client):
    route = respx.get(FEED).mock(return_value=httpx.Response(200, content=_feed(_engine(30, 4))))
    assert len(list(get_source("git").fetch(_target("app-v*"), client))) == 4
    assert route.call_count == 1


@respx.mock
def test_github_reads_no_further_when_the_list_is_unchanged(client, state):
    route = respx.get(FEED).mock(
        return_value=httpx.Response(200, content=_feed(_engine(30, 4)), headers={"ETag": '"a"'})
    )
    target = _target("app-v*")
    list(get_source("git").fetch(target, client))
    route.mock(return_value=httpx.Response(304))

    assert list(get_source("git").fetch(target, client)) == []
    assert route.call_count == 2


@respx.mock
def test_an_ignored_match_is_not_the_one_looked_for(client):
    """A beta on the list doesn't stand in for the release behind it"""
    respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200,
            content=_feed(
                ["app-v1.1.0-beta.1", *_engine(30, 9)]
                if "after" not in request.url.params
                else ["app-v1.0.0-rc.1", "app-v1.0.0"]
            ),
        )
    )
    target = _target("app-v*", ignore=["prerelease"])
    assert _versions(get_source("git").fetch(target, client))[-1] == "app-v1.0.0"


@respx.mock
def test_github_stops_at_the_last_page(client):
    route = respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200, content=_feed(_engine(30) if "after" not in request.url.params else _engine(20, 3))
        )
    )
    assert len(list(get_source("git").fetch(_target("app-v*"), client))) == 10
    assert route.call_count == 2


@respx.mock
def test_github_gives_up_after_a_few_pages(client):
    def page(request):
        after = request.url.params.get("after")
        first = 1000 if after is None else int(after.split(".")[1]) - 1
        return httpx.Response(200, content=_feed(_engine(first)))

    route = respx.get(FEED).mock(side_effect=page)
    assert len(list(get_source("git").fetch(_target("app-v*"), client))) == 10
    assert route.call_count == 1 + git.MATCH_PAGES


@respx.mock
def test_a_later_page_failing_fails_the_check(state, client):
    """So the list isn't answered `304` next time with the match still unread"""
    route = respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200, content=_feed(_engine(30)), headers={"ETag": '"a"'}
        )
        if "after" not in request.url.params
        else httpx.Response(404)
    )
    config = _config({"repo": REPO, "versions": {"match": "app-v*"}})

    assert len(run(config, state, client).errors) == 1
    run(config, state, client)
    firsts = [c.request for c in route.calls if "after" not in c.request.url.params]
    assert len(firsts) == 2 and "If-None-Match" not in firsts[-1].headers


@respx.mock
def test_track_latest_still_adds_the_latest_release(client):
    respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200,
            content=_feed(
                _engine(30) if "after" not in request.url.params else ["app-v1.0.0", "v1.20.0"]
            ),
        )
    )
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            302, headers={"Location": f"https://github.com/{REPO}/releases/tag/v1.30.0"}
        )
    )
    target = _target("app-v*")
    target.track_latest = True

    assert _versions(get_source("git").fetch(target, client)) == [*_engine(30), "app-v1.0.0"]


@respx.mock
def test_a_match_found_on_a_later_page_is_the_targets_latest(state, client):
    respx.get(FEED).mock(
        side_effect=lambda request: httpx.Response(
            200,
            content=_feed(
                _engine(30) if "after" not in request.url.params else ["app-v1.0.0", "v1.20.0"]
            ),
        )
    )
    result = run(_config({"repo": REPO, "versions": {"match": "app-v*"}}), state, client)

    assert _versions(result.baseline_items) == ["app-v1.0.0"]
    assert state.latest()[("git", f"github.com/{REPO}")]["version"] == "app-v1.0.0"


# forge APIs have numbered pages


def _release(tag: str) -> dict:
    return {
        "id": 1,
        "tag_name": tag,
        "name": tag,
        "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "published_at": "2026-10-01T00:00:00Z",
    }


def _pages(pages: dict[str | None, list[str]], headers=MORE):
    def answer(request):
        tags = pages[request.url.params.get("page")]
        last = request.url.params.get("page") == max(filter(None, pages))
        return httpx.Response(
            200, json=[_release(tag) for tag in tags], headers={} if last else headers
        )

    return answer


@respx.mock
def test_github_api_reads_the_next_pages(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    route = respx.get(API).mock(
        side_effect=_pages({
            None: _engine(60, 20), "2": _engine(40, 20), "3": ["v1.20.0", "app-v1.0.0"],
        })
    )
    updates = list(get_source("git").fetch(_target("app-v*"), client))

    assert _versions(updates) == [*_engine(60, 20), "app-v1.0.0"]
    assert [dict(call.request.url.params) for call in route.calls] == [
        {"per_page": "20"}, {"per_page": "20", "page": "2"}, {"per_page": "20", "page": "3"},
    ]
    assert all(call.request.headers["Authorization"] == "Bearer t" for call in route.calls)


@respx.mock
def test_gitlab_reads_the_next_pages(client):
    route = respx.get(f"{GL_API}/releases").mock(
        side_effect=_pages({None: _engine(60, 20), "2": ["v1.40.0", "app-v1.0.0"]})
    )
    updates = list(get_source("git").fetch(_target("app-v*", f"gitlab.com/{REPO}"), client))

    assert _versions(updates) == [*_engine(60, 20), "app-v1.0.0"]
    assert route.calls[-1].request.url.params["page"] == "2"


@respx.mock
def test_forgejo_reads_the_next_pages(client):
    route = respx.get(f"{CB_API}/releases").mock(
        side_effect=_pages({None: _engine(60, 20), "2": ["v1.40.0", "app-v1.0.0"]})
    )
    updates = list(get_source("git").fetch(_target("app-v*", f"codeberg.org/{REPO}"), client))

    assert _versions(updates) == [*_engine(60, 20), "app-v1.0.0"]
    assert dict(route.calls[-1].request.url.params) == {"limit": "20", "page": "2"}


@respx.mock
def test_an_api_list_without_more_pages_is_not_read_past(client):
    route = respx.get(f"{CB_API}/releases").mock(
        return_value=httpx.Response(200, json=[_release("v1.0.0")])
    )
    list(get_source("git").fetch(_target("app-v*", f"codeberg.org/{REPO}"), client))
    assert route.call_count == 1
