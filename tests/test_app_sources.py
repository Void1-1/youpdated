"""App sources: sparkle, appstore, flathub, fdroid, winget"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from youpdated.edit import candidates
from youpdated.http import FetchError
from youpdated.sources.base import ConfigEntryError

from .conftest import fixture, get_source


def _fetch(client, source_name, entry):
    source = get_source(source_name)
    (target,) = source.targets([entry])
    return target, list(source.fetch(target, client))


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://www.iterm2.com/appcasts/final_modern.xml", "sparkle"),
        ("https://protonvpn.com/download/macos/updates/v5/sparkle.xml", "sparkle"),
        ("https://apps.apple.com/us/app/xcode/id497799835?mt=12", "appstore"),
        ("https://flathub.org/apps/org.mozilla.firefox", "flathub"),
        ("https://flathub.org/en/apps/org.gnome.Builder", "flathub"),
        ("https://f-droid.org/en/packages/org.fdroid.fdroid/", "fdroid"),
        ("https://winstall.app/apps/Mozilla.Firefox", "winget"),
    ],
)
def test_app_pages_are_claimed(value, expected):
    assert list(candidates(value)) == [expected]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://github.com/sparkle-project/Sparkle", ["git"]),
        ("https://pypi.org/project/sparkle/", ["pypi"]),
        ("https://example.com/appcast", ["sparkle"]),
    ],
)
def test_sparkle_in_a_page_path_isnt_an_appcast(value, expected):
    assert list(candidates(value)) == expected


def test_a_plain_feed_is_still_the_feed_source():
    assert candidates("https://example.com/releases.xml") == {
        "feed": "https://example.com/releases.xml"
    }


# sparkle

ITERM = "https://www.iterm2.com/appcasts/final_modern.xml"

SPARKLE_2 = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Example</title>
    <item>
      <title>Example 2.1 beta</title>
      <pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate>
      <sparkle:version>210</sparkle:version>
      <sparkle:shortVersionString>2.1b1</sparkle:shortVersionString>
      <sparkle:channel>beta</sparkle:channel>
      <enclosure url="https://example.com/Example-2.1b1.zip" length="1" type="application/zip"/>
    </item>
    <item>
      <title>Example 2.0</title>
      <link>https://example.com/notes/2.0</link>
      <description><![CDATA[<ul><li>Faster</li></ul>]]></description>
      <pubDate>Sun, 01 Nov 2026 10:00:00 +0000</pubDate>
      <sparkle:version>200</sparkle:version>
      <sparkle:shortVersionString>2.0</sparkle:shortVersionString>
      <sparkle:criticalUpdate/>
      <enclosure url="https://example.com/Example-2.0.zip" length="1" type="application/zip"/>
    </item>
  </channel>
</rss>"""


def test_sparkle_targets():
    (target,) = get_source("sparkle").targets([{"url": ITERM, "name": "iTerm2"}])
    assert target.key == ITERM and target.label == "iTerm2"
    with pytest.raises(ConfigEntryError):
        get_source("sparkle").targets(["not a url"])


@respx.mock
def test_sparkle_reads_versions_off_the_enclosure_newest_first(client):
    respx.get(ITERM).mock(return_value=httpx.Response(200, content=fixture("sparkle_iterm2.xml")))
    target, updates = _fetch(client, "sparkle", ITERM)

    # appcast lists 3.6.11 before 3.7.9
    assert [u.version for u in updates] == ["3.7.9", "3.6.11"]
    assert updates[0].uid == "version:3.7.9"
    assert updates[0].tags == ("release", "latest")
    assert updates[0].url == "https://iterm2.com/appcasts/full_changes.txt"
    assert target.label == "iTerm2 Changelog - Final releases"


@respx.mock
def test_sparkle_2_elements_channels_and_critical_updates(client):
    url = "https://example.com/appcast.xml"
    respx.get(url).mock(return_value=httpx.Response(200, text=SPARKLE_2))
    _, (beta, final) = _fetch(client, "sparkle", url)

    assert beta.uid == "version:210" and beta.version == "2.1b1"
    assert beta.tags == ("release", "prerelease", "beta")
    assert beta.url == "https://example.com/Example-2.1b1.zip"
    assert final.tags == ("release", "critical", "latest")
    assert final.url == "https://example.com/notes/2.0"
    assert final.body == "Faster"


@respx.mock
def test_sparkle_rejects_a_page_that_isnt_an_appcast(client):
    url = "https://example.com/appcast.xml"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html><body>hi</body></html>"))
    with pytest.raises(FetchError, match="not an appcast"):
        _fetch(client, "sparkle", url)


# appstore

LOOKUP = "https://itunes.apple.com/lookup"


@pytest.mark.parametrize(
    "entry,key,params",
    [
        ("284882215", "284882215", {"kind": "id", "app": "284882215", "country": "us"}),
        ("id284882215", "284882215", {"kind": "id", "app": "284882215", "country": "us"}),
        ("gb/284882215", "gb/284882215", {"kind": "id", "app": "284882215", "country": "gb"}),
        ({"app": "284882215", "country": "DE"}, "de/284882215",
         {"kind": "id", "app": "284882215", "country": "de"}),
        ("com.apple.dt.Xcode", "com.apple.dt.Xcode",
         {"kind": "bundleId", "app": "com.apple.dt.Xcode", "country": "us"}),
    ],
)
def test_appstore_targets(entry, key, params):
    (target,) = get_source("appstore").targets([entry])
    assert (target.key, target.params) == (key, params)


@pytest.mark.parametrize("entry", ["facebook", "12", {"app": "284882215", "country": "usa"}])
def test_appstore_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("appstore").targets([entry])


