"""`ignore:` drops unwanted update types"""

from __future__ import annotations

import httpx
import pytest
import respx

from youpdated.config import Config, ConfigError, PrivacyConfig, parse_config
from youpdated.models import Target, Update
from youpdated.render import json_out, terminal
from youpdated.registry import get_source
from youpdated.runner import build_targets, run
from youpdated.sources.github import _is_prerelease

from .conftest import fixture

RELEASES = "https://github.com/python/cpython/releases.atom"
# The fixture holds 7 stable tags and 3 prereleases (rc1, b4, b3).
FIXTURE_PRERELEASES = 3
FIXTURE_TOTAL = 10


def _config(entries, ignore=None) -> Config:
    raw = {"sources": {"github": entries}}
    if ignore is not None:
        raw["ignore"] = ignore
    config = parse_config(raw)
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    return config


# config parsing


def test_bare_list_is_shorthand_for_every_source():
    config = parse_config({"sources": {"npm": ["x"]}, "ignore": ["prerelease"]})
    assert config.ignore == {"*": frozenset({"prerelease"})}
    assert config.ignored_tags("anything") == frozenset({"prerelease"})


def test_a_single_tag_needs_no_list():
    config = parse_config({"sources": {"npm": ["x"]}, "ignore": "prerelease"})
    assert config.ignored_tags("npm") == frozenset({"prerelease"})


def test_mapping_form_merges_the_wildcard_with_the_source():
    config = parse_config(
        {
            "sources": {"npm": ["x"]},
            "ignore": {"*": ["commit"], "github": ["prerelease", "tag"]},
        }
    )
    assert config.ignored_tags("github") == frozenset({"commit", "prerelease", "tag"})
    assert config.ignored_tags("steam") == frozenset({"commit"})


def test_tags_are_normalized():
    config = parse_config({"sources": {"npm": ["x"]}, "ignore": ["  PreRelease "]})
    assert config.ignored_tags("npm") == frozenset({"prerelease"})


def test_no_ignore_section_ignores_nothing():
    config = parse_config({"sources": {"npm": ["x"]}})
    assert config.ignore == {}
    assert config.ignored_tags("npm") == frozenset()


def test_empty_ignore_is_not_recorded():
    config = parse_config({"sources": {"npm": ["x"]}, "ignore": {"github": []}})
    assert config.ignore == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"ignore": 7},
        {"ignore": [""]},
        {"ignore": ["  "]},
        {"ignore": [3]},
        {"ignore": {"github": 7}},
        {"ignore": {"github": [None]}},
    ],
)
def test_malformed_ignore_is_rejected(bad):
    with pytest.raises(ConfigError):
        parse_config({"sources": {"npm": ["x"]}, **bad})


# Target


def test_target_without_rules_ignores_nothing():
    assert not Target(source="github", key="a/b").ignores(("release", "prerelease"))


def test_target_drops_an_update_matching_any_one_tag():
    target = Target(source="github", key="a/b", ignore=frozenset({"prerelease"}))
    assert target.ignores(("release", "prerelease"))
    assert not target.ignores(("release",))


def test_target_matching_is_case_insensitive():
    target = Target(source="github", key="a/b", ignore=frozenset({"prerelease"}))
    assert target.ignores(("PreRelease",))


# build_targets


def test_entry_rules_add_to_the_global_ones():
    config = _config(
        ["python/cpython", {"repo": "astral-sh/uv", "ignore": ["prerelease"]}],
        ignore={"*": ["commit"]},
    )
    targets, errors = build_targets(config)

    assert not errors
    by_key = {t.key: t for t in targets}
    assert by_key["python/cpython"].ignore == frozenset({"commit"})
    assert by_key["astral-sh/uv"].ignore == frozenset({"commit", "prerelease"})


def test_entry_rules_do_not_leak_between_entries():
    config = _config([{"repo": "a/one", "ignore": ["tag"]}, "b/two"])
    targets, _ = build_targets(config)
    by_key = {t.key: t for t in targets}

    assert by_key["a/one"].ignore == frozenset({"tag"})
    assert by_key["b/two"].ignore == frozenset()


