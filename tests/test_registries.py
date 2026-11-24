"""Package registry sources: crates, go, rubygems, packagist, nuget, maven"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from youpdated.edit import candidates
from youpdated.http import FetchError
from youpdated.sources.base import ConfigEntryError, highest, recent_versions
from youpdated.sources.packagist import expand

from .conftest import fixture, get_source


def _fetch(client, source_name, entry):
    source = get_source(source_name)
    (target,) = source.targets([entry])
    return target, list(source.fetch(target, client))


def _newest_first(updates):
    dated = [u.published for u in updates if u.published]
    return dated == sorted(dated, reverse=True)


# shared

def test_recent_versions_keeps_finals_behind_a_run_of_prereleases():
    ordered = [f"2.0.0-rc.{n}" for n in range(12, 0, -1)] + ["1.9.0", "1.8.0"]
    listed = recent_versions(ordered, lambda v: "-" in v, "1.9.0", limit=3)
    assert listed == {"2.0.0-rc.12", "2.0.0-rc.11", "2.0.0-rc.10", "1.9.0", "1.8.0"}


def test_highest_is_by_number_not_by_position():
    assert highest(["7.2.4", "8.1.4.1", "8.0.5"]) == "8.1.4.1"
    assert highest([]) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://crates.io/crates/serde", {"crates": "serde"}),
        ("https://docs.rs/tokio/latest/tokio/", {"crates": "tokio"}),
        ("https://pkg.go.dev/github.com/spf13/cobra@v1.10.2", {"go": "github.com/spf13/cobra"}),
        ("https://rubygems.org/gems/rails", {"rubygems": "rails"}),
        ("https://packagist.org/packages/monolog/monolog", {"packagist": "monolog/monolog"}),
        ("https://www.nuget.org/packages/Newtonsoft.Json/13.0.3", {"nuget": "Newtonsoft.Json"}),
        (
            "https://central.sonatype.com/artifact/com.google.guava/guava",
            {"maven": "com.google.guava:guava"},
        ),
        (
            "https://mvnrepository.com/artifact/com.google.guava/guava/33.0.0-jre",
            {"maven": "com.google.guava:guava"},
        ),
    ],
)
def test_registry_pages_are_claimed(value, expected):
    assert candidates(value) == expected


def test_a_bare_word_could_be_any_registry():
    assert list(candidates("express")) == [
        "crates", "homebrew", "modrinth", "npm", "nuget", "pypi", "rubygems",
    ]


# crates

CRATES_SERDE = "https://crates.io/api/v1/crates/serde"


@pytest.mark.parametrize("entry", ["serde", {"crate": "serde", "name": "Serde"}])
def test_crates_targets(entry):
    (target,) = get_source("crates").targets([entry])
    assert target.key == "serde"


@pytest.mark.parametrize("entry", ["serde_json", "serde-json", "Serde_JSON"])
def test_crates_spellings_crates_io_treats_alike_are_one_target(entry):
    (target,) = get_source("crates").targets([entry])
    assert target.key == "serde-json"
    assert target.label == entry


@respx.mock
def test_crates_title_and_url_use_the_crates_own_name(client):
    doc = json.loads(fixture("crates_serde.json"))
    doc["crate"]["name"] = "serde_json"
    respx.get("https://crates.io/api/v1/crates/serde-json").mock(
        return_value=httpx.Response(200, json=doc)
    )
    _, updates = _fetch(client, "crates", "Serde-JSON")
    assert updates[0].title == "serde_json 1.0.229"
    assert updates[0].url == "https://crates.io/crates/serde_json/1.0.229"


@pytest.mark.parametrize("entry", ["", "9lives", "has space", "a/b", {"name": "x"}])
def test_crates_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("crates").targets([entry])


@respx.mock
def test_crates_reports_newest_versions(client):
    respx.get(CRATES_SERDE).mock(return_value=httpx.Response(200, content=fixture("crates_serde.json")))
    target, updates = _fetch(client, "crates", "serde")

    assert len(updates) == 10
    assert updates[0].uid == "version:1.0.229"
    assert updates[0].tags == ("release", "latest")
    assert updates[0].url == "https://crates.io/crates/serde/1.0.229"
    assert updates[0].body == "A generic serialization/deserialization framework"
    assert _newest_first(updates)
    # uids are stable across fetches of the same payload
    assert [u.uid for u in _fetch(client, "crates", "serde")[1]] == [u.uid for u in updates]


@respx.mock
def test_crates_skips_yanked_and_tags_prereleases(client):
    doc = json.loads(fixture("crates_serde.json"))
    doc["versions"][0]["yanked"] = True
    doc["versions"][1]["num"] = "1.1.0-rc.1"
    respx.get(CRATES_SERDE).mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "crates", "serde")

    assert "version:1.0.229" not in {u.uid for u in updates}
    assert updates[0].version == "1.1.0-rc.1"
    assert "prerelease" in updates[0].tags


@respx.mock
def test_crates_unknown_crate_says_so(client):
    respx.get("https://crates.io/api/v1/crates/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no crate named `nope`"):
        _fetch(client, "crates", "nope")


# go

GO_BASE = "https://proxy.golang.org/github.com/spf13/cobra/@v"


def _go_info(request, route):
    version = request.url.path.rsplit("/", 1)[-1].removesuffix(".info")
    return httpx.Response(200, json={"Version": version, "Time": f"2025-01-{len(version):02d}T00:00:00Z"})


@pytest.mark.parametrize(
    "entry,key",
    [
        ("github.com/spf13/cobra", "github.com/spf13/cobra"),
        ({"module": "golang.org/x/net/"}, "golang.org/x/net"),
    ],
)
def test_go_targets(entry, key):
    (target,) = get_source("go").targets([entry])
    assert target.key == key


@pytest.mark.parametrize("entry", ["cobra", "github.com", "org.mozilla.firefox", "has space/x.y"])
def test_go_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("go").targets([entry])


@respx.mock
def test_go_lists_the_highest_versions_with_their_times(client):
    respx.get(f"{GO_BASE}/list").mock(return_value=httpx.Response(200, content=fixture("go_cobra_list.txt")))
    info = respx.get(url__regex=rf"{GO_BASE}/v.*\.info").mock(side_effect=_go_info)
    _, updates = _fetch(client, "go", "github.com/spf13/cobra")

    assert [u.version for u in updates][:3] == ["v1.10.2", "v1.10.1", "v1.10.0"]
    assert len(updates) == 10
    assert updates[0].tags == ("release", "latest")
    assert updates[0].url == "https://pkg.go.dev/github.com/spf13/cobra@v1.10.2"
    assert all(u.published for u in updates)
    assert info.call_count == 10

    # times never change
    _fetch(client, "go", "github.com/spf13/cobra")
    assert info.call_count == 10


@respx.mock
def test_go_escapes_capital_letters(client):
    route = respx.get("https://proxy.golang.org/github.com/!burnt!sushi/toml/@v/list").mock(
        return_value=httpx.Response(200, text="v1.0.0-beta.1\n")
    )
    respx.get(url__regex=r".*\.info$").mock(side_effect=_go_info)
    _, updates = _fetch(client, "go", "github.com/BurntSushi/toml")
    assert route.called
    assert updates[0].tags == ("release", "prerelease", "latest")


@respx.mock
def test_go_untagged_module_reports_its_pseudo_version(client):
    respx.get(f"{GO_BASE}/list").mock(return_value=httpx.Response(200, text=""))
    respx.get("https://proxy.golang.org/github.com/spf13/cobra/@latest").mock(
        return_value=httpx.Response(
            200, json={"Version": "v0.0.0-20251203235115-88b30ab89da2", "Time": "2025-12-03T23:51:15Z"}
        )
    )
    _, (update,) = _fetch(client, "go", "github.com/spf13/cobra")
    assert update.tags == ("prerelease",)
    assert update.version.startswith("v0.0.0-2025")


@respx.mock
def test_go_unknown_module_says_so(client):
    respx.get(f"{GO_BASE}/list").mock(return_value=httpx.Response(410, text="not found"))
    with pytest.raises(FetchError, match="no module named `github.com/spf13/cobra`"):
        _fetch(client, "go", "github.com/spf13/cobra")


# rubygems

GEMS_NOKOGIRI = "https://rubygems.org/api/v1/versions/nokogiri.json"


@pytest.mark.parametrize("entry", ["nokogiri", {"gem": "nokogiri"}])
def test_rubygems_targets(entry):
    (target,) = get_source("rubygems").targets([entry])
    assert target.key == "nokogiri"


@respx.mock
def test_rubygems_lists_each_version_once_across_platforms(client):
    respx.get(GEMS_NOKOGIRI).mock(return_value=httpx.Response(200, content=fixture("rubygems_nokogiri.json")))
    _, updates = _fetch(client, "rubygems", "nokogiri")

    uids = [u.uid for u in updates]
    assert len(uids) == len(set(uids))
    assert updates[0].version == "1.19.7"
    assert "latest" in updates[0].tags
    assert _newest_first(updates)


@respx.mock
def test_rubygems_tags_prereleases(client):
    doc = json.loads(fixture("rubygems_nokogiri.json"))
    doc.insert(0, {**doc[0], "number": "1.20.0.rc1", "prerelease": True, "created_at": "2099-01-01T00:00:00Z"})
    respx.get(GEMS_NOKOGIRI).mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "rubygems", "nokogiri")
    assert updates[0].version == "1.20.0.rc1"
    assert updates[0].tags == ("release", "prerelease")


@respx.mock
def test_rubygems_latest_is_the_highest_version(client):
    doc = json.loads(fixture("rubygems_nokogiri.json"))
    doc.insert(0, {**doc[0], "number": "1.18.99", "created_at": "2099-01-01T00:00:00Z"})
    respx.get(GEMS_NOKOGIRI).mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "rubygems", "nokogiri")
    assert updates[0].version == "1.18.99"
    assert [u.version for u in updates if "latest" in u.tags] == ["1.19.7"]


@respx.mock
def test_rubygems_unknown_gem_says_so(client):
    respx.get("https://rubygems.org/api/v1/versions/nope.json").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no gem named `nope`"):
        _fetch(client, "rubygems", "nope")


# packagist

PACKAGIST_MONOLOG = "https://repo.packagist.org/p2/monolog/monolog.json"


def test_packagist_expands_minified_metadata():
    expanded = expand([{"version": "2.0", "a": 1, "b": 2}, {"version": "1.0", "b": "__unset"}])
    assert expanded == [{"version": "2.0", "a": 1, "b": 2}, {"version": "1.0", "a": 1}]


@pytest.mark.parametrize("entry", ["monolog/monolog", {"package": "Monolog/Monolog"}])
def test_packagist_targets(entry):
    (target,) = get_source("packagist").targets([entry])
    assert target.key == "monolog/monolog"


@pytest.mark.parametrize("entry", ["monolog", "a/b/c", "/monolog"])
def test_packagist_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError, match="vendor/package"):
        get_source("packagist").targets([entry])


@respx.mock
def test_packagist_reports_newest_versions(client):
    respx.get(PACKAGIST_MONOLOG).mock(return_value=httpx.Response(200, content=fixture("packagist_monolog.json")))
    _, updates = _fetch(client, "packagist", "monolog/monolog")

    assert updates[0].uid == "version:3.13.0"
    assert updates[0].tags == ("release", "latest")
    assert _newest_first(updates)
    assert all(u.body and u.body.startswith("Sends your logs") for u in updates)


@respx.mock
def test_packagist_unknown_package_says_so(client):
    respx.get("https://repo.packagist.org/p2/no/pe.json").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no package named `no/pe`"):
        _fetch(client, "packagist", "no/pe")


# nuget

NUGET = "https://api.nuget.org/v3/registration5-gz-semver2"


@pytest.mark.parametrize("entry", ["Newtonsoft.Json", {"package": "newtonsoft.json"}])
def test_nuget_targets_lowercase_the_id(entry):
    (target,) = get_source("nuget").targets([entry])
    assert target.key == "newtonsoft.json"


@respx.mock
def test_nuget_reads_inline_pages(client):
    respx.get(f"{NUGET}/newtonsoft.json/index.json").mock(
        return_value=httpx.Response(200, content=fixture("nuget_newtonsoft.json"))
    )
    _, updates = _fetch(client, "nuget", "Newtonsoft.Json")

    assert updates[0].version == "14.0.1-beta2"
    assert updates[0].tags == ("release", "prerelease")
    assert [u.version for u in updates if "latest" in u.tags] == ["13.0.4"]
    assert updates[0].url == "https://www.nuget.org/packages/Newtonsoft.Json/14.0.1-beta2"
    assert _newest_first(updates)


@respx.mock
def test_nuget_fetches_linked_pages_newest_first(client):
    respx.get(f"{NUGET}/microsoft.extensions.logging/index.json").mock(
        return_value=httpx.Response(200, content=fixture("nuget_logging_index.json"))
    )
    pages = f"{NUGET}/microsoft.extensions.logging/page"
    newest = respx.get(f"{pages}/9.0.1/11.0.0.json").mock(
        return_value=httpx.Response(200, content=fixture("nuget_logging_page.json"))
    )
    older_page = {
        "items": [
            {"catalogEntry": {"version": f"9.0.{n}", "published": f"2025-0{n}-01T00:00:00Z"}}
            for n in range(1, 9)
        ]
    }
    older = respx.get(f"{pages}/3.1.24/9.0.0.json").mock(
        return_value=httpx.Response(200, json=older_page)
    )
    oldest = respx.get(url__regex=rf"{pages}/1\..*").mock(return_value=httpx.Response(500))
    _, updates = _fetch(client, "nuget", "Microsoft.Extensions.Logging")
    assert newest.called and older.called
    assert not oldest.called
    assert updates and all(u.version.startswith(("9.", "10.", "11.")) for u in updates)


@respx.mock
def test_nuget_skips_unlisted_versions(client):
    doc = json.loads(fixture("nuget_newtonsoft.json"))
    doc["items"][-1]["items"][-1]["catalogEntry"]["listed"] = False
    respx.get(f"{NUGET}/newtonsoft.json/index.json").mock(return_value=httpx.Response(200, json=doc))
    _, updates = _fetch(client, "nuget", "Newtonsoft.Json")
    assert "version:14.0.1-beta2" not in {u.uid for u in updates}


@respx.mock
def test_nuget_unknown_package_says_so(client):
    respx.get(f"{NUGET}/nope/index.json").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no package named `nope`"):
        _fetch(client, "nuget", "nope")


# maven

GUAVA = "https://repo1.maven.org/maven2/com/google/guava/guava/maven-metadata.xml"


@pytest.mark.parametrize(
    "entry",
    [
        "com.google.guava:guava",
        "com.google.guava:guava:33.0.0-jre",
        {"group": "com.google.guava", "artifact": "guava"},
    ],
)
def test_maven_targets(entry):
    (target,) = get_source("maven").targets([entry])
    assert target.key == "com.google.guava:guava"


@pytest.mark.parametrize(
    "entry",
    ["guava", {"group": "com.google.guava"}, {"package": "a:b", "repository": "ftp://x"}],
)
def test_maven_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("maven").targets([entry])


@respx.mock
def test_maven_reports_the_last_deployed_versions(client):
    respx.get(GUAVA).mock(return_value=httpx.Response(200, content=fixture("maven_guava.xml")))
    _, updates = _fetch(client, "maven", "com.google.guava:guava")

    assert updates[0].version == "33.8.0-jre"
    assert updates[0].tags == ("release", "latest")
    assert updates[0].published is None
    assert updates[0].url == "https://central.sonatype.com/artifact/com.google.guava/guava/33.8.0-jre"
    assert len(updates) == 10


@respx.mock
def test_maven_skips_snapshots_and_tags_prereleases(client):
    xml = fixture("maven_guava.xml").decode().replace(
        "</versions>", "<version>34.0.0-rc1</version><version>34.1.0-SNAPSHOT</version></versions>"
    )
    respx.get(GUAVA).mock(return_value=httpx.Response(200, text=xml))
    _, updates = _fetch(client, "maven", "com.google.guava:guava")
    assert updates[0].version == "34.0.0-rc1"
    assert "prerelease" in updates[0].tags
    assert not any("SNAPSHOT" in u.version for u in updates)


@respx.mock
def test_maven_latest_is_the_highest_final_when_release_is_a_milestone(client):
    # `<release>` is the last non-snapshot deployed, which JUnit and androidx make a milestone
    xml = fixture("maven_guava.xml").decode().replace(
        "</versions>", "<version>34.0.0-M1</version></versions>"
    ).replace("<release>33.8.0-jre</release>", "<release>34.0.0-M1</release>")
    respx.get(GUAVA).mock(return_value=httpx.Response(200, text=xml))
    _, updates = _fetch(client, "maven", "com.google.guava:guava")
    assert updates[0].version == "34.0.0-M1"
    assert updates[0].tags == ("release", "prerelease")
    assert [u.version for u in updates if "latest" in u.tags] == ["33.8.0-jre"]


@respx.mock
def test_maven_reads_another_repository(client):
    route = respx.get(
        "https://dl.google.com/android/maven2/androidx/core/core/maven-metadata.xml"
    ).mock(return_value=httpx.Response(200, content=fixture("maven_guava.xml")))
    _, updates = _fetch(
        client, "maven",
        {"package": "androidx.core:core", "repository": "https://dl.google.com/android/maven2/"},
    )
    assert route.called
    assert updates[0].url == "https://dl.google.com/android/maven2/androidx/core/core/33.8.0-jre/"


@respx.mock
def test_maven_unknown_artifact_says_so(client):
    respx.get(GUAVA).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no artifact `com.google.guava:guava`"):
        _fetch(client, "maven", "com.google.guava:guava")
