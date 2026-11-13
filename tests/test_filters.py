"""An entry's rules edited in place and the app's `filters.*` ops"""

from __future__ import annotations

import pytest
import yaml

from youpdated.config import ConfigError, parse_config
from youpdated.edit import set_entry_rules, watching
from youpdated.registry import get_source
from youpdated.runner import build_targets
from youpdated.sources.base import scalar_key
from youpdated.versions import VersionRules, suggest_patterns

from .fake_source import write_items
from .test_api import Engine, make_config

CONFIG = """\
# mine
sources:
  git:
    - python/cpython   # the language
    - repo: astral-sh/uv
      watch: [releases, commits]
      # only finals
      ignore: [prerelease]
    - ignore: [tag]
      repo: a/b
      versions:
        bump: major
        min: "3"
    - {repo: c/d, ignore: [prerelease]}
  npm: [express, react]
"""


def rules(text: str, source: str, key: str, **new) -> str:
    return set_entry_rules(text, source, key, new).text


def _targets(text: str):
    targets, errors = build_targets(parse_config(yaml.safe_load(text)))
    assert not errors
    return {t.key: t for t in targets}


@pytest.mark.parametrize(
    "source,entry,key",
    [
        ("git", "a/b", "repo"),
        ("npm", "left-pad", "package"),
        ("steam", 440, "appid"),
        ("homebrew", "cask:firefox", "formula"),
        ("youtube", "@veritasium", "channel"),
        ("youtube", "https://www.youtube.com/playlist?list=PLabcdefghijklmnop", "playlist"),
        ("git", {"repo": "a/b"}, None),
        ("git", "not a repo", None),
    ],
)
def test_a_source_says_what_a_bare_entry_is(source, entry, key):
    assert scalar_key(get_source(source), entry) == key


# editing an entry's rules


def test_a_bare_entry_becomes_a_mapping_and_keeps_its_comment():
    edited = rules(CONFIG, "git", "github.com/python/cpython", versions={"match": "v3.12.*"})

    assert (
        "    - repo: python/cpython   # the language\n"
        "      versions: {match: v3.12.*}\n"
        "    - repo: astral-sh/uv\n"
    ) in edited
    assert _targets(edited)["github.com/python/cpython"].versions.match == ("v3.12.*",)
    # and nothing else moved
    assert edited.replace("repo: python/cpython", "python/cpython").replace(
        "      versions: {match: v3.12.*}\n", ""
    ) == CONFIG


def test_a_rule_is_rewritten_where_it_is_and_a_new_one_goes_last():
    edited = rules(
        CONFIG, "git", "github.com/astral-sh/uv",
        ignore=["prerelease", "commit"], versions={"min": "0.5"},
    )
    assert (
        "    - repo: astral-sh/uv\n"
        "      watch: [releases, commits]\n"
        "      # only finals\n"
        "      ignore: [prerelease, commit]\n"
        "      versions: {min: '0.5'}\n"
    ) in edited


def test_a_rule_taken_off_takes_its_lines_with_it():
    edited = rules(CONFIG, "git", "github.com/a/b")
    assert "    - repo: a/b\n    - {repo: c/d, ignore: [prerelease]}\n" in edited
    target = _targets(edited)["github.com/a/b"]
    assert target.ignore == frozenset() and target.versions == VersionRules()


def test_a_block_rule_is_replaced_whole():
    edited = rules(CONFIG, "git", "github.com/a/b", ignore=["tag"], versions={"bump": "minor"})
    assert "    - ignore: [tag]\n      repo: a/b\n      versions: {bump: minor}\n" in edited


def test_a_flow_entry_stays_on_its_line():
    edited = rules(
        CONFIG, "git", "github.com/c/d", ignore=["prerelease"], versions={"match": ["app-v*", "*-lts"]}
    )
    assert "    - {repo: c/d, ignore: [prerelease], versions: {match: [app-v*, '*-lts']}}\n" in edited


def test_an_entry_in_a_flow_list_is_edited_in_it():
    edited = rules(CONFIG, "npm", "react", versions={"bump": "major"})
    assert "  npm: [express, {package: react, versions: {bump: major}}]\n" in edited
    assert rules(edited, "npm", "react") == CONFIG.replace("react]", "{package: react}]")


def test_the_same_rules_change_nothing():
    change = set_entry_rules(CONFIG, "git", "github.com/c/d", {"ignore": ["prerelease"]})
    assert change.applied == [] and change.text == CONFIG
    # a bare entry with no rules stays bare
    assert rules(CONFIG, "npm", "express") == CONFIG


def test_an_entry_not_watched_is_skipped():
    change = set_entry_rules(CONFIG, "npm", "vue", {"versions": {"bump": "major"}})
    assert change.skipped == ["vue"] and change.text == CONFIG


