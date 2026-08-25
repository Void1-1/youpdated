"""Parser tests: real payloads, no network"""

from __future__ import annotations

import httpx
import pytest
import respx

from youpdated.registry import get_source
from youpdated.sources.base import ConfigEntryError

from .conftest import fixture


# config normalization


@pytest.mark.parametrize(
    "source,entries,expected_keys",
    [
        ("github", ["python/cpython"], ["python/cpython"]),
        ("github", ["https://github.com/astral-sh/uv"], ["astral-sh/uv"]),
        ("github", [{"repo": "a/b", "watch": ["releases", "commits"]}], ["a/b"]),
        ("npm", ["express", "@types/node"], ["express", "@types/node"]),
        ("steam", [440, "730"], ["440", "730"]),
        ("steam", ["https://store.steampowered.com/app/620/Portal_2/"], ["620"]),
        ("itch", ["https://user.itch.io/my-game"], ["user/my-game"]),
        ("youtube", ["@NASA"], ["@NASA"]),
        ("youtube", ["UCLA_DiR1FfKNvjuUpBHmylQ"], ["UCLA_DiR1FfKNvjuUpBHmylQ"]),
        ("youtube", [{"playlist": "PLabcdefghijk"}], ["playlist:PLabcdefghijk"]),
    ],
)
def test_scalar_and_mapping_entries_normalize(source, entries, expected_keys):
    targets = get_source(source).targets(entries)
    assert [t.key for t in targets] == expected_keys


@pytest.mark.parametrize(
    "source,entries",
    [
        ("github", ["not a repo"]),
        ("github", [{"repo": "a/b", "watch": ["nonsense"]}]),
        ("steam", ["no-digits-here"]),
        ("itch", ["https://example.com/game"]),
        ("youtube", ["just-a-string"]),
    ],
)
def test_bad_entries_are_rejected(source, entries):
    with pytest.raises(ConfigEntryError):
        get_source(source).targets(entries)


# parsers


@respx.mock
def test_github_releases(client):
    respx.get("https://github.com/python/cpython/releases.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    source = get_source("github")
    (target,) = source.targets(["python/cpython"])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.source == "github" and u.target == "python/cpython" for u in updates)
    assert all("release" in u.tags for u in updates)
    # The tag is recovered from the atom entry id
    assert any(u.version and u.version.startswith("v3.") for u in updates)
    assert all(u.url.startswith("https://github.com/") for u in updates)
    # newest first
    dated = [u.published for u in updates if u.published]
    assert dated == sorted(dated, reverse=True)


@respx.mock
def test_github_commits_have_no_version(client):
    respx.get("https://github.com/astral-sh/uv/commits.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_commits.atom"))
    )
    source = get_source("github")
    (target,) = source.targets([{"repo": "astral-sh/uv", "watch": ["commits"]}])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.version is None and "commit" in u.tags for u in updates)


@respx.mock
def test_npm_versions(client):
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, content=fixture("npm_express.json"))
    )
    source = get_source("npm")
    (target,) = source.targets(["express"])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.version for u in updates)
    assert all(u.uid == f"version:{u.version}" for u in updates)
    assert sum("latest" in u.tags for u in updates) == 1


@respx.mock
def test_npm_scoped_package_is_url_encoded(client):
    route = respx.get("https://registry.npmjs.org/@types%2Fnode").mock(
        return_value=httpx.Response(200, content=fixture("npm_express.json"))
    )
    source = get_source("npm")
    (target,) = source.targets(["@types/node"])
    list(source.fetch(target, client))
    assert route.called


@respx.mock
def test_steam_news_resolves_app_name(client):
    respx.get(url__startswith="https://store.steampowered.com/api/appdetails").mock(
        return_value=httpx.Response(
            200, json={"440": {"success": True, "data": {"name": "Team Fortress 2"}}}
        )
    )
    respx.get(url__startswith="https://store.steampowered.com/feeds/news/app/440/").mock(
        return_value=httpx.Response(200, content=fixture("steam_news.xml"))
    )
    source = get_source("steam")
    (target,) = source.targets([440])
    updates = list(source.fetch(target, client))

    assert target.label == "Team Fortress 2"
    assert updates and all("news" in u.tags for u in updates)


