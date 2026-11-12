"""Parser tests: real payloads, no network"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from youpdated.config import parse_config
from youpdated.models import Update
from youpdated.runner import run
from youpdated.sources.base import ConfigEntryError

from .conftest import fixture, get_source


# config normalization


@pytest.mark.parametrize(
    "source,entries,expected_keys",
    [
        ("git", ["python/cpython"], ["github.com/python/cpython"]),
        ("git", ["https://github.com/astral-sh/uv"], ["github.com/astral-sh/uv"]),
        ("git", [{"repo": "a/b", "watch": ["releases", "commits"]}], ["github.com/a/b"]),
        ("github", ["python/cpython"], ["github.com/python/cpython"]),  # the old name
        ("npm", ["express", "@types/node"], ["express", "@types/node"]),
        ("steam", [440, "730"], ["440", "730"]),
        ("steam", ["https://store.steampowered.com/app/620/Portal_2/"], ["620"]),
        ("itch", ["https://user.itch.io/my-game"], ["user/my-game"]),
        ("youtube", ["@NASA"], ["@NASA"]),
        ("youtube", ["UCLA_DiR1FfKNvjuUpBHmylQ"], ["UCLA_DiR1FfKNvjuUpBHmylQ"]),
        ("youtube", [{"playlist": "PLabcdefghijk"}], ["playlist:PLabcdefghijk"]),
        (
            "youtube",
            [
                "https://www.youtube.com/playlist?list=PLabcdefghijk",
                "www.youtube.com/playlist?list=PLabcdefghijk&si=share",
                "https://m.youtube.com/playlist?si=x&list=PLabcdefghijk",
                {"playlist": "https://youtube.com/playlist?list=PLabcdefghijk"},
            ],
            ["playlist:PLabcdefghijk"] * 4,
        ),
    ],
)
def test_scalar_and_mapping_entries_normalize(source, entries, expected_keys):
    targets = get_source(source).targets(entries)
    assert [t.key for t in targets] == expected_keys


@pytest.mark.parametrize(
    "source,entries",
    [
        ("git", ["not a repo"]),
        ("git", [{"repo": "a/b", "watch": ["nonsense"]}]),
        ("steam", ["no-digits-here"]),
        ("itch", ["https://example.com/game"]),
        ("youtube", ["just-a-string"]),
        ("youtube", ["https://www.youtube.com/playlist?list=not-a-playlist"]),
        ("youtube", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]),
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
    source = get_source("git")
    (target,) = source.targets(["python/cpython"])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.source == "git" and u.target == "github.com/python/cpython" for u in updates)
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
    source = get_source("git")
    (target,) = source.targets([{"repo": "astral-sh/uv", "watch": ["commits"]}])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.version is None and "commit" in u.tags for u in updates)


_RELEASES_ATOM = "https://github.com/python/cpython/releases.atom"
_RELEASES_API = "https://api.github.com/repos/python/cpython/releases?per_page=20"
_CPYTHON_TAGS = re.findall(r"Repository/81598961/([^<]+)</id>", fixture("github_releases.atom").decode())


def _api_releases() -> list[dict]:
    """The REST payload for the same releases the atom fixture lists."""
    return [
        {
            "id": 1000 + n,
            "tag_name": tag,
            "name": tag,
            "html_url": f"https://github.com/python/cpython/releases/tag/{tag}",
            "published_at": "2026-08-12T13:57:54Z",
            "draft": False,
            "prerelease": False,
            "body": "",
        }
        for n, tag in enumerate(_CPYTHON_TAGS)
    ]


def _mock_both_release_paths() -> None:
    respx.get(_RELEASES_ATOM).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    respx.get(_RELEASES_API).mock(return_value=httpx.Response(200, json=_api_releases()))


@respx.mock
def test_github_release_uids_agree_with_and_without_a_token(client, monkeypatch):
    _mock_both_release_paths()
    source = get_source("git")
    (target,) = source.targets(["python/cpython"])

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    anonymous = {u.uid for u in source.fetch(target, client)}
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    authed = {u.uid for u in source.fetch(target, client)}

    assert anonymous == authed == {f"release:{tag}" for tag in _CPYTHON_TAGS}


@pytest.mark.parametrize("first_token", [True, False])
@respx.mock
def test_switching_github_token_does_not_rereport(state, client, monkeypatch, first_token):
    _mock_both_release_paths()
    config = parse_config({"sources": {"github": ["python/cpython"]}})

    for token in (first_token, not first_token):
        if token:
            monkeypatch.setenv("GITHUB_TOKEN", "t")
        else:
            monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = run(config, state, client)
        assert not result.updates


@pytest.mark.parametrize("token", [True, False])
@respx.mock
def test_github_history_from_older_uids_still_counts(state, client, monkeypatch, token):
    """State recorded before releases were keyed by tag is not reported again"""
    _mock_both_release_paths()
    if token:
        monkeypatch.setenv("GITHUB_TOKEN", "t")
    else:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    old_uids = [f"release:{1000 + n}" for n in range(len(_CPYTHON_TAGS))] if token else [
        f"tag:github.com,2008:Repository/81598961/{tag}" for tag in _CPYTHON_TAGS
    ]
    state.mark_seen(
        Update(source="git", target="github.com/python/cpython", uid=uid, title="", url="")
        for uid in old_uids
    )

    source = get_source("git")
    (target,) = source.targets(["python/cpython"])
    assert state.filter_new(source.fetch(target, client)) == []


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
def test_npm_latest_is_listed_when_newer_prereleases_fill_the_rest(client):
    times = {"created": "2026-01-01T00:00:00Z", "15.0.0": "2026-01-01T00:00:00Z"}
    times |= {f"15.1.0-canary.{n}": f"2026-01-{n + 2:02}T00:00:00Z" for n in range(12)}
    doc = {"dist-tags": {"latest": "15.0.0"}, "time": times, "versions": {}}
    respx.get("https://registry.npmjs.org/next").mock(return_value=httpx.Response(200, json=doc))
    source = get_source("npm")
    (target,) = source.targets(["next"])
    updates = list(source.fetch(target, client))

    assert len(updates) == 11
    assert updates[-1].version == "15.0.0"
    assert "latest" in updates[-1].tags


@respx.mock
def test_npm_lists_the_newest_finals_behind_a_run_of_canaries(client):
    times = {f"14.{n}.0": f"2026-01-{n + 1:02}T00:00:00Z" for n in range(12)}
    times |= {f"15.0.0-canary.{n}": f"2026-02-{n + 1:02}T00:00:00Z" for n in range(12)}
    doc = {"dist-tags": {"latest": "14.11.0"}, "time": times, "versions": {}}
    respx.get("https://registry.npmjs.org/next").mock(return_value=httpx.Response(200, json=doc))
    source = get_source("npm")
    (target,) = source.targets(["next"])
    versions = [u.version for u in source.fetch(target, client)]

    assert versions[:10] == [f"15.0.0-canary.{n}" for n in range(11, 1, -1)]
    assert versions[10:] == [f"14.{n}.0" for n in range(11, 1, -1)]


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
    # uids are normalized to the video id so the fallback paths dedupe, Shorts included
    assert all(re.fullmatch(r"yt:video:[A-Za-z0-9_-]{11}", u.uid) for u in updates)
    assert "yt:video:myZ9kn9MIWQ" in {u.uid for u in updates}
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


CHANNEL = "UCLA_DiR1FfKNvjuUpBHmylQ"
DATA_API = "https://www.googleapis.com/youtube/v3/playlistItems"


def _blocked_feed_and(instances: list | None = None) -> None:
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(404, text="blocked")
    )
    respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(
            200,
            json=[
                [uri, {"type": "https", "api": True, "uri": uri}] for uri in instances or []
            ],
        )
    )


def _video(vid: str = "abcdefghijk", **extra) -> dict:
    return {"videoId": vid, "title": "A video", "published": 1_700_000_000, **extra}


@respx.mock
def test_youtube_api_key_goes_in_a_header_and_is_never_shown(client, monkeypatch):
    from youpdated.http import FetchError

    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-secret")
    _blocked_feed_and()
    api = respx.get(url__startswith=DATA_API).mock(
        return_value=httpx.Response(403, json={"error": {"message": "quota"}})
    )
    (target,) = get_source("youtube").targets([CHANNEL])

    with pytest.raises(FetchError) as failed:
        list(get_source("youtube").fetch(target, client))

    sent = api.calls.last.request
    assert sent.headers["X-Goog-Api-Key"] == "AIza-secret"
    assert "AIza-secret" not in str(sent.url)
    assert "AIza-secret" not in str(failed.value)
    assert not any("AIza-secret" in url for url in client.requested_urls)


PLAYLIST = "https://www.youtube.com/playlist?list=PLabcdefghijk"
PLAYLIST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Select Lectures</title>
  <author><name>Lex Fridman</name></author>
  <entry>
    <id>yt:video:abcdefghijk</id>
    <title>A video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abcdefghijk"/>
    <author><name>Someone else</name></author>
    <published>2026-11-01T00:00:00+00:00</published>
  </entry>
</feed>"""