def test_appstore_leaves_bare_numbers_to_steam():
    assert candidates("440") == {"steam": "440"}
    assert candidates("https://apps.apple.com/gb/app/facebook/id284882215") == {
        "appstore": "gb/284882215"
    }


@respx.mock
def test_appstore_reports_the_current_version(client):
    route = respx.get(LOOKUP, params={"id": "284882215", "country": "us"}).mock(
        return_value=httpx.Response(200, content=fixture("appstore_facebook.json"))
    )
    target, (update,) = _fetch(client, "appstore", "284882215")

    assert route.called
    assert update.uid == "version:588.1"
    assert update.title == "Facebook 588.1"
    assert update.published.isoformat() == "2026-11-20T07:29:44+00:00"
    assert update.tags == ("release", "latest")
    assert target.label == "Facebook"


@respx.mock
def test_appstore_unknown_app_says_so(client):
    respx.get(LOOKUP).mock(return_value=httpx.Response(200, json={"resultCount": 0, "results": []}))
    with pytest.raises(FetchError, match="no app `1234567` in the GB App Store"):
        _fetch(client, "appstore", "gb/1234567")


# flathub

BUILDER = "https://flathub.org/api/v2/appstream/org.gnome.Builder"


@pytest.mark.parametrize("entry", ["org.gnome", "Builder", "org/gnome/Builder"])
def test_flathub_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("flathub").targets([entry])


@respx.mock
def test_flathub_reports_releases(client):
    respx.get(BUILDER).mock(return_value=httpx.Response(200, content=fixture("flathub_builder.json")))
    target, updates = _fetch(client, "flathub", "org.gnome.Builder")

    assert updates[0].uid == "version:50.0"
    assert updates[0].title == "Builder 50.0"
    assert updates[0].tags == ("release", "latest")
    assert updates[0].published.isoformat() == "2026-03-17T00:00:00+00:00"
    assert target.label == "Builder"


@respx.mock
def test_flathub_development_releases_are_prereleases(client):
    doc = json.loads(fixture("flathub_builder.json"))
    doc["releases"].insert(
        0, {"version": "51.alpha", "timestamp": "1794787200", "type": "development",
            "description": "<p>Try <em>this</em></p>"}
    )
    respx.get(BUILDER).mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "flathub", "org.gnome.Builder")
    assert updates[0].tags == ("release", "prerelease")
    assert updates[0].body == "Try this"
    assert [u.version for u in updates if "latest" in u.tags] == ["50.0"]


@respx.mock
def test_flathub_unknown_app_says_so(client):
    respx.get(BUILDER).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no app `org.gnome.Builder` on Flathub"):
        _fetch(client, "flathub", "org.gnome.Builder")


# fdroid

FDROID = "https://f-droid.org/api/v1/packages/org.fdroid.fdroid"


@respx.mock
def test_fdroid_reports_each_version_by_code(client):
    respx.get(FDROID).mock(return_value=httpx.Response(200, content=fixture("fdroid_fdroid.json")))
    _, updates = _fetch(client, "fdroid", "org.fdroid.fdroid")

    assert [u.uid for u in updates] == ["code:2000053", "code:2000052", "code:2000051"]
    assert updates[0].version == "2.0.3"
    assert updates[0].tags == ("release", "latest")
    assert all(u.published is None for u in updates)


@respx.mock
def test_fdroid_versions_past_the_suggested_one_are_prereleases(client):
    doc = json.loads(fixture("fdroid_fdroid.json"))
    doc["suggestedVersionCode"] = 2000052
    respx.get(FDROID).mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "fdroid", "org.fdroid.fdroid")
    assert updates[0].tags == ("release", "prerelease")
    assert updates[1].tags == ("release", "latest")


def test_fdroid_rejects_a_name_without_a_dot():
    with pytest.raises(ConfigEntryError, match="Android package name"):
        get_source("fdroid").targets(["fdroid"])


# winget

WINGET_FIREFOX = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/m/Mozilla/Firefox"


@respx.mock
def test_winget_lists_version_folders_only(client):
    respx.get(WINGET_FIREFOX).mock(return_value=httpx.Response(200, content=fixture("winget_firefox.json")))
    _, updates = _fetch(client, "winget", "Mozilla.Firefox")

    versions = [u.version for u in updates]
    assert versions[0] == "160.0.1"
    assert not {"ESR", "Beta", "de", "en-GB"} & set(versions)
    assert updates[0].tags == ("release", "latest")
    assert len(updates) == 10


@respx.mock
def test_winget_sends_a_github_token_if_set(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    route = respx.get(WINGET_FIREFOX).mock(
        return_value=httpx.Response(200, content=fixture("winget_firefox.json"))
    )
    _fetch(client, "winget", "Mozilla.Firefox")
    assert route.calls.last.request.headers["Authorization"] == "Bearer t0ken"


@respx.mock
def test_winget_folder_of_only_sub_packages_says_so(client):
    doc = [e for e in json.loads(fixture("winget_firefox.json")) if not e["name"][0].isdigit()]
    respx.get(WINGET_FIREFOX).mock(return_value=httpx.Response(200, json=doc))
    with pytest.raises(FetchError, match="no versions"):
        _fetch(client, "winget", "Mozilla.Firefox")


@respx.mock
def test_winget_unknown_package_says_so(client):
    respx.get(WINGET_FIREFOX).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no winget package `Mozilla.Firefox`") as raised:
        _fetch(client, "winget", "Mozilla.Firefox")
    assert "case-sensitive" in str(raised.value)


@pytest.mark.parametrize("entry", ["Firefox", "Mozilla Firefox", "Mozilla/Firefox"])
def test_winget_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError, match="Publisher.Package"):
        get_source("winget").targets([entry])