def test_an_entry_listed_twice_is_left_to_the_user():
    text = "sources:\n  npm:\n    - react\n    - {package: react, tag: next}\n"
    assert len(watching(text, "npm", "react")) == 2
    with pytest.raises(ConfigError, match="listed 2 times"):
        set_entry_rules(text, "npm", "react", {"versions": {"bump": "major"}})


def test_windows_line_ends_are_kept():
    text = CONFIG.replace("\n", "\r\n")
    edited = rules(text, "npm", "react", versions={"bump": "major"})
    assert "\r\n" in edited and "\n" not in edited.replace("\r\n", "")


# patterns offered


@pytest.mark.parametrize(
    "versions,offered",
    [
        # a repo that releases several things: by what's before the numbers
        (
            ["v0.6.0", "app-macos-v0.9.1", "app-windows-v0.9.0", "app-macos-v0.9.0", "v0.5.0"],
            [("v*", 2), ("app-macos-v*", 2), ("app-windows-v*", 1)],
        ),
        # one thing: by line
        (
            ["v3.15.0a1", "v3.14.1", "v3.13.9", "v3.14.0", "v3.12.12"],
            [("v3.15.*", 1), ("v3.14.*", 2), ("v3.13.*", 1), ("v3.12.*", 1)],
        ),
        (["v25.1.0", "v24.11.0", "v22.21.1", "v25.0.0"], [("v25.*", 2), ("v24.*", 1), ("v22.*", 1)]),
        (["1.2.3", "app-1.0", "2.0.0"], [("[0-9]*", 2), ("app-*", 1)]),
        # nothing to tell apart
        (["1.2.3", "1.2.2", "1.2.1"], []),
        (["nightly", "stable"], []),
        ([], []),
    ],
)
def test_patterns_are_offered_by_prefix_or_by_line(versions, offered):
    assert suggest_patterns(versions) == offered


def test_every_offered_pattern_takes_what_it_counts():
    versions = ["a[1]-1.0", "a[1]-1.1", "b-2.0"]
    offered = dict(suggest_patterns(versions))
    assert offered == {"a[[]1]-*": 2, "b-*": 1}
    assert [v for v in versions if VersionRules(match=("a[[]1]-*",)).matches(v)] == versions[:2]


# app ops


@pytest.fixture
def engine(tmp_path, fake):
    return Engine(make_config(tmp_path, entries=("alpha", "beta")), tmp_path / "state.db")


PLAIN = {
    "ok": True,
    "match": [],
    "skip_prereleases": False,
    "skip_prereleases_inherited": False,
    "bump": "any",
    "min": None,
    "editable": True,
    "reason": None,
}


def _set(engine, key="alpha", **rules):
    sent = {"match": [], "skip_prereleases": False, "bump": "any", "min": None, **rules}
    return engine("filters.set", source="fake", key=key, **sent)


def test_an_entry_without_rules_has_none(engine):
    assert engine("filters.get", source="fake", key="alpha") == PLAIN


def test_set_rules_are_written_to_the_entry_and_read_back(engine):
    reply = _set(engine, match=["app-v*", " "], skip_prereleases=True, bump="minor", min=" 2.0 ")

    assert reply == {
        **PLAIN, "match": ["app-v*"], "skip_prereleases": True, "bump": "minor", "min": "2",
    }
    assert engine("filters.get", source="fake", key="alpha") == reply
    text = engine.config_override.read_text()
    assert (
        "    - name: alpha\n"
        "      ignore: [prerelease]\n"
        "      versions: {match: app-v*, bump: minor, min: '2.0'}\n"
        "    - beta\n"
    ) in text


def test_clearing_the_rules_clears_the_entry(engine):
    _set(engine, match=["v*"], skip_prereleases=True, bump="major", min="3")
    assert _set(engine) == PLAIN
    assert "    - name: alpha\n    - beta\n" in engine.config_override.read_text()


def test_the_sources_rules_show_and_only_differences_are_written(engine, tmp_path):
    engine.config_override = make_config(
        tmp_path,
        "ignore: {fake: [prerelease]}\nversions: {fake: {bump: major, min: 2, match: 'v*'}}\n",
    )
    inherited = {
        **PLAIN, "match": ["v*"], "skip_prereleases": True, "skip_prereleases_inherited": True,
        "bump": "major", "min": "2",
    }
    assert engine("filters.get", source="fake", key="alpha") == inherited

    # the same writes nothing
    before = engine.config_override.read_text()
    assert _set(engine, match=["v*"], skip_prereleases=True, bump="major", min="2.0") == inherited
    assert engine.config_override.read_text() == before

    # undoing rules takes another rule; source ignores can't be undone
    assert _set(engine) == {**PLAIN, "skip_prereleases": True, "skip_prereleases_inherited": True}
    assert "      versions: {match: '*', bump: any, min: 0}\n" in engine.config_override.read_text()


def test_other_ignored_tags_are_kept(engine, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("{name: alpha, ignore: [tag, prerelease]}",))
    assert engine("filters.get", source="fake", key="alpha")["skip_prereleases"]

    assert not _set(engine)["skip_prereleases"]
    assert "- {name: alpha, ignore: [tag]}" in engine.config_override.read_text()


