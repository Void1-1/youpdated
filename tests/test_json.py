"""`json` source"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from youpdated.config import PrivacyConfig, parse_config
from youpdated.edit import guess_source
from youpdated.http import FetchError
from youpdated.runner import run
from youpdated.sources.base import ConfigEntryError
from youpdated.sources.jsondoc import EACH, PATH_CACHE, parse_path, show_path

from .conftest import fixture, get_source

URL = "https://lumen.example/updates/latest.json"
RELEASES = {"url": URL, "items": "releases[*]", "version": "tag_name"}


def _serve(doc: Any, content_type: str = "application/json") -> None:
    if isinstance(doc, bytes):
        content = doc
    elif isinstance(doc, str):
        content = doc.encode()
    else:
        content = json.dumps(doc).encode()
    respx.get(URL).mock(
        return_value=httpx.Response(200, content=content, headers={"Content-Type": content_type})
    )


def _fetch(client, entry: str | dict[str, Any] = URL):
    source = get_source("json")
    (target,) = source.targets([entry])
    return target, list(source.fetch(target, client))


def _version(client, doc: Any, **options: Any) -> str | None:
    _serve(doc)
    _, (update,) = _fetch(client, {"url": URL, **options})
    return update.version


def _versions(client, doc: Any, **options: Any) -> list[str | None]:
    _serve(doc)
    _, updates = _fetch(client, {"url": URL, **options})
    return [update.version for update in updates]


def _refusal(client, doc: Any, **options: Any) -> str:
    _serve(doc)
    with pytest.raises(FetchError) as caught:
        _fetch(client, {"url": URL, **options})
    return str(caught.value)


# paths


@pytest.mark.parametrize(
    "text, steps",
    [
        ("version", ("version",)),
        ("latest.release", ("latest", "release")),
        (".latest.release", ("latest", "release")),
        ("$.latest.release", ("latest", "release")),
        ("releases[0].tag_name", ("releases", 0, "tag_name")),
        (".[0].tag_name", (0, "tag_name")),
        ("builds[-1]", ("builds", -1)),
        ('packages["com.lumen.notes"].version', ("packages", "com.lumen.notes", "version")),
        ("release date", ("release date",)),
        ("releases[*]", ("releases", EACH)),
        (".", ()),
    ],
)
def test_a_path_is_read_and_written_back_the_same(text, steps):
    assert parse_path(text) == steps
    assert parse_path(show_path(steps)) == steps


@pytest.mark.parametrize("text", ["a..b", "a.", "a[", "a[x]", "a[1.5]"])
def test_a_path_that_cannot_be_read_is_refused(text):
    with pytest.raises(ValueError, match="can't read"):
        parse_path(text)


# config


def test_both_config_shapes_give_the_same_key():
    source = get_source("json")
    bare, mapped = source.targets([URL, {"url": URL, "name": "Lumen"}])
    assert bare.key == mapped.key == URL
    assert mapped.label == "Lumen"


@pytest.mark.parametrize("url", ["lumen.example/latest.json", "ftp://lumen.example/x", "https://"])
def test_a_value_that_is_not_a_document_url_is_refused(url):
    with pytest.raises(ConfigEntryError, match="not an http"):
        get_source("json").targets([url])


@pytest.mark.parametrize(
    "options, message",
    [
        ({"version": "a..b"}, "`version` for .* is not a path"),
        ({"items": 3}, "`items` for .* must be a path"),
        ({"published": ""}, "`published` for .* must be a path"),
        ({"version": "releases[*].tag_name"}, r"`version` for .* can't hold `\[\*\]`"),
        ({"limit": "many"}, "`limit` for .* must be a number"),
    ],
)
def test_a_bad_option_is_refused_before_anything_is_fetched(options, message):
    with pytest.raises(ConfigEntryError, match=message):
        get_source("json").targets([{"url": URL, **options}])


def test_a_document_address_is_a_json_entry():
    assert guess_source(URL) == ("json", URL)
    assert guess_source("https://lumen.example/latest-mac.yml")[0] == "json"
    assert guess_source("https://lumen.example/api/releases")[0] == "webpage"
    assert guess_source("https://blog.example.com/feed.xml")[0] == "feed"


@respx.mock
def test_an_update_file_names_its_version(client):
    _serve(fixture("json_latest.yml"), "text/yaml")
    target, (update,) = _fetch(client)

    assert update.version == "2.4.1"
    assert update.uid == "version:2.4.1"
    assert update.title == "2.4.1"
    assert update.url == URL
    assert update.tags == ("release",)
    assert update.body == "Read from `version` in the document"
    assert target.label is None


@respx.mock
def test_the_other_paths_fill_in_the_update(client):
    _serve(fixture("json_latest.yml"), "text/yaml")
    _, (update,) = _fetch(
        client, {"url": URL, "published": "releaseDate", "link": "path", "title": "files[0].url"}
    )

    assert update.published == datetime(2026, 10, 29, 14, 3, 11, 482000, tzinfo=timezone.utc)
    assert update.url == "https://lumen.example/updates/Lumen-Setup-2.4.1.exe"
    assert update.title == "Lumen-Setup-2.4.1.exe"


@respx.mock
def test_a_link_can_be_an_address_instead_of_a_path(client):
    _serve({"version": "2.4.1"})
    _, (update,) = _fetch(client, {"url": URL, "link": "https://lumen.example/download"})
    assert update.url == "https://lumen.example/download"


@respx.mock
@pytest.mark.parametrize("link", ["notes", "missing", "script"])
def test_a_link_that_is_not_an_address_falls_back_to_the_document(client, link):
    _serve({"version": "2.4.1", "notes": {"en": "Fixes"}, "script": "javascript:alert(1)"})
    _, (update,) = _fetch(client, {"url": URL, "link": link})
    assert update.url == URL


@respx.mock
@pytest.mark.parametrize(
    "doc",
    [
        {"name": "Lumen", "version": "2.4.1", "platforms": {"mac": {"url": "/dl/mac"}}},
        {"app": {"release": {"tag_name": "2.4.1", "draft": False}}},
        {"latest": "2.4.1", "channels": ["stable"]},
        {"Version": "2.4.1"},
        {"version": "2.4.1", "current": {"latest": "2.4.1"}},
        # not versions: a flag, a word, a mapping
        {"version": "2.4.1", "release": {"latest": True, "tag_name": "stable", "version": {}}},
        {"productVersion": "2.4.0", "version": "69047191fc427f2f92828279aae1d90d8eeda7ab",
         "latest": "2.4.1"},
    ],
)
def test_a_lone_version_key_is_found_anywhere(client, doc):
    assert _version(client, doc) == "2.4.1"


@respx.mock
def test_more_than_one_version_is_refused_with_their_paths(client):
    message = _refusal(client, {"version": 3, "app": {"version": "2.4.1"}})
    assert "found multiple versions" in message
    assert "`version` is 3, `app.version` is 2.4.1" in message
    assert "items:" not in message


@respx.mock
def test_a_list_of_versions_is_pointed_to_items(client):
    message = _refusal(client, [{"tag_name": "v2.4.1"}, {"tag_name": "v2.4.0"}])
    assert "`.[0].tag_name` is v2.4.1" in message
    assert "or `items: .` to watch every entry of the list" in message

    message = _refusal(client, {"data": {"releases": [{"version": "1"}, {"version": "2"}]}})
    assert "or `items: data.releases` to watch" in message


@respx.mock
def test_no_version_key_is_refused_with_what_the_document_holds(client):
    message = _refusal(client, {"latest": {"release": "1.21.4"}, "versions": [{"id": "1.21.4"}]})
    assert "no `version`, `tag_name`, or `latest` key has a version" in message
    assert "a mapping with the keys `latest`, `versions`" in message


@respx.mock
@pytest.mark.parametrize(
    "path, version",
    [
        ("latest.release", "1.21.4"),
        ("versions[0].id", "1.21.4"),
        ("versions[-1].id", "24w51a"),
        ('["odd.key"]', "7"),
        ("build", "4127"),
    ],
)
def test_a_version_path_says_where_the_version_is(client, path, version):
    doc = {
        "latest": {"release": "1.21.4", "snapshot": "24w51a"},
        "versions": [{"id": "1.21.4"}, {"id": "24w51a"}],
        "odd.key": "7",
        "build": 4127,
        "version": "9.9.9",
    }
    _serve(doc)
    _, (update,) = _fetch(client, {"url": URL, "version": path})
    assert update.version == version
    assert update.body is None


@respx.mock
@pytest.mark.parametrize("path", ["missing", "latest", "versions[5].id", "flag", "latest.release.x"])
def test_a_version_path_that_holds_no_version_fails(client, path):
    doc = {"latest": {"release": "1.21.4"}, "versions": [{"id": "1.21.4"}], "flag": True}
    message = _refusal(client, doc, version=path)
    assert f"`version: {path}` is" in message and "not a version" in message


@respx.mock
def test_a_version_written_as_a_number_keeps_its_digits(client):
    assert _version(client, '{"version": 2.10}') == "2.10"
    assert _version(client, "version: 1.10\npath: Lumen-1.10.exe\n") == "1.10"
    assert _version(client, {"version": 7}) == "7"


@respx.mock
@pytest.mark.parametrize(
    "version, prerelease",
    [("2.5.0-beta.1", True), ("3.13.0rc1", True), ("2.5.0", False), ("v2.5.0", False)],
)
def test_a_prerelease_marker_is_tagged(client, version, prerelease):
    _serve({"version": version})
    _, (update,) = _fetch(client)
    assert ("prerelease" in update.tags) == prerelease
    assert update.uid == f"version:{version}"


@respx.mock
@pytest.mark.parametrize(
    "written, expected",
    [
        ("2026-10-29T14:03:11Z", datetime(2026, 10, 29, 14, 3, 11, tzinfo=timezone.utc)),
        ("2026-10-29T16:03:11+02:00", datetime(2026, 10, 29, 14, 3, 11, tzinfo=timezone.utc)),
        ("2026-10-29", datetime(2026, 10, 29, tzinfo=timezone.utc)),
        ("Thu, 29 Oct 2026 14:03:11 GMT", datetime(2026, 10, 29, 14, 3, 11, tzinfo=timezone.utc)),
        (1793282591, datetime(2026, 10, 29, 14, 3, 11, tzinfo=timezone.utc)),
        (1793282591000, datetime(2026, 10, 29, 14, 3, 11, tzinfo=timezone.utc)),
        ("soon", None),
        (True, None),
        (["2026-10-29"], None),
    ],
)
def test_a_date_is_read_however_it_is_written(client, written, expected):
    _serve({"version": "2.4.1", "date": written})
    _, (update,) = _fetch(client, {"url": URL, "published": "date"})
    assert update.published == expected


@respx.mock
def test_each_entry_of_a_list_is_an_update_newest_first(client):
    _serve(fixture("json_releases.json"))
    entry = {**RELEASES, "link": "html_url", "published": "published_at", "title": "name"}
    _, updates = _fetch(client, entry)

    assert [u.version for u in updates] == ["v2.5.0-beta.1", "v2.4.1", "v2.4.0", "v2.3.0"]
    assert [u.uid for u in updates][:2] == ["version:v2.5.0-beta.1", "version:v2.4.1"]
    assert [u.title for u in updates][:2] == ["Lumen 2.5 beta", "Lumen 2.4.1"]
    assert updates[0].url == "https://lumen.example/releases/v2.5.0-beta.1"
    assert updates[1].url == "https://downloads.lumen.example/2.4.1/"
    assert updates[0].published == datetime(2026, 11, 2, 16, 45, tzinfo=timezone.utc)
    assert updates[0].tags == ("release", "prerelease")
    assert updates[1].tags == ("release",)
    assert all(u.body is None for u in updates)


@respx.mock
def test_a_list_without_dates_is_ordered_by_version(client):
    _serve(fixture("json_releases.json"))
    _, updates = _fetch(client, RELEASES)
    assert [u.version for u in updates] == ["v2.5.0-beta.1", "v2.4.1", "v2.4.0", "v2.3.0"]


@respx.mock
def test_only_the_newest_of_a_long_list_are_read(client):
    doc = [{"version": f"1.{n}.0"} for n in range(50)]
    assert _versions(client, doc, items=".", limit=3) == ["1.49.0", "1.48.0", "1.47.0"]
    assert len(_versions(client, doc, items=".")) == 20


@respx.mock
@pytest.mark.parametrize("items", ["releases", "releases[*]", ".releases", "$.releases[*]"])
def test_items_names_a_list_with_or_without_the_star(client, items):
    _serve(fixture("json_releases.json"))
    _, updates = _fetch(client, {"url": URL, "items": items})
    assert {u.version for u in updates} == {"v2.5.0-beta.1", "v2.4.1", "v2.4.0", "v2.3.0"}


@respx.mock
def test_a_star_mid_path_reads_every_list_it_meets(client):
    doc = {
        "channels": [
            {"name": "stable", "builds": [{"version": "2.4.1"}, {"version": "2.4.0"}]},
            {"name": "beta", "builds": [{"version": "2.5.0-beta.1"}]},
        ]
    }
    versions = _versions(client, doc, items="channels[*].builds")
    assert versions == ["2.5.0-beta.1", "2.4.1", "2.4.0"]


@respx.mock
def test_a_mapping_is_read_as_version_to_details(client):
    doc = {"releases": {"2.4.0": "2026-10-01", "2.4.1": "2026-10-29", "2.3.0": "2026-08-14"}}
    _serve(doc)
    _, updates = _fetch(client, {"url": URL, "items": "releases", "published": "."})
    assert [u.version for u in updates] == ["2.4.1", "2.4.0", "2.3.0"]
    assert updates[0].published == datetime(2026, 10, 29, tzinfo=timezone.utc)

    # an entry's version before its key
    doc = {"lumen-2.4.1": {"version": "2.4.1"}, "lumen-2.4.0": {"version": "2.4.0"}}
    assert _versions(client, doc, items=".") == ["2.4.1", "2.4.0"]


@respx.mock
def test_a_list_of_plain_values_is_a_list_of_versions(client):
    doc = {"available": [8, 11, 17, 21, 25], "names": ["mac", "windows"]}
    assert _versions(client, doc, items="available") == ["25", "21", "17", "11", "8"]
    assert "found no version in the 2 entries of `items: names`" in _refusal(
        client, doc, items="names"
    )


@respx.mock
def test_a_version_listed_twice_is_one_update(client):
    doc = [
        {"version": "2.4.1", "os": "mac", "url": "/dl/mac"},
        {"version": "2.4.1", "os": "windows", "url": "/dl/windows"},
        {"version": "2.4.0", "os": "mac", "url": "/dl/old"},
    ]
    _serve(doc)
    _, updates = _fetch(client, {"url": URL, "items": ".", "link": "url"})
    assert [(u.version, u.url) for u in updates] == [
        ("2.4.1", "https://lumen.example/dl/mac"),
        ("2.4.0", "https://lumen.example/dl/old"),
    ]


@respx.mock
def test_an_entry_without_the_version_is_left_out(client):
    doc = {"releases": [{"tag_name": "v2.4.1"}, {"draft": True}, "v2.3.0", {"tag_name": None}]}
    assert _versions(client, doc, **{k: v for k, v in RELEASES.items() if k != "url"}) == ["v2.4.1"]


@respx.mock
def test_an_empty_list_has_nothing_in_it(client):
    assert _versions(client, {"releases": []}, items="releases") == []


@respx.mock
@pytest.mark.parametrize(
    "items, version, message",
    [
        ("builds", None, "`items: builds` isn't in the document, which is a mapping with the keys"),
        ("product", None, "`items: product` is 'Lumen Notes', not a list"),
        ("releases", "version", "found nothing at `version: version` in the 4 entries"),
        ("releases[0]", None, "found no version in the 4 entries of `items: releases[0]`"),
    ],
)
def test_items_that_lead_nowhere_fail(client, items, version, message):
    options = {"items": items} | ({"version": version} if version else {})
    assert message in _refusal(client, fixture("json_releases.json"), **options)


@respx.mock
def test_an_entry_with_more_than_one_version_is_refused(client):
    doc = [{"version": "2.4.1", "engine": {"version": "31.2.0"}}]
    message = _refusal(client, doc, items=".")
    assert "an entry of `items: .` has multiple versions" in message
    assert "`version` is 2.4.1, `engine.version` is 31.2.0" in message
    assert _versions(client, doc, items=".", version="version") == ["2.4.1"]


@respx.mock
def test_uids_are_the_same_on_every_fetch(client):
    _serve(fixture("json_releases.json"))
    _, first = _fetch(client, RELEASES)
    _, second = _fetch(client, RELEASES)
    assert [u.uid for u in first] == [u.uid for u in second]


@respx.mock
def test_a_web_page_at_the_address_is_pointed_to_the_webpage_source(client):
    _serve("<!doctype html><html><body>Version 2.4.1</body></html>", "text/html")
    with pytest.raises(FetchError, match=f"youpdated add webpage {URL}"):
        _fetch(client)


@respx.mock
def test_a_feed_at_the_address_is_pointed_to_the_feed_source(client):
    _serve('<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>', "text/xml")
    with pytest.raises(FetchError, match=f"youpdated add feed {URL}"):
        _fetch(client)


@respx.mock
@pytest.mark.parametrize("body", ["2.4.1", "", "Not found", "{version: [2.4.1", "\x00\x01\x02"])
def test_anything_else_is_neither_json_nor_yaml(client, body):
    _serve(body, "text/plain")
    with pytest.raises(FetchError, match="neither JSON nor YAML"):
        _fetch(client)


@respx.mock
def test_a_json_address_under_webpage_is_pointed_here(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"version": "2.4.1"}))
    source = get_source("webpage")
    (target,) = source.targets([URL])
    with pytest.raises(FetchError, match=f"not a web page: youpdated add json {URL}"):
        list(source.fetch(target, client))


@respx.mock
def test_a_yaml_document_that_repeats_itself_is_read_once(client):
    # each line holds the one before twice: 2**40 if read
    lines = ['a0: &a0 {version: "2.4.1"}']
    lines += [f"a{n}: &a{n} [*a{n - 1}, *a{n - 1}]" for n in range(1, 41)]
    assert _version(client, "\n".join(lines)) == "2.4.1"


@respx.mock
def test_a_yaml_document_that_holds_itself_is_refused(client):
    _serve("version: 2.4.1\nself: &a\n  again: *a\n", "text/yaml")
    with pytest.raises(FetchError, match="neither JSON nor YAML"):
        _fetch(client)


@respx.mock
def test_where_the_version_was_found_is_remembered(client, state):
    _serve({"version": "2.4.1"})
    _fetch(client)
    assert state.cache_get(PATH_CACHE, URL) == "version"

    # a version key elsewhere doesn't make the document unclear
    assert _version(client, {"version": "2.5.0", "engine": {"version": "31.2.0"}}) == "2.5.0"

    message = _refusal(client, {"schema": {"version": "2"}, "app": {"release": "2.6.0"}})
    assert "earlier checks read the version at `version`, which has moved" in message
    assert "though `schema.version` is 2" in message
    assert f"youpdated rm --wipe json {URL}" in message

    assert _version(client, {"schema": {"version": "2"}, "app": {"release": "2.6.0"}},
                    version="app.release") == "2.6.0"


@respx.mock
def test_wiping_the_target_forgets_where_its_version_was(client, state):
    _serve({"version": "2.4.1"})
    _fetch(client)
    source = get_source("json")
    state.forget("json", [URL], getattr(source, "caches", ()))
    assert state.cache_get(PATH_CACHE, URL) is None

    assert _version(client, {"app": {"version": "2.6.0"}}) == "2.6.0"
    assert state.cache_get(PATH_CACHE, URL) == "app.version"


@respx.mock
def test_a_refused_document_remembers_nothing(client, state):
    _refusal(client, {"version": 3, "app": {"version": "2.4.1"}})
    assert state.cache_get(PATH_CACHE, URL) is None
    _version(client, {"version": 3, "app": {"version": "2.4.1"}}, version="app.version")
    assert state.cache_get(PATH_CACHE, URL) is None


@respx.mock
def test_a_new_version_in_the_document_is_reported_once(state, client):
    config = parse_config({"sources": {"json": [URL]}})
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)

    _serve(fixture("json_latest.yml"))
    first = run(config, state, client)
    assert first.baseline and not first.errors

    _serve(fixture("json_latest.yml").replace(b"2.4.1", b"2.5.0"))
    second = run(config, state, client)
    assert [u.version for u in second.updates] == ["2.5.0"]

    third = run(config, state, client)
    assert third.updates == [] and not third.errors


@respx.mock
def test_a_new_entry_in_a_list_is_reported_once(state, client):
    entry = {**RELEASES, "published": "published_at", "versions": {"match": "v*"}}
    config = parse_config({"sources": {"json": [entry]}, "ignore": ["prerelease"]})
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    doc = json.loads(fixture("json_releases.json"))

    _serve(doc)
    first = run(config, state, client)
    assert first.baseline and not first.errors

    doc["releases"].insert(0, {"tag_name": "v2.5.0", "published_at": "2026-11-10T10:00:00Z"})
    doc["releases"].append({"tag_name": "v2.6.0-rc1", "published_at": "2026-11-12T10:00:00Z"})
    _serve(doc)
    second = run(config, state, client)
    assert [u.version for u in second.updates] == ["v2.5.0"]

    third = run(config, state, client)
    assert third.updates == [] and not third.errors