@respx.mock
def test_a_playlist_is_named_by_its_title_from_the_official_feed(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=PLAYLIST_FEED)
    )
    (target,) = get_source("youtube").targets([PLAYLIST])
    list(get_source("youtube").fetch(target, client))

    assert target.label == "Select Lectures"


@respx.mock
def test_a_playlist_is_named_by_its_title_from_invidious(client):
    _blocked_feed_and(["https://inv.example"])
    respx.get("https://inv.example/api/v1/playlists/PLabcdefghijk").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Select Lectures",
                "author": "Lex Fridman",
                "videos": [_video(author="Someone else")],
            },
        )
    )
    (target,) = get_source("youtube").targets([PLAYLIST])
    list(get_source("youtube").fetch(target, client))

    assert target.label == "Select Lectures"


@respx.mock
def test_a_playlist_is_named_by_its_title_from_the_data_api(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-secret")
    _blocked_feed_and()
    item = {
        "snippet": {
            "title": "A video",
            "channelTitle": "Lex Fridman",
            "resourceId": {"videoId": "abcdefghijk"},
        }
    }
    respx.get(url__startswith=DATA_API).mock(
        return_value=httpx.Response(200, json={"items": [item]})
    )
    named = respx.get(url__startswith="https://www.googleapis.com/youtube/v3/playlists?").mock(
        return_value=httpx.Response(200, json={"items": [{"snippet": {"title": "Select Lectures"}}]})
    )
    (target,) = get_source("youtube").targets([PLAYLIST])
    list(get_source("youtube").fetch(target, client))

    assert target.label == "Select Lectures"
    assert named.calls.last.request.headers["X-Goog-Api-Key"] == "AIza-secret"


@respx.mock
def test_a_playlist_without_a_title_from_the_data_api_still_has_its_videos(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-secret")
    _blocked_feed_and()
    item = {"snippet": {"title": "A video", "resourceId": {"videoId": "abcdefghijk"}}}
    respx.get(url__startswith=DATA_API).mock(
        return_value=httpx.Response(200, json={"items": [item]})
    )
    respx.get(url__startswith="https://www.googleapis.com/youtube/v3/playlists?").mock(
        return_value=httpx.Response(403, json={"error": {"message": "quota"}})
    )
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert [u.uid for u in get_source("youtube").fetch(target, client)] == ["yt:video:abcdefghijk"]
    assert target.display == "playlist:PLabcdefghijk"


@respx.mock
def test_a_playlist_name_in_config_is_kept_over_its_title(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=PLAYLIST_FEED)
    )
    (target,) = get_source("youtube").targets([{"playlist": PLAYLIST, "name": "Lectures"}])
    list(get_source("youtube").fetch(target, client))

    assert target.label == "Lectures"


# long playlists: new videos go at the end, past the first page


def _vid(n: int) -> str:
    return f"v{n:010d}"


def _uids(updates) -> list[str]:
    return [u.uid.removeprefix("yt:video:") for u in updates]


def _ends(count: int) -> list[str]:
    """What a playlist of ``count`` videos should be checked for: both ends"""
    return [_vid(n) for n in [*range(15), *range(max(15, count - 15), count)]]


@respx.mock
def test_a_full_official_feed_leaves_a_playlist_to_paths_that_reach_its_end(client):
    entries = "".join(
        f"<entry><id>yt:video:{_vid(n)}</id><title>{n}</title>"
        f'<link rel="alternate" href="https://www.youtube.com/watch?v={_vid(n)}"/></entry>'
        for n in range(15)
    )
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(
            200, content=f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()
        )
    )
    respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(
            200, json=[["i", {"type": "https", "api": True, "uri": "https://inv.example"}]]
        )
    )
    respx.get("https://inv.example/api/v1/playlists/PLabcdefghijk").mock(
        return_value=httpx.Response(
            200, json={"videoCount": 16, "videos": [_video(_vid(n)) for n in range(16)]}
        )
    )
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert _uids(get_source("youtube").fetch(target, client)) == _ends(16)