@pytest.mark.parametrize(
    "rules,message",
    [
        ({"min": "latest"}, "`filters.min` must be a version number"),
        ({"bump": "huge"}, "bump must be: any, major, minor, patch"),
    ],
)
def test_a_bad_rule_is_refused_and_nothing_is_written(engine, rules, message):
    before = engine.config_override.read_text()
    reply = _set(engine, **rules)
    assert reply["error"]["code"] == "bad_request" and message in reply["error"]["message"]
    assert engine.config_override.read_text() == before


def test_an_entry_listed_twice_is_read_only(engine, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "{name: alpha}"))
    got = engine("filters.get", source="fake", key="alpha")
    assert got["editable"] is False and "multiple times" in got["reason"]
    assert _set(engine, bump="major")["error"]["code"] == "bad_request"


def test_filters_of_something_not_watched_are_not_found(engine):
    assert engine("filters.get", source="fake", key="nope")["error"]["code"] == "not_found"
    assert engine("filters.preview", source="fake", key="nope")["error"]["code"] == "not_found"
    assert engine("filters.get", source="nope", key="alpha")["error"]["code"] == "bad_request"


def _versions(*listed):
    return [
        {"uid": v, "version": v, "published": f"2026-10-{30 - n:02d}T12:00:00Z",
         "tags": ["release", "prerelease"] if "beta" in v else ["release"]}
        for n, v in enumerate(listed)
    ]


MIXED = _versions("v1.0.0", "app-v1.0.0", "app-v1.0.0-beta.1", "v0.6.0", "app-v0.9.1")


def test_preview_says_what_the_rules_would_keep(engine, fake):
    write_items(fake, {"alpha": MIXED})

    everything = engine("filters.preview", source="fake", key="alpha")
    assert [v["version"] for v in everything["versions"]] == [m["version"] for m in MIXED]
    assert everything["kept"] == 5 and everything["fetch_error"] is None
    assert everything["versions"][2] == {
        "version": "app-v1.0.0-beta.1", "prerelease": True, "kept": True,
    }
    assert everything["patterns"] == [
        {"pattern": "v*", "label": "v…", "count": 2},
        {"pattern": "app-v*", "label": "app-v…", "count": 3},
    ]

    some = engine(
        "filters.preview", source="fake", key="alpha",
        match=["app-v*"], skip_prereleases=True, min="1",
    )
    assert [v["version"] for v in some["versions"] if v["kept"]] == ["app-v1.0.0"]
    assert some["kept"] == 1 and some["patterns"] == everything["patterns"]


def test_preview_ignores_the_entrys_own_rules(engine, fake, tmp_path):
    """A set pattern doesn't hide versions another would take"""
    engine.config_override = make_config(
        tmp_path, entries=("{name: alpha, ignore: [prerelease], versions: {match: 'app-v*'}}",)
    )
    write_items(fake, {"alpha": MIXED})
    assert len(engine("filters.preview", source="fake", key="alpha")["versions"]) == 5


def test_preview_fetches_once_until_asked_again(engine, fake):
    write_items(fake, {"alpha": MIXED})
    engine("filters.preview", source="fake", key="alpha")
    write_items(fake, {"alpha": MIXED[:2]})

    assert len(engine("filters.preview", source="fake", key="alpha", match=["v*"])["versions"]) == 5
    assert len(engine("filters.preview", source="fake", key="alpha", refresh=True)["versions"]) == 2


def test_preview_of_a_source_that_fails_says_why(engine, fake):
    write_items(fake, {"alpha": "fail"})
    reply = engine("filters.preview", source="fake", key="alpha", match=["v*"])
    assert reply["ok"] and reply["versions"] == [] and reply["patterns"] == []
    assert reply["fetch_error"] == "RuntimeError: alpha is down"


def test_a_bad_pattern_or_version_in_a_preview_is_refused(engine, fake):
    write_items(fake, {"alpha": MIXED})
    reply = engine("filters.preview", source="fake", key="alpha", min="soon")
    assert reply["error"]["code"] == "bad_request"


def test_a_line_pattern_is_labelled_as_a_line(engine, fake):
    write_items(fake, {"alpha": _versions("v3.14.1", "v3.13.9", "v3.13.8")})
    patterns = engine("filters.preview", source="fake", key="alpha")["patterns"]
    assert patterns == [
        {"pattern": "v3.14.*", "label": "v3.14.x", "count": 1},
        {"pattern": "v3.13.*", "label": "v3.13.x", "count": 2},
    ]


def test_a_set_filter_decides_the_next_check(engine, fake):
    write_items(fake, {"alpha": MIXED[3:]})
    engine("check")
    _set(engine, match=["app-v*"], skip_prereleases=True)
    write_items(fake, {"alpha": MIXED})

    reply = engine("check")
    assert [i["version"] for i in reply["items"]] == ["app-v1.0.0"]
