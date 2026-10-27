"""`versions:` reports only some versions of a target"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from youpdated.config import Config, ConfigError, PrivacyConfig, parse_config
from youpdated.models import Target, Update
from youpdated.render import json_out, terminal
from youpdated.runner import RunResult, build_targets, rule_warnings, run
from youpdated.versions import Highest, VersionRules, highest, release

from .conftest import fixture

RELEASES = "https://github.com/python/cpython/releases.atom"
BREW_WGET = "https://formulae.brew.sh/api/formula/wget.json"
NPM_LEFTPAD = "https://registry.npmjs.org/left-pad"

DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _update(version: str | None, days: int = 0, *tags: str) -> Update:
    return Update(
        source="npm",
        target="x",
        uid=f"version:{version}",
        title=f"x {version}",
        url="http://x",
        published=DAY + timedelta(days=days),
        version=version,
        tags=tags,
    )


def _versions(updates: list[Update]) -> list[str | None]:
    return [u.version for u in updates]


def _config(source: str, entries: list, versions=None) -> Config:
    raw: dict = {"sources": {source: entries}}
    if versions is not None:
        raw["versions"] = versions
    config = parse_config(raw)
    config.privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    return config


# reading a version


@pytest.mark.parametrize(
    "version,numbers",
    [
        ("3.13.1", (3, 13, 1)),
        ("v3.15.0rc1", (3, 15)),
        ("4.0.0", (4,)),
        ("4", (4,)),
        ("1.2.0-beta.1", (1, 2)),
        ("131.0.6778.69", (131, 0, 6778, 69)),
        ("1.25.0_2", (1, 25)),
        ("go1.21.0", (1, 21)),
        ("pkg2@1.4.0", (1, 4)),
        ("release-7", (7,)),
        ("0.0.0", (0,)),
        ("4.7-stable", (4, 7)),
        ("2026.1.post1", (2026, 1)),
        ("curl-8_23_0", (8, 23)),
        ("rc-8_23_0-3", (8, 23)),
        ("REL_18_6", (18, 6)),
        ("REL_19_RC1", (19,)),
        ("v3_4_12", (3, 4, 12)),
        ("INKSCAPE_1_4_BETA3", (1, 4)),
        ("llvmorg-24-init", (24,)),
        ("10", (10,)),
    ],
)
def test_a_version_is_read_by_its_numbers(version, numbers):
    assert release(version) == numbers


@pytest.mark.parametrize(
    "version",
    [
        None,
        "",
        "nightly",
        "latest",
        "1" * 31,
        # A date or a number in a name is not a version
        "2024-10-01",
        "bug-2026-08-12T09_26_04Z",
        "swift-DEVELOPMENT-SNAPSHOT-2026-10-17-a",
        "assoc-array-fixes-v2",
    ],
)
def test_a_version_without_a_usable_number_has_no_release(version):
    assert release(version) is None


def test_highest_keeps_finals_apart_from_prereleases():
    updates = [_update("4.0.0rc1", 0, "prerelease"), _update("3.9.1"), _update("nightly")]
    assert highest(updates, None) == Highest(final=(3, 9, 1), overall=(4,))
    assert highest(updates, Highest((3, 10), (3, 10))) == Highest((3, 10), (4,))
    assert highest([_update("4.0.0rc1", 0, "prerelease")], None) == Highest(None, (4,))
    assert highest([_update(None)], None) is None


@pytest.mark.parametrize("value", [Highest((3, 9, 1), (4,)), Highest(None, (4,))])
def test_highest_reads_back_as_it_was_stored(value):
    assert Highest.parse(str(value)) == value


# min


def test_no_rules_keep_everything():
    updates = [_update("1.0.0"), _update("nightly"), _update(None)]
    assert VersionRules().admit(updates) == updates


def test_min_keeps_versions_at_or_above_it():
    rules = VersionRules(min=(3, 13))
    updates = [_update(v) for v in ("3.12.9", "3.13", "3.13.0", "3.13.2", "4.0.0")]
    assert _versions(rules.admit(updates)) == ["3.13", "3.13.0", "3.13.2", "4.0.0"]


def test_min_keeps_prereleases_of_its_own_release():
    rules = VersionRules(min=(4,))
    assert _versions(rules.admit([_update("4.0.0rc1"), _update("3.9.0")])) == ["4.0.0rc1"]


def test_updates_without_a_version_pass_and_unreadable_versions_do_not():
    rules = VersionRules(min=(1,))
    updates = [_update(None), _update("nightly"), _update("2.0")]
    assert _versions(rules.admit(updates)) == [None, "2.0"]


# bump


def test_a_major_bump_is_kept_and_what_follows_it_is_not():
    rules = VersionRules(bump="major")
    updates = [_update("3.9.0", 0), _update("3.9.1", 1), _update("4.0.0", 2), _update("4.0.1", 3)]
    assert _versions(rules.admit(updates)) == ["4.0.0"]


@pytest.mark.parametrize(
    "bump,kept",
    [
        ("major", ["2.0.0"]),
        ("minor", ["1.1.0", "2.0.0"]),
        ("patch", ["1.0.1", "1.1.0", "2.0.0"]),
    ],
)
def test_each_bump_compares_its_own_number(bump, kept):
    rules = VersionRules(bump=bump)
    updates = [_update(v, i) for i, v in enumerate(["1.0.0", "1.0.1", "1.1.0", "2.0.0"])]
    assert _versions(rules.admit(updates)) == kept


def test_a_build_number_is_not_a_patch_bump():
    rules = VersionRules(bump="patch")
    updates = [_update("131.0.6778.69", 0), _update("131.0.6778.85", 1)]
    assert rules.admit(updates) == []


def test_a_backport_is_not_a_bump():
    """3.12.8 shipped after 3.13.0 is new to 3.12, but 3.12 is not new"""
    rules = VersionRules(bump="minor")
    updates = [_update("3.12.7", 0), _update("3.13.0", 1), _update("3.12.8", 2)]
    assert _versions(rules.admit(updates)) == ["3.13.0"]


def test_a_major_and_its_first_patch_in_one_fetch_keep_the_major():
    rules = VersionRules(bump="major")
    updates = [_update("4.0.1", 5), _update("4.0.0", 4), _update("3.9.0", 0)]
    assert _versions(rules.admit(updates)) == ["4.0.0"]


def test_the_first_prerelease_toward_a_bump_is_one_and_the_final_still_is():
    rules = VersionRules(bump="major")
    updates = [
        _update("3.9.0", 0),
        _update("4.0.0a1", 1, "prerelease"),
        _update("4.0.0-rc.2", 2, "prerelease"),
        _update("4.0.0", 3),
    ]
    assert _versions(rules.admit(updates)) == ["4.0.0a1", "4.0.0"]


def test_prereleases_are_measured_against_every_version_seen_before():
    rules = VersionRules(bump="major")
    # A canary of 19 was seen last time; another is no news, but 19.0.0 is
    previous = Highest(final=(18, 3, 1), overall=(19,))
    updates = [_update("19.0.0-canary-2", 1, "prerelease"), _update("19.0.0", 2)]
    assert _versions(rules.admit(updates, previous)) == ["19.0.0"]


def test_nothing_known_before_is_not_a_bump():
    assert VersionRules(bump="major").admit([_update("4.0.0")]) == []


def test_the_previous_highest_stands_in_for_a_release_no_longer_listed():
    rules = VersionRules(bump="major")
    previous = Highest((3, 9), (3, 9))
    assert _versions(rules.admit([_update("4.0.0")], previous)) == ["4.0.0"]
    assert rules.admit([_update("3.9.2")], previous) == []
    # A backport below what was seen
    assert rules.admit([_update("2.7.18")], previous) == []


@pytest.mark.parametrize(
    "first,second",
    [
        ("v4.0.0", "4.0.0"),  # a tag, then its release
        ("2026.1", "2026.1.post1"),  # a post-release
        ("4.25.0", "4.25.0_2"),  # a Homebrew revision
        ("160.0b1", "160.0b2"),  # a beta channel, whose betas are its releases
    ],
)
def test_a_release_with_the_same_numbers_is_not_a_second_bump(first, second):
    rules = VersionRules(bump="minor")
    updates = [_update("3.9.0", 0), _update(first, 1), _update(second, 2)]
    assert _versions(rules.admit(updates)) == [first]
    assert rules.admit([_update(second)], Highest(release(first), release(first))) == []


def test_without_dates_bumps_go_by_version_order():
    rules = VersionRules(bump="major")
    undated = [
        Update(source="s", target="t", uid=v, title=v, url="", version=v)
        for v in ("2.0.0", "1.4.0")
    ]
    assert _versions(rules.admit(undated)) == ["2.0.0"]


def test_bump_and_min_combine():
    rules = VersionRules(bump="major", min=(5,))
    updates = [_update("3.0.0", 0), _update("4.0.0", 1), _update("5.0.0", 2), _update("5.1.0", 3)]
    assert _versions(rules.admit(updates)) == ["5.0.0"]


# config


def test_entry_rules_are_parsed_onto_the_target():
    config = _config("npm", [{"package": "react", "versions": {"bump": "Major ", "min": "18"}}])
    (target,), errors = build_targets(config)
    assert errors == []
    assert target.versions == VersionRules(bump="major", min=(18,))


def test_rules_merge_a_rule_at_a_time_and_any_turns_a_bump_off():
    config = _config(
        "npm",
        [
            "express",
            {"package": "react", "versions": {"min": "18"}},
            {"package": "vue", "versions": {"bump": "any"}},
        ],
        versions={"*": {"bump": "minor"}, "npm": {"bump": "major"}},
    )
    express, react, vue = build_targets(config)[0]
    assert express.versions == VersionRules(bump="major")
    assert react.versions == VersionRules(bump="major", min=(18,))
    assert vue.versions == VersionRules()


def test_a_mapping_of_rules_is_shorthand_for_every_source():
    config = _config("npm", ["express"], versions={"bump": "major"})
    assert config.versions == {"*": {"bump": "major"}}
    assert build_targets(config)[0][0].versions == VersionRules(bump="major")


def test_rules_under_an_alias_cover_its_entries():
    config = _config("github", ["python/cpython"], versions={"github": {"bump": "minor"}})
    (target,), _ = build_targets(config)
    assert target.source == "git"
    assert target.versions == VersionRules(bump="minor")


@pytest.mark.parametrize(
    "value,numbers",
    [(4, (4,)), ("3.13", (3, 13)), ("v1.4.2", (1, 4, 2)), (" 2.0 ", (2,))],
)
def test_min_takes_a_number_or_a_version_string(value, numbers):
    config = _config("npm", [{"package": "x", "versions": {"min": value}}])
    assert build_targets(config)[0][0].versions.min == numbers


@pytest.mark.parametrize(
    "versions,message",
    [
        ({"bump": "huge"}, "must be one of major, minor, patch, any"),
        ({"bump": 1}, "must be one of"),
        ({"min": 3.10}, 'must be in quotes, as YAML reads 3.1 as a number'),
        ({"min": True}, "must be a version number"),
        ({"min": "4.0-rc1"}, "must be a version number"),
        ({"min": "latest"}, "must be a version number"),
        ({"max": "4"}, "unknown `versions` option(s): max"),
        ("major", "must be a mapping"),
    ],
)
def test_malformed_entry_rules_are_collected_as_errors(versions, message):
    config = _config("npm", ["express", {"package": "x", "versions": versions}])
    targets, errors = build_targets(config)
    assert [t.key for t in targets] == ["express"]
    (error,) = errors
    assert message in error.message


@pytest.mark.parametrize(
    "versions,message",
    [
        (["major"], "`versions` must be a mapping"),
        ({"bump": "major", "npm": {"min": 2}}, "mixes rules (bump) with source names (npm)"),
        ({"npm": {"bump": "big"}}, "`versions.npm.bump` must be one of"),
    ],
)
def test_malformed_top_level_rules_are_rejected(versions, message):
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        parse_config({"sources": {"npm": ["x"]}, "versions": versions})


def test_empty_rules_are_not_recorded():
    config = parse_config({"sources": {"npm": ["x"]}, "versions": {"npm": None, "*": {}}})
    assert config.versions == {}


def test_versions_keys_that_cover_nothing_are_warned_about():
    config = parse_config({
        "sources": {"git": ["python/cpython"]},
        "versions": {"*": {"bump": "major"}, "git": {"bump": "major"}, "github": {"min": 1}},
    })
    (moved,) = rule_warnings(config)
    assert "`versions.github` covers nothing" in moved and "need `versions.git`" in moved


def test_add_and_rm_see_through_an_entrys_rules():
    from youpdated.edit import _keys
    from youpdated.registry import get_source

    source = get_source("npm")
    assert source is not None
    assert _keys(source, {"package": "react", "versions": {"bump": "major"}}) == {"react"}


# runs


@respx.mock
def test_all_shows_only_what_passes_and_counts_the_rest(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    config = _config("git", [{"repo": "python/cpython", "versions": {"bump": "minor"}}])

    result = run(config, state, client, show_all=True)

    # 3.15 is the only new minor, and b3 its first prerelease; 3.12.14 and the like are backports
    assert _versions(result.updates) == ["v3.15.0b3"]
    assert result.filtered == 9
    assert result.ignored == 0


@respx.mock
def test_left_out_versions_are_recorded_as_seen(state, client):
    respx.get(RELEASES).mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    with_rule = _config("git", [{"repo": "python/cpython", "versions": {"min": "3.15"}}])
    run(with_rule, state, client, show_all=True)
    assert state.seen_count() == 10

    # Unlike `ignore:`, loosening the rule doesn't report the past
    assert run(_config("git", ["python/cpython"]), state, client).updates == []


def _brew(version: str) -> httpx.Response:
    doc = json.loads(fixture("homebrew_wget.json"))
    doc["versions"]["stable"], doc["revision"] = version, 0
    return httpx.Response(200, json=doc)


@respx.mock
def test_a_source_listing_one_version_bumps_against_the_last_one_seen(state, client):
    route = respx.get(BREW_WGET).mock(return_value=_brew("1.25.0"))
    config = _config("homebrew", [{"formula": "wget", "versions": {"bump": "major"}}])

    assert run(config, state, client).baseline
    assert state.highest_version("homebrew", "formula:wget") == "1.25|1.25"

    route.mock(return_value=_brew("1.26.0"))
    minor = run(config, state, client)
    assert minor.updates == [] and minor.filtered == 1

    route.mock(return_value=_brew("2.0.0"))
    assert _versions(run(config, state, client).updates) == ["2.0.0"]
    assert state.highest_version("homebrew", "formula:wget") == "2|2"


@respx.mock
def test_the_highest_version_is_kept_only_for_targets_with_a_bump(state, client):
    respx.get(BREW_WGET).mock(return_value=_brew("1.25.0"))
    run(_config("homebrew", [{"formula": "wget", "versions": {"min": 1}}]), state, client)
    assert state.highest_version("homebrew", "formula:wget") is None


@respx.mock
def test_a_preview_does_not_move_the_highest_version(state, client):
    route = respx.get(BREW_WGET).mock(return_value=_brew("1.25.0"))
    config = _config("homebrew", [{"formula": "wget", "versions": {"bump": "major"}}])
    run(config, state, client)

    route.mock(return_value=_brew("2.0.0"))
    assert len(run(config, state, client, save=False).updates) == 1
    assert state.highest_version("homebrew", "formula:wget") == "1.25|1.25"
    assert len(run(config, state, client).updates) == 1


def _npm(times: dict[str, datetime]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "dist-tags": {"latest": max(times)},
            "time": {v: t.isoformat() for v, t in times.items()},
            "versions": {},
        },
    )


@respx.mock
def test_what_since_holds_back_does_not_move_the_highest_version(state, client):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    route = respx.get(NPM_LEFTPAD).mock(return_value=_npm({"1.3.0": old}))
    config = _config("npm", [{"package": "left-pad", "versions": {"bump": "major"}}])
    run(config, state, client)

    # 2.0.0 is new to us but older than --since, so it waits for the next run
    route.mock(return_value=_npm({"1.3.0": old, "2.0.0": old + timedelta(days=1)}))
    assert run(config, state, client, since=timedelta(days=1)).updates == []
    assert state.highest_version("npm", "left-pad") == "1.3|1.3"
    assert _versions(run(config, state, client).updates) == ["2.0.0"]


# output


def test_json_exposes_the_filtered_count():
    payload = json.loads(json_out.render(RunResult(filtered=3)))
    assert payload["counts"]["filtered"] == 3


def test_terminal_summary_mentions_what_versions_left_out(capsys):
    from rich.console import Console

    result = RunResult(targets=[Target(source="npm", key="x")], ignored=2, filtered=3)
    terminal.render(result, console=Console(width=120))

    assert "2 ignored, 3 left out by version." in capsys.readouterr().out