@respx.mock
def test_a_short_official_feed_is_the_whole_playlist(client):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=PLAYLIST_FEED)
    )
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert _uids(get_source("youtube").fetch(target, client)) == ["abcdefghijk"]


@respx.mock
def test_invidious_reads_the_last_page_of_a_long_playlist(client):
    _blocked_feed_and(["https://inv.example"])
    url = "https://inv.example/api/v1/playlists/PLabcdefghijk"
    last = respx.get(f"{url}?page=5").mock(
        return_value=httpx.Response(
            200, json={"videoCount": 448, "videos": [_video(_vid(n)) for n in range(350, 448)]}
        )
    )
    respx.get(url).mock(
        return_value=httpx.Response(
            200, json={"videoCount": 448, "videos": [_video(_vid(n)) for n in range(200)]}
        )
    )
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert _uids(get_source("youtube").fetch(target, client)) == _ends(448)
    assert last.called


@respx.mock
def test_invidious_moves_on_when_a_playlist_page_is_empty(client, monkeypatch):
    import youpdated.sources.youtube as youtube

    monkeypatch.setattr(youtube.random, "sample", lambda seq, k: list(seq)[:k])
    _blocked_feed_and(["https://bad.example", "https://good.example"])
    for base, tail in (("https://bad.example", []), ("https://good.example", [449])):
        url = f"{base}/api/v1/playlists/PLabcdefghijk"
        respx.get(f"{url}?page=5").mock(
            return_value=httpx.Response(200, json={"videos": [_video(_vid(n)) for n in tail]})
        )
        respx.get(url).mock(
            return_value=httpx.Response(
                200, json={"videoCount": 450, "videos": [_video(_vid(n)) for n in range(200)]}
            )
        )
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert _vid(449) in _uids(get_source("youtube").fetch(target, client))