def test_ignore_is_stripped_before_the_source_sees_it():
    """Sources know nothing about `ignore`"""
    config = _config([{"repo": "a/one", "ignore": ["tag"], "watch": ["commits"]}])
    (target,) = build_targets(config)[0]

    assert "ignore" not in target.params
    assert target.params["watch"] == ["commits"]


def test_a_malformed_entry_rule_is_collected_as_an_error():
    config = _config([{"repo": "a/one", "ignore": [""]}])
    targets, errors = build_targets(config)

    assert targets == []
    assert len(errors) == 1
    assert "ignore" in errors[0].message


# run


@respx.mock
def test_run_drops_ignored_updates_and_counts_them(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    config = _config([{"repo": "python/cpython", "ignore": ["prerelease"]}])

    result = run(config, state, client, show_all=True)

    assert result.ignored == FIXTURE_PRERELEASES
    assert len(result.updates) == FIXTURE_TOTAL - FIXTURE_PRERELEASES
    assert all("prerelease" not in u.tags for u in result.updates)


@respx.mock
def test_run_without_rules_keeps_everything(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    result = run(_config(["python/cpython"]), state, client, show_all=True)

    assert result.ignored == 0
    assert len(result.updates) == FIXTURE_TOTAL


@respx.mock
def test_ignored_updates_are_not_recorded_as_seen(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    with_rule = _config([{"repo": "python/cpython", "ignore": ["prerelease"]}])
    run(with_rule, state, client, show_all=True)

    seen = state.seen_count()
    assert seen == FIXTURE_TOTAL - FIXTURE_PRERELEASES

    # Same run, rule removed: only the prereleases are still unseen
    fresh = run(_config(["python/cpython"]), state, client)
    assert len(fresh.updates) == FIXTURE_PRERELEASES
    assert all("prerelease" in u.tags for u in fresh.updates)


@respx.mock
def test_ignoring_every_tag_leaves_a_clean_run(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    config = _config(["python/cpython"], ignore=["release"])
    result = run(config, state, client, show_all=True)

    assert result.updates == []
    assert result.ignored == FIXTURE_TOTAL
    assert not result.errors


# reporting the count


def test_json_exposes_the_ignored_count():
    from youpdated.runner import RunResult

    payload = json_out.render(RunResult(ignored=4))
    assert '"ignored": 4' in payload


def test_terminal_summary_mentions_ignored_items(capsys):
    from rich.console import Console

    from youpdated.runner import RunResult

    update = Update(
        source="github", target="a/b", uid="1", title="v1", url="http://x", tags=("release",)
    )
    result = RunResult(updates=[update], targets=[Target(source="github", key="a/b")], ignored=4)
    terminal.render(result, console=Console(width=100))

    assert "4 ignored." in capsys.readouterr().out


def test_terminal_summary_stays_quiet_when_nothing_was_ignored(capsys):
    from rich.console import Console

    from youpdated.runner import RunResult

    result = RunResult(targets=[Target(source="github", key="a/b")])
    terminal.render(result, console=Console(width=100))

    assert "ignored" not in capsys.readouterr().out


# the tagging the rules match on


@pytest.mark.parametrize(
    "version",
    [
        "v3.15.0rc2", "v3.15.0b4", "v3.15.0a1", "1.2.0-beta.1", "1.2.0-rc1",
        "0.12.0-alpha", "2.0.0-dev", "v1.0-preview", "4.0.0-nightly.20240101",
        "1.0.0-canary", "1.5.0-snapshot",
    ],
)
def test_github_version_reads_as_a_prerelease(version):
    assert _is_prerelease(version)


@pytest.mark.parametrize(
    "version",
    [
        "v3.14.7", "0.12.13", "1.98.1", "v1.0.0", "2026.1.0", "v1.0.0-linux",
        "release-1.2.3", "v4.2.0+build.7", "1.0.0-final", "v2.0.0-stable",
        "20240101", "v1.2.3-x86_64", "openssl-3.0.0", None, "",
    ],
)
def test_github_version_reads_as_stable(version):
    assert not _is_prerelease(version)


@respx.mock
def test_github_atom_tags_prereleases_without_a_token(client, monkeypatch):
    """The anonymous path is the default, so it has to carry the tag too"""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    source = get_source("github")
    (target,) = source.targets(["python/cpython"])
    updates = list(source.fetch(target, client))

    pre = [u for u in updates if "prerelease" in u.tags]
    assert len(pre) == FIXTURE_PRERELEASES
    assert {u.version for u in pre} == {"v3.15.0rc1", "v3.15.0b4", "v3.15.0b3"}
    # Never at the cost of the release tag the API path also sets.
    assert all("release" in u.tags for u in pre)


@respx.mock
def test_github_commits_are_never_marked_prerelease(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get("https://github.com/python/cpython/commits.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_commits.atom"))
    )
    source = get_source("github")
    (target,) = source.targets([{"repo": "python/cpython", "watch": ["commits"]}])
    updates = list(source.fetch(target, client))

    assert updates
    assert all("prerelease" not in u.tags for u in updates)


@respx.mock
def test_npm_tags_semver_prereleases(client):
    respx.get("https://registry.npmjs.org/widget").mock(
        return_value=httpx.Response(200, content=fixture("npm_prereleases.json"))
    )
    source = get_source("npm")
    (target,) = source.targets(["widget"])
    updates = list(source.fetch(target, client))

    pre = {u.version for u in updates if "prerelease" in u.tags}
    assert pre == {"3.0.0-alpha.1", "3.0.0-beta.2", "3.0.0-rc.1"}
    # dist-tags.latest is stable here, so it keeps release/latest and no more.
    (latest,) = [u for u in updates if "latest" in u.tags]
    assert latest.version == "2.1.0"
    assert "prerelease" not in latest.tags


@respx.mock
def test_npm_prereleases_can_be_ignored(state, client):
    respx.get("https://registry.npmjs.org/widget").mock(
        return_value=httpx.Response(200, content=fixture("npm_prereleases.json"))
    )
    config = parse_config(
        {"sources": {"npm": [{"package": "widget", "ignore": ["prerelease"]}]}}
    )
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)

    result = run(config, state, client, show_all=True)

    assert result.ignored == 3
    assert {u.version for u in result.updates} == {"2.0.0", "2.1.0"}


@respx.mock
def test_itch_builds_are_ignorable_without_touching_devlogs(state, client):
    """itch builds are not releases, and must not share the github/npm tag."""
    respx.get("https://aak581.itch.io/engineering-marvels-from-hell/devlog.rss").mock(
        return_value=httpx.Response(200, content=fixture("itch_devlog.rss"))
    )
    respx.get("https://aak581.itch.io/engineering-marvels-from-hell").mock(
        return_value=httpx.Response(200, content=fixture("itch_game_page.html"))
    )
    config = parse_config(
        {
            "sources": {
                "itch": [
                    {
                        "url": "https://aak581.itch.io/engineering-marvels-from-hell",
                        "ignore": ["build"],
                    }
                ]
            }
        }
    )
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)

    result = run(config, state, client, show_all=True)

    assert result.ignored == 1
    assert result.updates
    assert all("devlog" in u.tags for u in result.updates)


@respx.mock
def test_ignoring_release_everywhere_leaves_itch_builds_alone(state, client):
    """The collision this retagging exists to prevent."""
    respx.get("https://hempuli.itch.io/baba-is-you/devlog.rss").mock(
        return_value=httpx.Response(404, text="not found")
    )
    respx.get("https://hempuli.itch.io/baba-is-you").mock(
        return_value=httpx.Response(200, content=fixture("itch_game_no_devlog.html"))
    )
    config = parse_config(
        {"ignore": ["release"], "sources": {"itch": ["https://hempuli.itch.io/baba-is-you"]}}
    )
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)

    result = run(config, state, client, show_all=True)

    assert result.ignored == 0
    assert [u.tags for u in result.updates] == [("build",)]