@respx.mock
def test_steam_app_name_is_cached_after_first_lookup(client, state):
    details = respx.get(url__startswith="https://store.steampowered.com/api/appdetails").mock(
        return_value=httpx.Response(
            200, json={"440": {"success": True, "data": {"name": "Team Fortress 2"}}}
        )
    )
    respx.get(url__startswith="https://store.steampowered.com/feeds/news/app/440/").mock(
        return_value=httpx.Response(200, content=fixture("steam_news.xml"))
    )
    source = get_source("steam")
    for _ in range(2):
        (target,) = source.targets([440])
        list(source.fetch(target, client))

    assert details.call_count == 1


@respx.mock
def test_itch_devlog(client):
    respx.get("https://user.itch.io/my-game/devlog.rss").mock(
        return_value=httpx.Response(200, content=fixture("itch_devlog.rss"))
    )
    source = get_source("itch")
    (target,) = source.targets([{"url": "https://user.itch.io/my-game", "watch": ["devlog"]}])
    updates = list(source.fetch(target, client))

    assert updates and all("devlog" in u.tags for u in updates)


@respx.mock
def test_itch_404_means_no_devlog_not_an_error(client):
    respx.get("https://user.itch.io/my-game/devlog.rss").mock(
        return_value=httpx.Response(404, text="<html>not found</html>")
    )
    source = get_source("itch")
    (target,) = source.targets([{"url": "https://user.itch.io/my-game", "watch": ["devlog"]}])

    assert list(source.fetch(target, client)) == []


@respx.mock
def test_youtube_uses_official_feed_when_it_works(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    source = get_source("youtube")
    (target,) = source.targets(["UCLA_DiR1FfKNvjuUpBHmylQ"])
    updates = list(source.fetch(target, client))

    assert updates
    # uids are normalized to the video id so the fallback paths dedupe
    assert all(u.uid.startswith("yt:video:") for u in updates)
    # ...and the label comes from the feed, as it does on the fallback paths
    assert target.label == "NASA"


@respx.mock
def test_youtube_falls_back_to_invidious_when_feed_is_blocked(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(404, text="blocked")
    )
    respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(
            200,
            json=[["inv.example", {"type": "https", "api": True, "uri": "https://inv.example"}]],
        )
    )
    respx.get(
        "https://inv.example/api/v1/channels/UCLA_DiR1FfKNvjuUpBHmylQ/latest"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "videoId": "abcdefghijk",
                    "title": "A video",
                    "published": 1_700_000_000,
                    "author": "NASA",
                }
            ],
        )
    )
    source = get_source("youtube")
    (target,) = source.targets(["UCLA_DiR1FfKNvjuUpBHmylQ"])
    updates = list(source.fetch(target, client))

    assert [u.uid for u in updates] == ["yt:video:abcdefghijk"]
    assert updates[0].url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert target.label == "NASA"


@respx.mock
def test_youtube_resolves_handle_to_channel_id(client, state):
    respx.get("https://www.youtube.com/@NASA").mock(
        return_value=httpx.Response(
            200, text='...,"externalId":"UCLA_DiR1FfKNvjuUpBHmylQ",...'
        )
    )
    feed = respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    source = get_source("youtube")
    (target,) = source.targets(["@NASA"])
    list(source.fetch(target, client))

    assert "channel_id=UCLA_DiR1FfKNvjuUpBHmylQ" in str(feed.calls[0].request.url)
    assert state.cache_get("youtube_channel_ids", "@NASA") == "UCLA_DiR1FfKNvjuUpBHmylQ"


@respx.mock
def test_youtube_reports_clearly_when_every_path_fails(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(403, text="blocked")
    )
    respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(200, json=[])
    )
    source = get_source("youtube")
    (target,) = source.targets(["UCLA_DiR1FfKNvjuUpBHmylQ"])

    with pytest.raises(Exception, match="every YouTube path failed"):
        list(source.fetch(target, client))