@respx.mock
def test_invidious_keeps_a_channels_newest_videos(client):
    _blocked_feed_and(["https://inv.example"])
    respx.get(f"https://inv.example/api/v1/channels/{CHANNEL}/latest").mock(
        return_value=httpx.Response(200, json=[_video(_vid(n)) for n in range(30)])
    )
    (target,) = get_source("youtube").targets([CHANNEL])

    assert _uids(get_source("youtube").fetch(target, client)) == [_vid(n) for n in range(15)]


def _data_api_playlist(count: int, *, bad_tokens: tuple[str, ...] = ()):
    """A Data API serving a ``count``-video playlist 50 a page, with tokens ``T<offset>``"""

    def page(request):
        token = request.url.params.get("pageToken")
        if token in bad_tokens:
            return httpx.Response(400, json={"error": {"message": "invalid page token"}})
        offset = int(token[1:]) if token else 0
        items = [
            {"snippet": {"title": str(n), "resourceId": {"videoId": _vid(n)}}}
            for n in range(offset, min(offset + 50, count))
        ]
        body = {"items": items, "pageInfo": {"totalResults": count}}
        if offset + 50 < count:
            body["nextPageToken"] = f"T{offset + 50}"
        return httpx.Response(200, json=body)

    respx.get(url__startswith="https://www.googleapis.com/youtube/v3/playlists?").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    return respx.get(url__startswith=DATA_API).mock(side_effect=page)


