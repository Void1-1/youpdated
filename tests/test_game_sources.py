"""Game sources: modrinth, gog"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from youpdated import preview
from youpdated.edit import candidates
from youpdated.http import FetchError
from youpdated.sources.base import ConfigEntryError

from .conftest import fixture, get_source


def _fetch(client, source_name, entry):
    source = get_source(source_name)
    (target,) = source.targets([entry])
    return target, list(source.fetch(target, client))


# modrinth

MODRINTH = "https://api.modrinth.com/v2/project/sodium"


def _mock_modrinth(versions=None):
    route = respx.get(f"{MODRINTH}/version").mock(
        return_value=httpx.Response(
            200, **({"json": versions} if versions is not None else {"content": fixture("modrinth_sodium.json")})
        )
    )
    project = respx.get(MODRINTH).mock(
        return_value=httpx.Response(200, content=fixture("modrinth_sodium_project.json"))
    )
    return route, project


@pytest.mark.parametrize(
    "value",
    ["https://modrinth.com/mod/sodium", "https://modrinth.com/mod/sodium/versions"],
)
def test_modrinth_pages_are_claimed(value):
    assert candidates(value) == {"modrinth": "sodium"}


def test_modrinth_targets_take_filters():
    (target,) = get_source("modrinth").targets(
        [{"project": "Sodium", "loaders": "fabric", "game_versions": ["1.21.1", "1.20"]}]
    )
    assert target.key == "sodium"
    assert target.params == {"loaders": ["fabric"], "game_versions": ["1.21.1", "1.20"]}


@pytest.mark.parametrize("entry", ["ab", {"project": "sodium", "loaders": {"a": 1}}])
def test_modrinth_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("modrinth").targets([entry])


@respx.mock
def test_modrinth_reports_versions_by_id(client):
    _mock_modrinth()
    target, updates = _fetch(client, "modrinth", "sodium")

    first = updates[0]
    assert first.uid == "version:6etTPjD5"
    assert first.title == "Sodium 0.9.4-beta.1 for Fabric 26.3"
    assert first.version == "mc26.3-0.9.4-beta.1-fabric"
    assert first.tags == ("release", "prerelease", "beta")
    assert first.url == "https://modrinth.com/project/AANobbMI/version/6etTPjD5"
    assert [u.version for u in updates if "latest" in u.tags] == ["mc26.3-0.9.3-neoforge"]
    assert target.label == "Sodium"
    assert len({u.uid for u in updates}) == len(updates)


@respx.mock
def test_modrinth_sends_its_filters(client):
    route, _ = _mock_modrinth([])
    _fetch(client, "modrinth", {"project": "sodium", "loaders": ["fabric"], "game_versions": ["1.21.1"]})
    params = route.calls.last.request.url.params
    assert json.loads(params["loaders"]) == ["fabric"]
    assert json.loads(params["game_versions"]) == ["1.21.1"]


@respx.mock
def test_modrinth_looks_up_the_title_once(client):
    _, project = _mock_modrinth()
    _fetch(client, "modrinth", "sodium")
    _fetch(client, "modrinth", "sodium")
    assert project.call_count == 1


@respx.mock
def test_modrinth_unknown_project_says_so(client):
    respx.get(f"{MODRINTH}/version").mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no project `sodium` on Modrinth"):
        _fetch(client, "modrinth", "sodium")


# gog

GOG_BUILDS = "https://content-system.gog.com/products/1207658924/os/{os}/builds"
GOG_PRODUCT = "https://api.gog.com/products/1207658924"


def _mock_gog(os_name="windows", builds=None):
    route = respx.get(GOG_BUILDS.format(os=os_name)).mock(
        return_value=httpx.Response(
            200, **({"json": builds} if builds is not None else {"content": fixture("gog_witcher_builds.json")})
        )
    )
    product = respx.get(GOG_PRODUCT).mock(
        return_value=httpx.Response(200, content=fixture("gog_witcher_product.json"))
    )
    return route, product


@pytest.mark.parametrize(
    "entry,key,os_name",
    [
        ("1207658924", "1207658924", "windows"),
        ({"product": 1207658924, "os": "mac"}, "1207658924/mac", "osx"),
    ],
)
def test_gog_targets(entry, key, os_name):
    (target,) = get_source("gog").targets([entry])
    assert (target.key, target.params["os"]) == (key, os_name)


@pytest.mark.parametrize("entry", ["the_witcher", {"product": "1207658924", "os": "linux"}])
def test_gog_rejects_bad_entries(entry):
    with pytest.raises(ConfigEntryError):
        get_source("gog").targets([entry])


@respx.mock
def test_gog_reports_builds(client):
    _mock_gog()
    target, updates = _fetch(client, "gog", "1207658924")

    assert updates[0].uid == "build:58076094395251196"
    assert updates[0].version == "1.5 (CS) GOG 0.2"
    assert updates[0].title == "The Witcher: Enhanced Edition 1.5 (CS) GOG 0.2"
    assert updates[0].url == "https://www.gog.com/game/the_witcher"
    assert updates[0].tags == ("release", "latest")
    assert updates[0].published.isoformat() == "2024-11-05T10:31:15+00:00"
    assert target.label == "The Witcher: Enhanced Edition"


@respx.mock
def test_gog_builds_on_a_branch_are_prereleases(client):
    doc = json.loads(fixture("gog_witcher_builds.json"))
    doc["items"][0]["branch"] = "Beta"
    _mock_gog(builds=doc)
    _, updates = _fetch(client, "gog", "1207658924")
    assert updates[0].tags == ("release", "prerelease", "beta")
    assert "latest" in updates[1].tags


@respx.mock
def test_gog_looks_up_the_title_once(client):
    _, product = _mock_gog()
    _fetch(client, "gog", "1207658924")
    _fetch(client, "gog", "1207658924")
    assert product.call_count == 1


@respx.mock
def test_gog_store_page_resolves_to_its_product_id(client):
    page = "https://www.gog.com/en/game/the_witcher"
    respx.get(page).mock(
        return_value=httpx.Response(200, text='<div card-product="1207658924"></div>')
    )
    assert preview.resolver(page) == "gog"
    assert preview.resolve("gog", page, client) == ("1207658924", "the game's product id")


@respx.mock
def test_gog_unknown_product_says_so(client):
    respx.get(GOG_BUILDS.format(os="windows")).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError, match="no GOG product `1207658924`"):
        _fetch(client, "gog", "1207658924")


@respx.mock
def test_gog_unknown_product_with_an_empty_build_list_says_so(client):
    # GOG answer for a product id that doesn't exist
    respx.get(GOG_BUILDS.format(os="windows")).mock(
        return_value=httpx.Response(200, json={"total_count": 0, "count": 0, "items": []})
    )
    respx.get(GOG_PRODUCT).mock(
        return_value=httpx.Response(404, json={"error": "Product not found"})
    )
    with pytest.raises(FetchError, match="no GOG product `1207658924`"):
        _fetch(client, "gog", "1207658924")


@respx.mock
def test_gog_game_without_builds_for_the_os_reports_nothing(client):
    _mock_gog(os_name="osx", builds={"total_count": 0, "count": 0, "items": []})
    _, updates = _fetch(client, "gog", {"product": "1207658924", "os": "mac"})
    assert updates == []
