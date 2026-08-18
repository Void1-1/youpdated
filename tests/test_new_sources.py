"""Browser releases, itch build tracking, and generic feed source"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

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
    assert updates[0].tags == ("release",)
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
    assert {"devlog", "release"} <= {t for u in updates for t in u.tags}


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


@pytest.mark.parametrize("entry", ["not-a-url", "ftp://example.com/feed", {"url": ""}])
def test_generic_feed_rejects_non_http_urls(entry):
    with pytest.raises(ConfigEntryError):
        get_source("feed").targets([entry])


def test_generic_feed_extracts_versions_from_release_titles():
    from youpdated.sources.generic import _version_from_title

    assert _version_from_title({"title": "Obsidian v1.5.3"}) == "1.5.3"
    assert _version_from_title({"title": "Announcing Rust 1.80.0"}) == "1.80.0"
    assert _version_from_title({"title": "A blog post"}) is None