def _tokens(route) -> list[str | None]:
    return [call.request.url.params.get("pageToken") for call in route.calls]


@respx.mock
def test_the_data_api_walks_to_a_long_playlists_end_once(client, state, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-secret")
    _blocked_feed_and()
    api = _data_api_playlist(448)
    (target,) = get_source("youtube").targets([PLAYLIST])

    assert _uids(get_source("youtube").fetch(target, client)) == _ends(448)
    assert _tokens(api) == [None, *(f"T{n}" for n in range(50, 448, 50))]

    before = len(api.calls)
    _data_api_playlist(452)
    assert _uids(get_source("youtube").fetch(target, client)) == _ends(452)
    assert _tokens(api)[before:] == [None, "T350", "T400", "T450"]


@respx.mock
def test_the_data_api_walks_from_the_start_when_its_saved_place_is_gone(
    client, state, monkeypatch
):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-secret")
    _blocked_feed_and()
    (target,) = get_source("youtube").targets([PLAYLIST])

    state.cache_set("youtube_playlist_pages", target.key, "T900") # the playlist got shorter
    _data_api_playlist(120)
    assert _uids(get_source("youtube").fetch(target, client)) == _ends(120)

    state.cache_set("youtube_playlist_pages", target.key, "Tbroken")
    _data_api_playlist(120, bad_tokens=("Tbroken",))
    assert _uids(get_source("youtube").fetch(target, client)) == _ends(120)
    assert state.cache_get("youtube_playlist_pages", target.key) == "T50"


@respx.mock
def test_youtube_uses_only_public_https_invidious_instances(client):
    _blocked_feed_and(
        [
            "http://plain.example",
            "https://127.0.0.1:9",
            "https://[::1]",
            "https://localhost",
            "https://other-port.example:8443",
            "https://user:pw@creds.example",
            "https://good.example/",
        ]
    )
    respx.get(f"https://good.example/api/v1/channels/{CHANNEL}/latest").mock(
        return_value=httpx.Response(200, json=[_video()])
    )
    (target,) = get_source("youtube").targets([CHANNEL])

    assert [u.uid for u in get_source("youtube").fetch(target, client)] == ["yt:video:abcdefghijk"]
    instance_requests = [u for u in client.requested_urls if "/api/v1/" in u]
    assert instance_requests == [f"https://good.example/api/v1/channels/{CHANNEL}/latest"]


@respx.mock
@pytest.mark.parametrize(
    "reply",
    [
        {"videos": [None]},
        [{"videoId": 5}],
        [{"videoId": "../../evil", "title": "x"}],
        {"videos": "nope"},
    ],
)
def test_a_malformed_invidious_reply_moves_on_to_the_next_instance(client, monkeypatch, reply):
    import youpdated.sources.youtube as youtube

    monkeypatch.setattr(youtube.random, "sample", lambda seq, k: list(seq)[:k])
    _blocked_feed_and(["https://odd.example", "https://good.example"])
    respx.get(f"https://odd.example/api/v1/channels/{CHANNEL}/latest").mock(
        return_value=httpx.Response(200, json=reply)
    )
    respx.get(f"https://good.example/api/v1/channels/{CHANNEL}/latest").mock(
        return_value=httpx.Response(200, json=[_video(published=10**20)])
    )
    (target,) = get_source("youtube").targets([CHANNEL])

    (update,) = get_source("youtube").fetch(target, client)
    assert update.uid == "yt:video:abcdefghijk"
    assert update.published is None  # out of range for a datetime


@respx.mock
def test_a_malformed_data_api_reply_does_not_stop_the_chain_raising_cleanly(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    _blocked_feed_and()
    respx.get(url__startswith=DATA_API).mock(return_value=httpx.Response(200, json=[1, 2]))
    (target,) = get_source("youtube").targets([CHANNEL])

    with pytest.raises(Exception, match="every YouTube path failed.*unexpected payload"):
        list(get_source("youtube").fetch(target, client))


@respx.mock
def test_youtube_tries_every_kept_instance_over_time(client, state):
    import youpdated.sources.youtube as youtube

    bases = [f"https://inv{n}.example" for n in range(youtube.KEPT_INSTANCES)]
    _blocked_feed_and(bases)
    tried: set[str] = set()
    for _ in range(40):
        tried.update(youtube.YouTubeSource()._instances(client))
    assert tried == set(bases)


@respx.mock
def test_youtube_refreshes_an_instance_list_a_day_old(client, state):
    from datetime import datetime, timedelta, timezone

    import youpdated.sources.youtube as youtube

    listing = respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(
            200, json=[["n", {"type": "https", "api": True, "uri": "https://new.example"}]]
        )
    )
    state.cache_set(youtube.INSTANCE_CACHE, "list", "https://old.example")
    state.cache_set(youtube.INSTANCE_CACHE, "fetched", datetime.now(timezone.utc).isoformat())
    assert youtube.YouTubeSource()._instances(client) == ["https://old.example"]
    assert not listing.called

    day_old = datetime.now(timezone.utc) - timedelta(days=1, minutes=1)
    state.cache_set(youtube.INSTANCE_CACHE, "fetched", day_old.isoformat())
    assert youtube.YouTubeSource()._instances(client) == ["https://new.example"]
    assert state.cache_get(youtube.INSTANCE_CACHE, "list") == "https://new.example"


@respx.mock
def test_a_web_page_at_a_feed_address_is_an_error_not_an_empty_feed(state, client):
    respx.get("https://e.com/feed.xml").mock(
        return_value=httpx.Response(200, text="<!doctype html><html><body>Moved</body></html>")
    )
    result = run(parse_config({"sources": {"feed": ["https://e.com/feed.xml"]}}), state, client)

    (error,) = result.errors
    assert "did not return an RSS or Atom feed" in error.message
    assert not state.is_baselined("feed", "https://e.com/feed.xml")


@respx.mock
def test_an_empty_but_real_feed_is_still_fine(state, client):
    respx.get("https://e.com/feed.xml").mock(
        return_value=httpx.Response(200, text='<rss version="2.0"><channel><title>t</title></channel></rss>')
    )
    result = run(parse_config({"sources": {"feed": ["https://e.com/feed.xml"]}}), state, client)
    assert result.ok and state.is_baselined("feed", "https://e.com/feed.xml")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2024-01-01T00:00:00.123+05:30", "2023-12-31T18:30:00.123000+00:00"),
        ("2026-08-13T21:38:40.667708123Z", "2026-08-13T21:38:40.667708+00:00"),
        ("2026-10-06T11:37:36.000+02:00", "2026-10-06T09:37:36+00:00"),
        ("2024-01-01T00:00:00", "2024-01-01T00:00:00+00:00"),
    ],
)
def test_parse_iso_keeps_the_zone_after_a_fraction(value, expected):
    from datetime import timezone

    from youpdated.sources.base import parse_iso

    parsed = parse_iso(value)
    assert parsed is not None
    assert parsed.astimezone(timezone.utc).isoformat() == expected


