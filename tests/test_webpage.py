"""`webpage` source"""

from __future__ import annotations

import httpx
import pytest
import respx

from youpdated.config import PrivacyConfig, parse_config
from youpdated.edit import guess_source
from youpdated.http import FetchError
from youpdated.runner import run
from youpdated.sources.base import ConfigEntryError
from youpdated.sources.webpage import SIGNAL_CACHE

from .conftest import fixture, get_source

URL = "https://lumen.example/download"


def _page(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


def _serve(markup: str | bytes, content_type: str = "text/html; charset=utf-8") -> None:
    content = markup.encode() if isinstance(markup, str) else markup
    respx.get(URL).mock(
        return_value=httpx.Response(200, content=content, headers={"Content-Type": content_type})
    )


def _fetch(client, entry=URL):
    source = get_source("webpage")
    (target,) = source.targets([entry])
    return target, list(source.fetch(target, client))


def _version(client, markup: str) -> str | None:
    _serve(markup)
    _, (update,) = _fetch(client)
    return update.version


def _refusal(client, markup: str) -> str:
    _serve(markup)
    with pytest.raises(FetchError) as caught:
        _fetch(client)
    return str(caught.value)


# config


def test_both_config_shapes_give_the_same_key():
    source = get_source("webpage")
    bare, mapped = source.targets([URL, {"url": URL, "name": "Lumen"}])
    assert bare.key == mapped.key == URL
    assert mapped.label == "Lumen"


@pytest.mark.parametrize("url", ["lumen.example/download", "ftp://lumen.example/x", "https://"])
def test_a_value_that_is_not_a_page_url_is_refused(url):
    with pytest.raises(ConfigEntryError, match="not an http"):
        get_source("webpage").targets([url])


@pytest.mark.parametrize("regex, message", [("Version (\\d+", "not a valid pattern"), ("", "must be a pattern"), (3, "must be a pattern")])
def test_a_bad_regex_is_refused_before_anything_is_fetched(regex, message):
    with pytest.raises(ConfigEntryError, match=message):
        get_source("webpage").targets([{"url": URL, "regex": regex}])


def test_any_url_no_other_source_claims_is_a_webpage():
    assert guess_source(URL) == ("webpage", URL)
    assert guess_source("https://github.com/python/cpython")[0] == "git"
    assert guess_source("https://blog.example.com/feed.xml")[0] == "feed"
    assert guess_source("https://hempuli.itch.io/baba-is-you")[0] == "itch"


@respx.mock
def test_download_links_that_agree_name_the_version(client):
    _serve(fixture("webpage_download.html"))
    target, (update,) = _fetch(client)

    assert update.version == "2.4.1"
    assert update.uid == "version:2.4.1"
    assert update.title == "2.4.1"
    assert update.url == URL
    assert update.tags == ("release",)
    assert update.body == "Read from the download links: Lumen-2.4.1-universal.dmg"
    assert target.label == "Download Lumen Notes"


@respx.mock
def test_structured_data_names_the_version_and_the_app(client):
    ld = """<script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [
      {"@type": "Organization", "name": "Lumen Labs"},
      {"@type": "SoftwareApplication", "name": "Lumen Notes", "softwareVersion": "2.4.1"}
    ]}</script>"""
    _serve(_page('<a href="/dl/Lumen-2.4.1.dmg">Mac</a>', head=ld + "<title>Download</title>"))
    target, (update,) = _fetch(client)

    assert update.version == "2.4.1"
    assert "structured data" in (update.body or "")
    assert target.label == "Lumen Notes"


@respx.mock
def test_microdata_names_the_version(client):
    markup = _page(
        '<div itemscope itemtype="https://schema.org/SoftwareApplication">'
        '<span itemprop="softwareVersion"><b>5.1</b></span>'
        '<meta itemprop="softwareVersion" content="5.1"></div>'
    )
    assert _version(client, markup) == "5.1"


@respx.mock
def test_microdata_in_an_unclosed_table_cell_ends_with_the_cell(client):
    markup = _page(
        '<table><tr><td>Version<td itemprop="softwareVersion">2.4.1'
        "<tr><td>Requires<td>macOS 14.0</table>"
    )
    assert _version(client, markup) == "2.4.1"


@respx.mock
def test_a_lone_number_counts_only_in_structured_data(client):
    assert _version(client, _page('<span itemprop="softwareVersion">131</span>')) == "131"


@respx.mock
def test_latest_version_text_wins_over_a_changelog_on_the_same_page(client):
    markup = _page(
        "<p>Latest version: 2.4.1</p><h2>Changelog</h2>"
        "<h3>Version 2.4.1</h3><p>Fixes</p><h3>Version 2.4.0</h3><p>Features</p>"
    )
    assert _version(client, markup) == "2.4.1"


@pytest.mark.parametrize(
    "body, version",
    [
        ("<dl><dt>Version</dt><dd>1.2.3</dd><dt>Size</dt><dd>4.5 MB</dd></dl>", "1.2.3"),
        ("<p>Current version is v3.0.21 • macOS • 52 MB</p>", "3.0.21"),
        ("<p>Stable 2.4.1 | Beta 2.5.0-beta.2</p>", "2.4.1"),
        ("<p>Version 2.4.x is supported until 2027. Version 2.5.0 is out.</p>", "2.5.0"),
        ("<h3>Version 2.4.1 - 2026-10-29</h3>", "2.4.1"),
        ("<p>Latest release: 29.10.2026</p><p>Version 2.4.1</p>", "2.4.1"),
        ("<span>Saved to your Downloads folder</span> <span>Version 2.4.1</span>", "2.4.1"),
        ("<title>Lumen</title><p>Version 2.4.1</p>", "2.4.1"),
    ],
)
@respx.mock
def test_labelled_text_names_the_version(client, body, version):
    assert _version(client, _page(body)) == version


@pytest.mark.parametrize(
    "link, version",
    [
        ("/dl/Lumen%20Setup%202.4.1.exe", "2.4.1"),
        ("/dl/lumen_2.4.1_amd64.deb", "2.4.1"),
        ("/dl/node-v22.11.0-darwin-arm64.tar.gz", "22.11.0"),
        ("/dl/python-3.13.0-macos11.pkg", "3.13.0"),
        ("/dl/Lumen.2.4.1.zip", "2.4.1"),
        ("/download.php?file=releases/Lumen-2.4.1.dmg", "2.4.1"),
        ("/release/Blender4.2/blender-4.2.3-macos-arm64.dmg/", "4.2.3"),
    ],
)
@respx.mock
def test_a_version_is_read_from_a_download_file_name(client, link, version):
    assert _version(client, _page(f'<a href="{link}">Download</a>')) == version


@respx.mock
def test_a_prerelease_is_tagged(client):
    _serve(_page('<a href="/dl/Lumen-2.5.0-beta.1.dmg">Mac</a><a href="/dl/Lumen-2.5.0-beta.1.exe">Win</a>'))
    _, (update,) = _fetch(client)
    assert update.version == "2.5.0-beta.1"
    assert update.tags == ("release", "prerelease")


@respx.mock
def test_two_spellings_of_one_version_agree(client):
    markup = _page('<a href="/dl/Lumen-2.4.dmg">Mac</a><a href="/dl/Lumen-2.4.0.exe">Win</a>')
    assert _version(client, markup) == "2.4"


@respx.mock
def test_the_uid_does_not_depend_on_which_spelling_comes_first(client):
    mac, win = '<a href="/dl/Lumen-2.4.dmg">Mac</a>', '<a href="/dl/Lumen-Setup-2.4.0.exe">Win</a>'
    _serve(_page(mac + win))
    _, (first,) = _fetch(client)
    _serve(_page(win + mac))
    _, (second,) = _fetch(client)
    assert first.uid == second.uid == "version:2.4"


@respx.mock
def test_a_beta_written_with_a_space_is_a_prerelease(client):
    _serve(_page("<p>Version 3.0 Beta 2</p>"))
    _, (update,) = _fetch(client)
    assert update.version == "3.0 Beta 2"
    assert update.uid == "version:3-beta2"
    assert update.tags == ("release", "prerelease")


@respx.mock
def test_an_unreadable_link_does_not_hide_the_rest_of_the_page(client):
    markup = _page('<a href="http://[insert link here]">x</a><a href="/dl/Lumen-2.4.1.dmg">Mac</a>')
    assert _version(client, markup) == "2.4.1"


@respx.mock
def test_a_changelog_is_not_read_as_the_version(client):
    message = _refusal(client, _page("<h3>Version 2.4.1</h3><p>Fixes</p><h3>Version 2.4.0</h3>"))
    assert "more than one version" in message and "(2.4.1, 2.4.0)" in message
    assert "regex" in message


@respx.mock
def test_download_links_that_disagree_are_refused(client):
    markup = _page(
        '<a href="/dl/Lumen-2.4.1.dmg">Current</a><a href="/dl/Lumen-2.3.0.dmg">Previous</a>'
    )
    assert "download links (2.4.1, 2.3.0)" in _refusal(client, markup)


@respx.mock
def test_a_label_naming_another_version_than_the_download_links_is_refused(client):
    markup = _page(
        '<h2>Version 2.4.1</h2><a href="/dl/Lumen.dmg">Mac</a>'
        '<a href="/dl/Lumen-1.9.8.dmg">For macOS 10.15</a>'
    )
    assert "found 1.9.8 in the download links but 2.4.1" in _refusal(client, markup)


@respx.mock
def test_a_label_naming_the_series_agrees_with_its_release(client):
    markup = _page('<p>Version 3.2 is here</p><a href="/dl/gimp-3.2.6-setup.exe">Windows</a>')
    assert _version(client, markup) == "3.2.6"


@respx.mock
def test_a_release_does_not_vouch_for_a_series_read_as_the_version(client):
    message = _refusal(client, _page('<p>Version 3.2.6</p><span itemprop="softwareVersion">3.2</span>'))
    assert "found 3.2 in the page's structured data but 3.2.6" in message


@pytest.mark.parametrize(
    "body",
    [
        "<p>Users of version 2.3.0 should upgrade to version 2.4.1</p>",
        "<h3>Version 2.4.1 – 29.10.2026</h3><p>Fixes</p><h3>Version 2.4.0</h3>",
    ],
)
@respx.mock
def test_a_newer_version_beside_an_older_one_is_not_dropped(client, body):
    assert "more than one version" in _refusal(client, _page(body))


@respx.mock
def test_structured_data_and_download_links_naming_different_versions_are_refused(client):
    ld = '<script type="application/ld+json">{"@type": "SoftwareApplication", "softwareVersion": "2.3"}</script>'
    message = _refusal(client, _page('<a href="/dl/Lumen-2.4.1.dmg">Mac</a>', head=ld))
    assert "found 2.3 in the page's structured data but 2.4.1 in the download links" in message


@pytest.mark.parametrize(
    "body",
    [
        "<p>Requires macOS version 14.0 or later</p>",
        "<p>Supported version: 3.11</p>",
        "<p>Works with v14.0+</p>",
        "<p>Version 10.15 and earlier aren't supported</p>",
        "<p>Upgrading from version 1.9? Read this first.</p>",
        "<p>Versions 2.0 to 2.4 are affected</p><p>Version 2.0 - 2.4</p>",
        "<p>© 2019–2026 Lumen Labs. Python 3.11+. Built on Qt 6.7.</p>",
        '<a href="/dl/gcc-arm-10.3-2021.10-mac.pkg">gcc</a><a href="/dl/7z2408-x64.exe">7z</a>',
        '<a href="/docs/Lumen-Manual-1.0.pdf">Manual</a><a href="/releases/v2.4.1/">Notes</a>',
        "<h1>Lumen 2.4.1</h1><footer>Licensed under the Apache License, Version 2.0</footer>",
        "<p>Download 7-Zip 24.08</p><p>GNU LGPL v2.1</p>",
        "<p>Now built on Electron v31.2.0</p>",
        "<p>Tested on macOS version 14.6.1</p>",
        "<nav>Docs v2.4</nav><h1>Lumen 2.4.1</h1>",
        # date and float without trailing zero
        "<p>Latest release: 29.10.2026</p>",
        '<script type="application/ld+json">{"@type": "SoftwareApplication", "softwareVersion": 2.10}</script>',
        '<span itemprop="softwareVersion">' + "1" * 5000 + "</span>",
        '<link itemprop="softwareVersion" href="/v"><p>Requires macOS 14.0 or later</p>',
        "<p>If you are on version 2.3.0, update to 2.4.1.</p>",
        "<p>Version 2.4.1a</p>",
    ],
)
@respx.mock
def test_a_version_something_requires_or_a_page_never_names_is_not_taken(client, body):
    assert _refusal(client, _page(body)).startswith("no version found on the page")


@respx.mock
def test_a_page_without_a_version_points_to_its_feed(client):
    head = (
        '<link rel="alternate" type="application/rss+xml" title="Comments Feed" href="/comments/feed/">'
        '<link rel="alternate" type="application/atom+xml" href="/news.atom">'
    )
    message = _refusal(client, _page("<p>Lumen is a notes app.</p>", head=head))
    assert "youpdated add feed https://lumen.example/news.atom" in message


@respx.mock
def test_a_page_built_by_javascript_says_so(client):
    message = _refusal(client, _page('<div id="root"></div>', head='<script src="/app.js"></script>'))
    assert "JavaScript" in message


@respx.mock
def test_structured_data_alone_is_not_javascript(client):
    ld = '<script type="application/ld+json">{"@type": "Organization", "name": "Lumen"}</script>'
    assert "JavaScript" not in _refusal(client, _page("<p>A notes app.</p>", head=ld))


@respx.mock
def test_a_feed_at_the_address_is_pointed_to_the_feed_source(client):
    _serve('<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>', "application/rss+xml")
    with pytest.raises(FetchError, match=f"youpdated add feed {URL}"):
        _fetch(client)


@respx.mock
def test_json_at_the_address_is_not_a_page(client):
    _serve('{"version": "2.4.1"}', "application/json")
    with pytest.raises(FetchError, match="returned application/json, not a web page"):
        _fetch(client)


@respx.mock
def test_a_later_check_finding_the_version_elsewhere_fails(client, state):
    _serve(_page('<a href="/dl/Lumen-2.4.1.dmg">Mac</a>'))
    _fetch(client)
    assert state.cache_get(SIGNAL_CACHE, URL) == "downloads"

    message = _refusal(client, _page("<p>Version 3.11 of the plugin API</p>"))
    assert "earlier checks found the version in the download links" in message
    assert "though there's 3.11 in the page's \"version\" labels" in message
    assert f"youpdated rm --wipe webpage {URL}" in message


@respx.mock
def test_a_check_keeps_to_the_signal_it_found_first(client, state):
    _serve(_page('<p>Version 2.4.1</p>'))
    _fetch(client)
    assert state.cache_get(SIGNAL_CACHE, URL) == "label"

    # Download links agree
    _serve(_page('<p>Version 2.5.0</p><a href="/dl/Lumen-2.5.0.dmg">Mac</a>'))
    _, (update,) = _fetch(client)
    assert update.version == "2.5.0"
    assert "\"version\" labels" in (update.body or "")


@respx.mock
def test_wiping_a_target_forgets_where_its_version_was(client, state):
    _serve(_page('<a href="/dl/Lumen-2.4.1.dmg">Mac</a>'))
    _fetch(client)
    source = get_source("webpage")
    state.forget("webpage", [URL], getattr(source, "caches", ()))
    assert state.cache_get(SIGNAL_CACHE, URL) is None


# regex


@pytest.mark.parametrize(
    "regex, version",
    [
        (r"Download 7-Zip ([\d.]+)", "26.00"),
        (r"(?P<version>\d+\.\d+) \(", "26.00"),
        (r"\d+\.\d+(?= \()", "26.00"),
        (r'data-version="([^"]+)"', "26.00-rc1"),
    ],
)
@respx.mock
def test_a_regex_names_the_version_outright(client, regex, version):
    _serve(
        _page(
            '<p>Download 7-Zip 26.00 (2026-10-01)</p><span data-version="26.00-rc1"></span>'
            "<h3>Version 25.01</h3><h3>Version 24.09</h3>"
        )
    )
    _, (update,) = _fetch(client, {"url": URL, "regex": regex})
    assert update.version == version
    assert update.uid == ("version:26-rc1" if version.endswith("rc1") else "version:26")
    assert ("prerelease" in update.tags) == version.endswith("rc1")


@respx.mock
def test_a_regex_that_matches_nothing_fails(client, state):
    _serve(_page("<p>Version 2.4.1</p>"))
    with pytest.raises(FetchError, match="matched nothing"):
        _fetch(client, {"url": URL, "regex": r"Build (\d+)"})
    assert state.cache_get(SIGNAL_CACHE, URL) is None


# across runs


@respx.mock
def test_a_new_version_on_the_page_is_reported_once(state, client):
    config = parse_config({"sources": {"webpage": [URL]}})
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)

    _serve(fixture("webpage_download.html"))
    first = run(config, state, client)
    assert first.baseline and not first.errors

    _serve(fixture("webpage_download.html").replace(b"2.4.1", b"2.5.0"))
    second = run(config, state, client)
    assert [u.version for u in second.updates] == ["2.5.0"]

    third = run(config, state, client)
    assert third.updates == [] and not third.errors
