"""Browser releases, itch build tracking, and generic feed source"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from youpdated.http import FetchError
from youpdated.registry import get_source
from youpdated.sources.base import ConfigEntryError

from .conftest import fixture

CHROME_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/mac/"
    "channels/stable/versions/all/releases"
)
EDGE_URL = "https://edgeupdates.microsoft.com/api/products"
FIREFOX_URL = "https://product-details.mozilla.org/1.0/firefox_versions.json"
BRAVE_ATOM_URL = "https://github.com/brave/brave-browser/releases.atom"
BRAVE_API_URL = "https://api.github.com/repos/brave/brave-browser/releases?per_page=100"


# itch: builds and devlogs


@respx.mock
def test_itch_reports_a_build_for_a_game_with_no_devlog(client):
    respx.get("https://hempuli.itch.io/baba-is-you/devlog.rss").mock(
        return_value=httpx.Response(404, text="not found")
    )
    respx.get("https://hempuli.itch.io/baba-is-you").mock(
        return_value=httpx.Response(200, content=fixture("itch_game_no_devlog.html"))
    )
    source = get_source("itch")
    (target,) = source.targets(["https://hempuli.itch.io/baba-is-you"])
    updates = list(source.fetch(target, client))

    assert len(updates) == 1
    assert updates[0].tags == ("build",)
    assert "2 file(s)" in updates[0].title
    assert "Baba Is You (zip)" in updates[0].body


@respx.mock
def test_itch_build_uses_the_updated_timestamp_when_present(client):
    respx.get(url__startswith="https://aak581.itch.io").mock(
        return_value=httpx.Response(200, content=fixture("itch_game_page.html"))
    )
    source = get_source("itch")
    (target,) = source.targets(
        [{"url": "https://aak581.itch.io/engineering-marvels-from-hell", "watch": ["releases"]}]
    )
    (update,) = list(source.fetch(target, client))

    assert update.published == datetime(2026, 8, 17, 10, 34, tzinfo=timezone.utc)
    assert "Engineering_Marvels_From_Hell_Win_1.4.zip" in update.body


@respx.mock
def test_itch_build_uid_changes_only_when_the_build_changes(client):
    page = fixture("itch_game_page.html").decode()
    respx.get(url__startswith="https://aak581.itch.io").mock(
        return_value=httpx.Response(200, text=page)
    )
    source = get_source("itch")
    (target,) = source.targets(
        [{"url": "https://aak581.itch.io/engineering-marvels-from-hell", "watch": ["releases"]}]
    )
    first = list(source.fetch(target, client))[0]

    # Same page again -> same uid, not reported twice
    assert list(source.fetch(target, client))[0].uid == first.uid

    # A new upload -> new fingerprint
    respx.get(url__startswith="https://aak581.itch.io").mock(
        return_value=httpx.Response(
            200,
            text=page.replace("_Win_1.4.zip", "_Win_1.5.zip"),
        )
    )
    assert list(source.fetch(target, client))[0].uid != first.uid


@respx.mock
def test_itch_build_uid_ignores_attribute_order(client):
    """itch shuffles attribute order between responses of the same unchanged page"""
    page = fixture("itch_game_page.html").decode()
    shuffled = page.replace('<strong title="', '<strong class="name" title="').replace(
        '" class="name">', '">'
    )
    assert shuffled != page
    route = respx.get(url__startswith="https://aak581.itch.io")
    source = get_source("itch")
    (target,) = source.targets(
        [{"url": "https://aak581.itch.io/engineering-marvels-from-hell", "watch": ["releases"]}]
    )

    route.mock(return_value=httpx.Response(200, text=page))
    (first,) = list(source.fetch(target, client))
    route.mock(return_value=httpx.Response(200, text=shuffled))
    (second,) = list(source.fetch(target, client))

    assert second.uid == first.uid
    assert second.body == first.body == (
        "Engineering_Marvels_From_Hell_Win_1.4.zip (180 MB), "
        "Engineering Marvels From Hell v1.4.apk (92 MB), "
        "Engineering_Marvels_From_Hell_Mac_1.4.zip (234 MB), Bonus.zip (9.9 MB)"
    )


@respx.mock
def test_itch_watches_both_by_default(client):
    devlog = respx.get(
        "https://aak581.itch.io/engineering-marvels-from-hell/devlog.rss"
    ).mock(return_value=httpx.Response(200, content=fixture("itch_devlog.rss")))
    page = respx.get("https://aak581.itch.io/engineering-marvels-from-hell").mock(
        return_value=httpx.Response(200, content=fixture("itch_game_page.html"))
    )
    source = get_source("itch")
    (target,) = source.targets(["https://aak581.itch.io/engineering-marvels-from-hell"])
    updates = list(source.fetch(target, client))

    assert devlog.called and page.called
    assert {"devlog", "build"} <= {t for u in updates for t in u.tags}


def test_itch_rejects_unknown_watch():
    with pytest.raises(ConfigEntryError):
        get_source("itch").targets([{"url": "https://u.itch.io/g", "watch": ["nope"]}])


@respx.mock
def test_itch_page_with_nothing_observable_reports_nothing(client):
    respx.get(url__startswith="https://u.itch.io").mock(
        return_value=httpx.Response(200, text="<html><body>no info panel</body></html>")
    )
    source = get_source("itch")
    (target,) = source.targets([{"url": "https://u.itch.io/g", "watch": ["releases"]}])
    assert list(source.fetch(target, client)) == []


# browsers


@pytest.mark.parametrize(
    "entries,expected",
    [
        (["brave"], ["brave/stable"]),
        ([{"browser": "brave", "channel": "nightly"}], ["brave/nightly"]),
        ([{"browser": "chrome", "platform": "mac"}], ["chrome/mac/stable"]),
        ([{"browser": "chrome", "platform": "win", "channel": "beta"}], ["chrome/windows/beta"]),
        ([{"browser": "firefox", "channel": "esr"}], None),  # platform is host-dependent
    ],
)
def test_browser_targets_normalize(entries, expected):
    targets = get_source("browser").targets(entries)
    if expected is not None:
        assert [t.key for t in targets] == expected
    else:
        assert targets[0].key.startswith("firefox/") and targets[0].key.endswith("/esr")


@pytest.mark.parametrize(
    "entry",
    [
        "netscape",
        {"browser": "chrome", "platform": "amiga"},
        {"browser": "chrome", "channel": "experimental"},
        {"browser": "firefox", "channel": "canary"},
    ],
)
def test_browser_rejects_unknown_values(entry):
    with pytest.raises(ConfigEntryError):
        get_source("browser").targets([entry])


@respx.mock
def test_chrome_releases(client):
    respx.get(CHROME_URL).mock(
        return_value=httpx.Response(200, content=fixture("chrome_releases.json"))
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "chrome", "platform": "mac", "channel": "stable"}])
    updates = list(source.fetch(target, client))

    assert updates
    # A version appears once even though the API lists it per rollout fraction
    versions = [u.version for u in updates]
    assert len(versions) == len(set(versions))
    assert all(u.uid.startswith("chrome:mac:stable:") for u in updates)
    dated = [u.published for u in updates if u.published]
    assert dated == sorted(dated, reverse=True)



@respx.mock
def test_chrome_tolerates_a_version_first_listed_without_a_start_time(client):
    respx.get(CHROME_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "releases": [
                    {"version": "130.0.1", "serving": {}},
                    {"version": "130.0.1", "serving": {"startTime": "2026-01-01T00:00:00Z"}},
                ]
            },
        )
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "chrome", "platform": "mac", "channel": "stable"}])
    (update,) = list(source.fetch(target, client))

    assert update.published == datetime(2026, 1, 1, tzinfo=timezone.utc)

def _mock_brave_api():
    return respx.get(BRAVE_API_URL).mock(
        return_value=httpx.Response(200, content=fixture("brave_api_releases.json"))
    )


@respx.mock
def test_brave_releases_parse_version_out_of_the_tag(client):
    _mock_brave_api()
    source = get_source("browser")
    (target,) = source.targets([{"browser": "brave", "channel": "nightly"}])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.version and u.version.startswith("1.") for u in updates if u.version)


@respx.mock
def test_brave_channel_filters_one_shared_feed(client):
    """Brave puts Release, Beta, and Nightly in the same repo; asking for one
    channel must not report the others."""
    _mock_brave_api()
    source = get_source("browser")
    (nightly,) = source.targets([{"browser": "brave", "channel": "nightly"}])
    (beta,) = source.targets([{"browser": "brave", "channel": "beta"}])

    nightly_updates = list(source.fetch(nightly, client))
    beta_updates = list(source.fetch(beta, client))

    assert nightly_updates and beta_updates
    assert all(u.title.startswith("Nightly") for u in nightly_updates)
    assert all(u.title.startswith("Beta") for u in beta_updates)
    assert not {u.uid for u in nightly_updates} & {u.uid for u in beta_updates}


@respx.mock
def test_brave_stable_is_found_via_the_api(client):
    _mock_brave_api()
    source = get_source("browser")
    (target,) = source.targets(["brave"])
    updates = list(source.fetch(target, client))

    assert updates, "stable releases must be reachable"
    assert all(u.title.startswith("Release") for u in updates)


@respx.mock
def test_brave_falls_back_to_atom_when_the_api_rate_limits(client):
    respx.get(BRAVE_API_URL).mock(return_value=httpx.Response(403, json={"message": "limit"}))
    atom = respx.get(BRAVE_ATOM_URL).mock(
        return_value=httpx.Response(200, content=fixture("brave_releases.atom"))
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "brave", "channel": "nightly"}])
    updates = list(source.fetch(target, client))

    assert atom.called
    assert updates and all(u.title.startswith("Nightly") for u in updates)
    # Both paths key on the tag: later API run won't re-report
    assert all(u.uid.startswith("brave:v") for u in updates)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@respx.mock
def test_brave_falls_back_to_atom_on_a_server_error(client, status):
    respx.get(BRAVE_API_URL).mock(return_value=httpx.Response(status, text="upstream sad"))
    atom = respx.get(BRAVE_ATOM_URL).mock(
        return_value=httpx.Response(200, content=fixture("brave_releases.atom"))
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "brave", "channel": "nightly"}])
    updates = list(source.fetch(target, client))

    assert atom.called, f"HTTP {status} must fall back, not raise"
    assert updates and all(u.title.startswith("Nightly") for u in updates)


@respx.mock
def test_brave_falls_back_to_atom_on_a_transport_error(client):
    """A timeout or DNS failure should reach the fallback too, not just statuses."""
    respx.get(BRAVE_API_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    atom = respx.get(BRAVE_ATOM_URL).mock(
        return_value=httpx.Response(200, content=fixture("brave_releases.atom"))
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "brave", "channel": "beta"}])
    updates = list(source.fetch(target, client))

    assert atom.called
    assert updates and all(u.title.startswith("Beta") for u in updates)


@respx.mock
def test_brave_still_raises_when_both_paths_fail(client):
    """Falling back must not paper over a total outage."""
    respx.get(BRAVE_API_URL).mock(return_value=httpx.Response(504, text="gateway"))
    respx.get(BRAVE_ATOM_URL).mock(return_value=httpx.Response(504, text="gateway"))
    source = get_source("browser")
    (target,) = source.targets(["brave"])

    with pytest.raises(Exception):
        list(source.fetch(target, client))


@respx.mock
def test_edge_links_to_the_matching_channel_release_notes(client):
    respx.get(EDGE_URL).mock(
        return_value=httpx.Response(200, content=fixture("edge_products.json"))
    )
    source = get_source("browser")
    (stable,) = source.targets([{"browser": "edge", "platform": "mac", "channel": "stable"}])
    (beta,) = source.targets([{"browser": "edge", "platform": "mac", "channel": "beta"}])

    assert all("stable-channel" in u.url for u in source.fetch(stable, client))
    assert all("beta-channel" in u.url for u in source.fetch(beta, client))


@respx.mock
def test_firefox_reports_one_version_per_channel(client):
    respx.get(FIREFOX_URL).mock(
        return_value=httpx.Response(200, content=fixture("firefox_versions.json"))
    )
    source = get_source("browser")
    (stable,) = source.targets([{"browser": "firefox", "channel": "stable"}])
    (esr,) = source.targets([{"browser": "firefox", "channel": "esr"}])

    (a,) = list(source.fetch(stable, client))
    (b,) = list(source.fetch(esr, client))

    assert a.version and b.version and a.version != b.version
    assert b.version.endswith("esr")
    assert a.uid != b.uid


@respx.mock
def test_edge_filters_to_the_requested_platform_and_channel(client):
    respx.get(EDGE_URL).mock(
        return_value=httpx.Response(200, content=fixture("edge_products.json"))
    )
    source = get_source("browser")
    (target,) = source.targets([{"browser": "edge", "platform": "mac", "channel": "stable"}])
    updates = list(source.fetch(target, client))

    assert updates
    assert all(u.uid.startswith("edge:Stable:MacOS:") for u in updates)
    assert all("MacOS" in (u.body or "") for u in updates)


@respx.mock
def test_edge_unknown_product_reports_nothing(client):
    respx.get(EDGE_URL).mock(return_value=httpx.Response(200, json=[]))
    source = get_source("browser")
    (target,) = source.targets([{"browser": "edge", "channel": "beta"}])
    assert list(source.fetch(target, client)) == []


# generic feed


@respx.mock
def test_generic_feed_uses_the_feed_title_as_label(client):
    respx.get("https://blog.rust-lang.org/feed.xml").mock(
        return_value=httpx.Response(200, content=fixture("generic_feed.xml"))
    )
    source = get_source("feed")
    (target,) = source.targets(["https://blog.rust-lang.org/feed.xml"])
    updates = list(source.fetch(target, client))

    assert target.label == "Rust Blog"
    assert updates and all(u.source == "feed" for u in updates)


@respx.mock
def test_generic_feed_respects_an_explicit_name_and_limit(client):
    respx.get("https://blog.rust-lang.org/feed.xml").mock(
        return_value=httpx.Response(200, content=fixture("generic_feed.xml"))
    )
    source = get_source("feed")
    (target,) = source.targets(
        [{"url": "https://blog.rust-lang.org/feed.xml", "name": "Rust", "limit": 2}]
    )
    updates = list(source.fetch(target, client))

    assert target.label == "Rust"
    assert len(updates) == 2


def test_feed_limit_keeps_the_newest_entries_of_an_oldest_first_feed():
    from youpdated.sources.feed import parse_feed

    items = "".join(
        f"<item><title>post {day}</title><guid>id-{day}</guid>"
        f"<pubDate>{day:02d} Jan 2026 00:00:00 GMT</pubDate></item>"
        for day in range(1, 26)
    )
    doc = f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'
    updates = parse_feed(doc.encode(), source="feed", target="t", limit=20)

    assert len(updates) == 20
    assert updates[0].title == "post 25" and updates[-1].title == "post 6"


@respx.mock
def test_generic_feed_parses_the_document_only_once(client, monkeypatch):
    from youpdated.sources import feed as feed_module

    calls = []
    real = feed_module.feedparser.parse

    def counting_parse(content, *args, **kwargs):
        calls.append(content)
        return real(content, *args, **kwargs)

    monkeypatch.setattr(feed_module.feedparser, "parse", counting_parse)

    respx.get("https://blog.rust-lang.org/feed.xml").mock(
        return_value=httpx.Response(200, content=fixture("generic_feed.xml"))
    )
    source = get_source("feed")
    (target,) = source.targets(["https://blog.rust-lang.org/feed.xml"])
    updates = list(source.fetch(target, client))

    # The label still comes from the feed, and it cost exactly one parse.
    assert target.label == "Rust Blog"
    assert updates
    assert len(calls) == 1


@pytest.mark.parametrize("entry", ["not-a-url", "ftp://example.com/feed", {"url": ""}])
def test_generic_feed_rejects_non_http_urls(entry):
    with pytest.raises(ConfigEntryError):
        get_source("feed").targets([entry])


def test_generic_feed_extracts_versions_from_release_titles():
    from youpdated.sources.generic import _version_from_title

    assert _version_from_title({"title": "Obsidian v1.5.3"}) == "1.5.3"
    assert _version_from_title({"title": "Announcing Rust 1.80.0"}) == "1.80.0"
    assert _version_from_title({"title": "A blog post"}) is None

# homebrew

BREW_WGET = "https://formulae.brew.sh/api/formula/wget.json"
BREW_FIREFOX = "https://formulae.brew.sh/api/cask/firefox.json"

@pytest.mark.parametrize(
    "entry,key",
    [
        ("wget", "formula:wget"),
        ("formula:wget", "formula:wget"),
        ("cask:firefox", "cask:firefox"),
        ({"formula": "python@3.12"}, "formula:python@3.12"),
        ({"cask": "firefox", "name": "Firefox"}, "cask:firefox"),
        ("libstdc++", "formula:libstdc++"),
    ],
)
def test_homebrew_targets(entry, key):
    (target,) = get_source("homebrew").targets([entry])
    assert target.key == key

@pytest.mark.parametrize(
    "entry",
    [
        {"formula": "wget", "cask": "wget"},
        {"name": "nothing to watch"},
        "user/tap/thing",
        "not a name",
        "",
    ],
)
def test_homebrew_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("homebrew").targets([entry])


@respx.mock
def test_homebrew_formula_version_includes_the_revision(client):
    respx.get(BREW_WGET).mock(
        return_value=httpx.Response(200, content=fixture("homebrew_wget.json"))
    )
    source = get_source("homebrew")
    (target,) = source.targets(["wget"])
    (update,) = source.fetch(target, client)

    assert update.version == "1.25.0_2"
    assert update.uid == "version:1.25.0_2"
    assert update.url == "https://formulae.brew.sh/formula/wget"
    assert update.tags == ("release",)
    # stable across fetches of the same payload
    assert [u.uid for u in source.fetch(target, client)] == [update.uid]


@respx.mock
def test_homebrew_formula_without_a_revision_is_the_bare_version(client):
    doc = json.loads(fixture("homebrew_wget.json"))
    doc["revision"] = 0
    respx.get(BREW_WGET).mock(return_value=httpx.Response(200, json=doc))
    source = get_source("homebrew")
    (target,) = source.targets(["wget"])
    (update,) = source.fetch(target, client)
    assert update.version == "1.25.0"


@respx.mock
def test_homebrew_head_only_formula_reports_nothing(client):
    doc = json.loads(fixture("homebrew_wget.json"))
    doc["versions"]["stable"] = None
    respx.get(BREW_WGET).mock(return_value=httpx.Response(200, json=doc))
    source = get_source("homebrew")
    (target,) = source.targets(["wget"])
    assert list(source.fetch(target, client)) == []


@respx.mock
def test_homebrew_cask_version_is_as_given(client):
    respx.get(BREW_FIREFOX).mock(
        return_value=httpx.Response(200, content=fixture("homebrew_firefox.json"))
    )
    source = get_source("homebrew")
    (target,) = source.targets([{"cask": "firefox"}])
    (update,) = source.fetch(target, client)

    assert update.version == "157.0.1"
    assert update.url == "https://formulae.brew.sh/cask/firefox"


@respx.mock
def test_homebrew_unknown_name_says_so(client):
    respx.get("https://formulae.brew.sh/api/formula/nope.json").mock(
        return_value=httpx.Response(404, text="not found")
    )
    source = get_source("homebrew")
    (target,) = source.targets(["nope"])
    with pytest.raises(FetchError, match="no formula named `nope`"):
        list(source.fetch(target, client))

# pypi

PYPI_HTTPX = "https://pypi.org/simple/httpx/"


def _pypi_doc():
    return json.loads(fixture("pypi_httpx_simple.json"))


def _pypi_updates(client, doc=None):
    respx.get(PYPI_HTTPX).mock(return_value=httpx.Response(200, json=doc or _pypi_doc()))
    source = get_source("pypi")
    (target,) = source.targets(["httpx"])
    return list(source.fetch(target, client))


def _pypi_file(filename, uploaded="2026-01-01T00:00:00Z", yanked=False):
    return {"filename": filename, "upload-time": uploaded, "yanked": yanked}


@pytest.mark.parametrize(
    "entry,key",
    [
        ("httpx", "httpx"),
        ({"package": "httpx", "name": "HTTPX"}, "httpx"),
        ("Django", "django"),
        ("typing_extensions", "typing-extensions"),
        ("zope.interface", "zope-interface"),
    ],
)
def test_pypi_targets_normalize_the_name(entry, key):
    (target,) = get_source("pypi").targets([entry])
    assert target.key == key


@pytest.mark.parametrize(
    "entry",
    ["", "owner/package", "not a name", "-leading", "trailing.", {"name": "nothing to watch"}],
)
def test_pypi_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("pypi").targets([entry])


@respx.mock
def test_pypi_asks_for_the_json_index(client):
    _pypi_updates(client)
    (call,) = respx.calls
    assert call.request.headers["Accept"] == "application/vnd.pypi.simple.v1+json"


@respx.mock
def test_pypi_reports_newest_releases(client):
    updates = _pypi_updates(client)

    assert len(updates) == 10
    assert updates[0].version == "1.0.dev6"
    assert updates[0].uid == "version:1.0.dev6"
    assert updates[0].url == "https://pypi.org/project/httpx/1.0.dev6/"
    assert updates[0].title == "httpx 1.0.dev6"
    dated = [u.published for u in updates]
    assert all(d and d.tzinfo for d in dated)
    assert dated == sorted(dated, reverse=True)
    # the first upload of a release is its date
    assert updates[0].published == datetime(2026, 8, 31, 17, 59, 54, 190125, tzinfo=timezone.utc)


@respx.mock
def test_pypi_tags_latest_and_prereleases(client):
    tags = {u.version: u.tags for u in _pypi_updates(client)}

    # 1.0.dev6 is newer, but the latest is the highest final release
    assert tags["0.28.1"] == ("release", "latest")
    assert tags["0.28.0"] == ("release",)
    assert tags["1.0.dev6"] == ("release", "prerelease")


@respx.mock
def test_pypi_latest_is_the_highest_version_not_the_newest(client):
    doc = _pypi_doc()
    # a backport uploaded after 0.28.1
    doc["versions"].append("0.27.3")
    doc["files"].append(_pypi_file("httpx-0.27.3.tar.gz", "2026-09-01T00:00:00Z"))
    tags = {u.version: u.tags for u in _pypi_updates(client, doc)}

    assert tags["0.27.3"] == ("release",)
    assert tags["0.28.1"] == ("release", "latest")


@respx.mock
def test_pypi_latest_falls_back_to_a_prerelease(client):
    doc = {
        "name": "httpx",
        "versions": ["1.0a1", "1.0b1"],
        "files": [_pypi_file("httpx-1.0a1.tar.gz"), _pypi_file("httpx-1.0b1.tar.gz")],
    }
    tags = {u.version: u.tags for u in _pypi_updates(client, doc)}
    assert tags["1.0b1"] == ("release", "prerelease", "latest")


@pytest.mark.parametrize(
    "version,pre",
    [
        ("1.0a1", True),
        ("6.1rc1", True),
        ("2.0.0b3", True),
        ("1.0.dev6", True),
        ("1.0-beta", True),
        ("1.0.post1", False),
        ("2.0", False),
        ("1.0+abc", False),
        ("2004d", False),
    ],
)
def test_pypi_prerelease_detection(version, pre):
    from youpdated.sources.pypi import _is_prerelease

    assert _is_prerelease(version) is pre


@pytest.mark.parametrize(
    "filename,project,version",
    [
        ("httpx-0.28.1-py3-none-any.whl", "httpx", "0.28.1"),
        ("httpx-0.28.1.tar.gz", "httpx", "0.28.1"),
        ("typing_extensions-4.16.0-py3-none-any.whl", "typing-extensions", "4.16.0"),
        ("PyYAML-3.10.zip", "pyyaml", "3.10"),
        ("zope.interface-4.0.tar.gz", "zope-interface", "4.0"),
        ("pywin32-218.win32-py2.7.exe", "pywin32", "218"),
        ("distribute-0.6-py2.4.egg", "distribute", "0.6"),
        ("thing-1.0-beta.tar.gz", "thing", "1.0-beta"),
        # filename spelled 69.3 for version 69.3.0
        ("setuptools-69.3.tar.gz", "setuptools", "69.3.0"),
        # a wheel build tag is not part of the version
        ("thing-1.0-1-py3-none-any.whl", "thing", "1.0"),
        ("other-1.0.tar.gz", "thing", None),
        ("thing-9.9.tar.gz", "thing", None),
    ],
)
def test_pypi_matches_files_to_versions(filename, project, version):
    from youpdated.sources.pypi import _canonical, _version_of

    versions = ["0.28.1", "4.16.0", "3.10", "4.0", "218", "0.6", "1.0", "1.0-beta", "1.0.post1", "69.3.0"]
    by_canonical = {_canonical(v): v for v in versions}
    assert _version_of(filename, project, set(versions), by_canonical) == version


@respx.mock
def test_pypi_skips_versions_with_no_live_files(client):
    doc = _pypi_doc()
    for file in doc["files"]:
        if file["filename"].startswith("httpx-1.0.dev6"):
            file["yanked"] = "broken build"
    # one live file keeps a release, dated by that file
    for file in doc["files"]:
        if file["filename"] == "httpx-1.0.dev5-py3-none-any.whl":
            file["yanked"] = True
    doc["versions"].append("2.0")
    updates = _pypi_updates(client, doc)
    versions = [u.version for u in updates]

    assert "1.0.dev6" not in versions
    assert "2.0" not in versions
    assert versions[0] == "1.0.dev5"
    assert updates[0].published == datetime(2026, 8, 21, 10, 59, 58, 520677, tzinfo=timezone.utc)


@respx.mock
def test_pypi_uids_are_stable(client):
    first = _pypi_updates(client)
    second = _pypi_updates(client)
    assert [u.uid for u in first] == [u.uid for u in second]


@pytest.mark.parametrize("status", ["archived", "deprecated", "quarantined"])
@respx.mock
def test_pypi_reports_a_project_status(client, status):
    doc = _pypi_doc()
    doc["project-status"] = {"status": status, "reason": "no longer maintained"}
    updates = _pypi_updates(client, doc)
    alert = updates[0]

    assert alert.uid == f"status:{status}"
    assert alert.title == f"httpx is {status}"
    assert alert.url == "https://pypi.org/project/httpx/"
    assert alert.body == "no longer maintained"
    assert alert.tags == ("status", status)
    assert len(updates) == 11


@pytest.mark.parametrize("project_status", [{"status": "active"}, {}, None])
@respx.mock
def test_pypi_active_or_unmarked_project_has_no_status_item(client, project_status):
    doc = _pypi_doc()
    if project_status is None:
        del doc["project-status"]
    else:
        doc["project-status"] = project_status
    assert not any("status" in u.tags for u in _pypi_updates(client, doc))


@respx.mock
def test_pypi_quarantined_project_lists_only_the_status(client):
    doc = {"name": "httpx", "project-status": {"status": "quarantined"}, "versions": [], "files": []}
    (update,) = _pypi_updates(client, doc)
    assert update.uid == "status:quarantined"
    assert update.body is None


@respx.mock
def test_pypi_unknown_package_says_so(client):
    respx.get("https://pypi.org/simple/nope/").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    source = get_source("pypi")
    (target,) = source.targets(["nope"])
    with pytest.raises(FetchError, match="no package named `nope` on PyPI"):
        list(source.fetch(target, client))