def test_a_plugin_that_fails_to_start_is_skipped_and_later_ones_still_load(monkeypatch):
    import importlib.metadata
    from types import SimpleNamespace

    from youpdated import registry

    class Broken:
        name = "broken"

        def __init__(self):
            raise RuntimeError("needs a setting it doesn't have")

    class Fine:
        name = "fine-plugin"
        summary = "loads"

    entries = [
        SimpleNamespace(name="broken", load=lambda: Broken),
        SimpleNamespace(name="fine", load=lambda: Fine),
    ]
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: entries)
    monkeypatch.setattr(registry, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(registry, "_SOURCES", dict(registry._SOURCES))

    sources = registry.all_sources()

    assert "fine-plugin" in sources and "broken" not in sources


@pytest.mark.parametrize("value", [None, "", "soon", 12])
def test_parse_iso_gives_none_for_what_it_cannot_read(value):
    from youpdated.sources.base import parse_iso

    assert parse_iso(value) is None


_FEED = "https://www.youtube.com/feeds/videos.xml"
_INVIDIOUS = "https://inv.example/api/v1/channels/UCLA_DiR1FfKNvjuUpBHmylQ/latest"


def _invidious(*videos):
    respx.get("https://api.invidious.io/instances.json").mock(
        return_value=httpx.Response(
            200, json=[["inv.example", {"type": "https", "api": True, "uri": "https://inv.example"}]]
        )
    )
    return respx.get(_INVIDIOUS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"videoId": vid, "title": vid, "published": int(when.timestamp()), "author": "NASA"}
                for vid, when in videos
            ],
        )
    )


@respx.mock
def test_youtube_fallback_reaching_further_back_reports_no_old_videos(state, client):
    """Invidious lists a channel's videos without its Shorts and streams"""
    from datetime import datetime, timedelta, timezone

    config = parse_config({"sources": {"youtube": ["UCLA_DiR1FfKNvjuUpBHmylQ"]}})
    respx.get(url__startswith=_FEED).mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    run(config, state, client)

    respx.get(url__startswith=_FEED).mock(return_value=httpx.Response(404, text="down"))
    now = datetime.now(timezone.utc)
    _invidious(("newvideo001", now - timedelta(hours=2)), ("oldvideo001", now - timedelta(days=200)))
    later = run(config, state, client)
    assert [u.uid for u in later.updates] == ["yt:video:newvideo001"]
    assert not later.errors

    # Back on the feed, what the fallback listed isn't new either
    respx.get(url__startswith=_FEED).mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    assert run(config, state, client).updates == []


@respx.mock
def test_youtube_feed_back_after_an_outage_reports_no_old_shorts(state, client):
    """A channel first checked through Invidious never saw Shorts and streams"""
    from datetime import datetime, timedelta, timezone

    config = parse_config({"sources": {"youtube": ["UCLA_DiR1FfKNvjuUpBHmylQ"]}})
    respx.get(url__startswith=_FEED).mock(return_value=httpx.Response(404, text="down"))
    _invidious(("newvideo001", datetime.now(timezone.utc) - timedelta(days=3)))
    assert run(config, state, client).baseline

    respx.get(url__startswith=_FEED).mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    back = run(config, state, client)
    assert back.updates == [] and not back.errors


@respx.mock
def test_youtube_on_one_path_reports_old_videos_it_finds(state, client):
    from datetime import datetime, timedelta, timezone

    config = parse_config({"sources": {"youtube": ["UCLA_DiR1FfKNvjuUpBHmylQ"]}})
    respx.get(url__startswith=_FEED).mock(return_value=httpx.Response(404, text="down"))
    now = datetime.now(timezone.utc)
    route = _invidious(("newvideo001", now - timedelta(hours=2)))
    run(config, state, client)
    route.return_value = httpx.Response(
        200, json=[{"videoId": "oldvideo001", "title": "x", "published": int((now - timedelta(days=90)).timestamp())}]
    )
    assert [u.uid for u in run(config, state, client).updates] == ["yt:video:oldvideo001"]
