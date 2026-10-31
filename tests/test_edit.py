"""`add` and `remove`: config edits that keep the rest of the file as written"""

from __future__ import annotations

import os
import stat
from typing import Sequence

import pytest
import yaml

from youpdated import crypto
from youpdated.cli import main
from youpdated.config import EXAMPLE_CONFIG, ConfigError, load_config, parse_config
from youpdated.edit import add_to_config, guess_source, remove_from_config, yaml_scalar
from youpdated.models import Update
from youpdated.registry import all_sources
from youpdated.state import State

PASSPHRASE = "correct horse battery staple"
STEAM = all_sources()["steam"]
CPYTHON_API = "https://api.github.com/repos/python/cpython/releases?per_page=20"
CPYTHON_ATOM = "https://github.com/python/cpython/releases.atom"
STEAM_NEWS = "https://store.steampowered.com/feeds/news/app/440/?cc=US&l=english"
SHARED = "https://example.com/shared.json"
UNRELATED = "https://example.com/other.xml"


def add(text: str, source: str, *values: str) -> str:
    return add_to_config(text, source, list(values)).text


def remove(text: str, source: str, *values: str) -> str:
    return remove_from_config(text, source, list(values)).text


def is_subsequence(short: Sequence[str], long: Sequence[str]) -> bool:
    rest = iter(long)
    return all(line in rest for line in short)

# writing values

@pytest.mark.parametrize(
    "value, written",
    [
        ("python/cpython", "python/cpython"),
        ("https://blog.rust-lang.org/feed.xml", "https://blog.rust-lang.org/feed.xml"),
        ("cask:firefox", "cask:firefox"),
        ("440", "440"),
        ("@NASA", '"@NASA"'),
        ("@types/node", '"@types/node"'),
        ("yes", '"yes"'),
        ("null", '"null"'),
        ("1.0", '"1.0"'),
        ("0123", '"0123"'),
        ("a, b", '"a, b"'),
        ("a #b", '"a #b"'),
        ("key: value", '"key: value"'),
    ],
)
def test_values_are_quoted_only_when_they_must_be(value, written):
    assert yaml_scalar(value) == written

# adding

def test_adding_keeps_every_comment_and_line():
    edited = add(EXAMPLE_CONFIG, "git", "torvalds/linux")
    assert is_subsequence(EXAMPLE_CONFIG.splitlines(), edited.splitlines())
    assert len(edited.splitlines()) == len(EXAMPLE_CONFIG.splitlines()) + 1
    assert "    - codeberg.org/forgejo/forgejo\n    - torvalds/linux\n\n  npm:" in edited

def test_an_added_entry_goes_after_a_trailing_comment_in_the_list_not_before_it():
    edited = add(EXAMPLE_CONFIG, "youtube", "@Veritasium")
    assert '    - "@NASA"\n    - "@Veritasium"\n    # - playlist:' in edited

def test_adding_then_removing_gives_back_the_original():
    edited = add(EXAMPLE_CONFIG, "npm", "left-pad", "react")
    assert remove(edited, "npm", "left-pad", "react") == EXAMPLE_CONFIG

def test_an_alias_adds_to_the_canonical_section():
    change = add_to_config(EXAMPLE_CONFIG, "github", ["torvalds/linux"])
    assert change.section == "git"
    assert "github:" not in change.text


def test_an_alias_section_is_used_when_it_is_the_only_one():
    text = "sources:\n  github:\n    - python/cpython\n"
    assert add(text, "git", "torvalds/linux") == (
        "sources:\n  github:\n    - python/cpython\n    - torvalds/linux\n"
    )

def test_an_entry_already_watched_in_another_form_is_skipped():
    change = add_to_config(
        EXAMPLE_CONFIG, "git",
        ["https://github.com/python/cpython", "torvalds/linux", "github.com/torvalds/linux"],
    )
    assert change.skipped == ["https://github.com/python/cpython", "github.com/torvalds/linux"]
    assert change.applied == ["torvalds/linux"]


def test_nothing_new_leaves_the_text_alone():
    change = add_to_config(EXAMPLE_CONFIG, "pypi", ["HTTPX"])
    assert change.applied == []
    assert change.text == EXAMPLE_CONFIG


def test_an_entry_the_source_rejects_is_an_error():
    with pytest.raises(ConfigError, match="does not name a owner/repo"):
        add_to_config(EXAMPLE_CONFIG, "git", ["nope"])


def test_an_unknown_source_is_an_error():
    with pytest.raises(ConfigError, match="unknown source `crates`"):
        add_to_config(EXAMPLE_CONFIG, "crates", ["serde"])

def test_a_new_section_goes_at_the_end_of_sources_spaced_like_the_rest():
    text = (
        "sources:\n"
        "  git:\n"
        "    - python/cpython\n"
        "\n"
        "  # packages\n"
        "  npm:\n"
        "    - express   # web\n"
        "\n"
        "# Comments about notify\n"
        "notify:\n"
        "  command: echo\n"
    )
    assert add(text, "steam", "440") == (
        "sources:\n"
        "  git:\n"
        "    - python/cpython\n"
        "\n"
        "  # packages\n"
        "  npm:\n"
        "    - express   # web\n"
        "\n"
        "  steam:\n"
        "    - 440\n"
        "\n"
        "# Comments about notify\n"
        "notify:\n"
        "  command: echo\n"
    )


def test_a_new_section_follows_a_compact_file_compactly():
    text = "sources:\n  git:\n  - python/cpython\n  npm:\n  - express\n"
    assert add(text, "pypi", "httpx") == text + "  pypi:\n  - httpx\n"


def test_an_indentless_list_is_appended_to_at_its_own_indent():
    text = "sources:\n  git:\n  - python/cpython\n"
    assert add(text, "git", "torvalds/linux") == text + "  - torvalds/linux\n"


@pytest.mark.parametrize(
    "line, kept",
    [("  npm:", "  npm:"), ("  npm: ~   # later", "  npm: # later"), ("  npm: []", "  npm:")],
)
def test_an_empty_section_is_filled_in(line, kept):
    text = f"sources:\n  git:\n    - python/cpython\n{line}\n"
    assert add(text, "npm", "express") == (
        f"sources:\n  git:\n    - python/cpython\n{kept}\n    - express\n"
    )


def test_a_flow_list_stays_a_flow_list():
    text = 'sources:\n  youtube: ["@NASA", UCLA_DiR1FfKNvjuUpBHmylQ]  # channels\n'
    assert add(text, "youtube", "@Veritasium", "UCsXVk37bltHxD1rDPwtNM8Q") == (
        'sources:\n  youtube: ["@NASA", UCLA_DiR1FfKNvjuUpBHmylQ, "@Veritasium", '
        "UCsXVk37bltHxD1rDPwtNM8Q]  # channels\n"
    )


def test_a_file_without_sources_gets_them_at_the_end():
    text = "privacy:\n  concurrency: 2\n"
    assert add(text, "npm", "express") == (
        "privacy:\n  concurrency: 2\n\nsources:\n  npm:\n    - express\n"
    )


@pytest.mark.parametrize("text", ["", "# just a comment\n"])
def test_an_empty_file_gets_a_sources_section(text):
    edited = add(text, "npm", "express")
    assert edited.endswith("sources:\n  npm:\n    - express\n")
    assert parse_config(yaml.safe_load(edited)).sources == {"npm": ["express"]}


def test_an_empty_sources_section_is_filled_in():
    assert add("sources:\n", "npm", "express") == "sources:\n  npm:\n    - express\n"


def test_an_entry_after_a_block_scalar_goes_after_its_text():
    text = (
        "sources:\n"
        "  feed:\n"
        "    - url: https://example.com/feed.xml\n"
        "      name: |\n"
        "        Example\n"
        "        # not a comment\n"
        "\n"
        "  # trailing\n"
    )
    edited = add(text, "feed", "https://example.org/rss")
    assert "        # not a comment\n    - https://example.org/rss\n\n  # trailing\n" in edited


def test_windows_line_ends_are_kept():
    text = "sources:\r\n  npm:\r\n    - express\r\n"
    assert add(text, "npm", "react") == "sources:\r\n  npm:\r\n    - express\r\n    - react\r\n"


def test_a_section_shared_through_an_anchor_is_refused_with_what_to_do():
    text = "x: &shared\n  - python/cpython\nsources:\n  git: *shared\n"
    with pytest.raises(ConfigError, match=r"by hand:\n  - torvalds/linux"):
        add_to_config(text, "git", ["torvalds/linux"])


def test_a_section_that_is_not_a_list_is_refused():
    with pytest.raises(ConfigError, match="not a list"):
        add_to_config("sources:\n  npm: express\n", "npm", ["react"])


# removing


def test_removing_takes_an_entry_with_its_own_lines_and_comments():
    edited = remove(EXAMPLE_CONFIG, "git", "https://github.com/astral-sh/uv")
    assert "astral-sh/uv" not in edited
    assert "Per-entry rules" not in edited
    assert "    - python/cpython                     # owner/repo means GitHub\n" in edited
    assert is_subsequence(edited.splitlines(), EXAMPLE_CONFIG.splitlines())
    assert len(EXAMPLE_CONFIG.splitlines()) - len(edited.splitlines()) == 4


def test_comments_above_a_removed_entry_stay():
    edited = remove(EXAMPLE_CONFIG, "itch", "https://hempuli.itch.io/baba-is-you")
    assert "  itch:\n    # Watches devlog posts and new builds; most games have one.\n    - url:" in edited


def test_removing_the_last_entry_leaves_the_section_empty():
    edited = remove(EXAMPLE_CONFIG, "homebrew", "wget", "cask:firefox")
    assert "  homebrew:\n\n  pypi:" in edited
    assert "homebrew" not in parse_config(yaml.safe_load(edited)).sources


def test_removing_matches_any_form_and_every_section_of_the_source():
    text = (
        "sources:\n"
        "  github:\n"
        "    - python/cpython\n"
        "  git:\n"
        "    - repo: https://github.com/python/cpython\n"
        "      watch: [commits]\n"
        "    - torvalds/linux\n"
    )
    change = remove_from_config(text, "git", ["github.com/python/cpython"])
    assert change.applied == ["github.com/python/cpython"]
    assert change.text == "sources:\n  github:\n  git:\n    - torvalds/linux\n"


def test_an_entry_the_source_cannot_read_is_removed_when_written_the_same():
    text = "sources:\n  git:\n    - nope\n    - python/cpython\n"
    assert remove(text, "git", "nope") == "sources:\n  git:\n    - python/cpython\n"


def test_a_number_entry_is_removed_by_its_digits():
    assert remove("sources:\n  steam:\n    - 440\n    - 730\n", "steam", "440") == (
        "sources:\n  steam:\n    - 730\n"
    )


def test_an_entry_not_watched_is_reported_and_nothing_changes():
    change = remove_from_config(EXAMPLE_CONFIG, "npm", ["left-pad"])
    assert change.skipped == ["left-pad"]
    assert change.applied == []
    assert change.text == EXAMPLE_CONFIG


@pytest.mark.parametrize(
    "value, left",
    [("440", "[730, 570]"), ("730", "[440, 570]"), ("570", "[440, 730]")],
)
def test_removing_from_a_flow_list_takes_one_comma(value, left):
    text = "sources:\n  steam: [440, 730, 570]  # games\n"
    assert remove(text, "steam", value) == f"sources:\n  steam: {left}  # games\n"


def test_removing_the_only_flow_entry_leaves_an_empty_list():
    assert remove("sources:\n  steam: [440]\n", "steam", "440") == "sources:\n  steam: []\n"


def test_a_dash_on_its_own_line_goes_with_its_entry():
    text = "sources:\n  git:\n    -\n      repo: python/cpython\n    - torvalds/linux\n"
    assert remove(text, "git", "python/cpython") == "sources:\n  git:\n    - torvalds/linux\n"


# CLI


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return path


def test_add_and_rm_write_the_config(config, capsys):
    assert main(["add", "-c", str(config), "npm", "left-pad"]) == 0
    assert "left-pad" in load_config(config).sources["npm"]
    assert "Added left-pad under npm" in capsys.readouterr().out

    assert main(["rm", "-c", str(config), "npm", "left-pad"]) == 0
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG
    assert main(["remove", "-c", str(config), "npm", "left-pad"]) == 0
    assert "Not watching left-pad" in capsys.readouterr().out


def test_a_config_path_with_a_space_keeps_one_style(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "500")
    monkeypatch.delenv("NO_COLOR", raising=False)
    path = tmp_path / "Application Support" / "youpdated.yaml"
    path.parent.mkdir()
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    assert main(["add", "-c", str(path), "npm", "left-pad"]) == 0
    assert f"\x1b[1m{path}\x1b[0m" in capsys.readouterr().out


def test_test_shows_the_change_and_writes_nothing(config, capsys):
    assert main(["add", "--test", "-c", str(config), "steam", "570"]) == 0
    out = capsys.readouterr().out
    assert "+    - 570" in out
    assert "nothing was written" in out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_a_rejected_entry_is_a_config_error_and_writes_nothing(config, capsys):
    assert main(["add", "-c", str(config), "git", "nope"]) == 1
    assert "Config error" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_add_without_a_config_says_to_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("youpdated.config.find_config", lambda explicit=None: None)
    assert main(["add", "npm", "express"]) == 1
    assert "youpdated init" in capsys.readouterr().out


@pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")
def test_an_encrypted_config_stays_encrypted(config, monkeypatch):
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    config.write_bytes(crypto.encrypt(EXAMPLE_CONFIG.encode("utf-8"), PASSPHRASE))

    assert main(["add", "-c", str(config), "npm", "left-pad"]) == 0
    assert crypto.is_encrypted_file(config)
    assert b"left-pad" not in config.read_bytes()
    assert "left-pad" in load_config(config, passphrase=PASSPHRASE).sources["npm"]


@pytest.mark.skipif(os.name != "posix", reason="symlinks and permission bits")
def test_a_symlinked_config_keeps_its_link_and_permissions(tmp_path, config):
    config.chmod(0o640)
    link = tmp_path / "link.yaml"
    link.symlink_to(config)

    assert main(["add", "-c", str(link), "npm", "left-pad"]) == 0
    assert link.is_symlink()
    assert "left-pad" in config.read_text(encoding="utf-8")
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


# guessing the source


@pytest.mark.parametrize(
    "value, source, entry",
    [
        ("https://github.com/python/cpython", "git", "https://github.com/python/cpython"),
        ("python/cpython", "git", "python/cpython"),
        ("https://gitlab.gnome.org/GNOME/gtk", "git", "https://gitlab.gnome.org/GNOME/gtk"),
        ("https://github.com/o/r/releases.atom", "feed", "https://github.com/o/r/releases.atom"),
        ("https://example.com/blog/feed/", "feed", "https://example.com/blog/feed/"),
        ("440", "steam", "440"),
        ("https://store.steampowered.com/app/440/TF2/", "steam", "https://store.steampowered.com/app/440/TF2/"),
        ("https://hempuli.itch.io/baba-is-you", "itch", "https://hempuli.itch.io/baba-is-you"),
        ("@NASA", "youtube", "@NASA"),
        ("https://www.youtube.com/@NASA", "youtube", "https://www.youtube.com/@NASA"),
        ("chrome", "browser", "chrome"),
        ("cask:firefox", "homebrew", "cask:firefox"),
        ("https://formulae.brew.sh/formula/wget", "homebrew", "wget"),
        ("https://formulae.brew.sh/cask/firefox", "homebrew", "cask:firefox"),
        ("@types/node", "npm", "@types/node"),
        ("https://www.npmjs.com/package/@types/node", "npm", "@types/node"),
        ("https://www.npmjs.com/package/react/v/18.0.0", "npm", "react"),
        ("https://pypi.org/project/httpx/0.27.0/", "pypi", "httpx"),
        # No other source claims -> they're pages
        ("https://example.com", "webpage", "https://example.com"),
        ("https://git.example.org/a/b", "webpage", "https://git.example.org/a/b"),
    ],
)
def test_a_source_is_guessed_from_an_entry_only_it_could_be(value, source, entry):
    assert guess_source(value) == (source, entry)


@pytest.mark.parametrize("value", ["express", "hashicorp/tap/terraform"])
def test_an_entry_any_source_could_be_asks_for_one(value):
    with pytest.raises(ConfigError, match=rf"provide a source type: youpdated rm \[source type\] {value}"):
        guess_source(value, "rm")


class _Mirror:
    """Claims everything"""

    name = "mirror"

    def claim(self, value):
        return value


class _Fallback(_Mirror):
    name = "fallback"
    fallback = True


def test_an_entry_two_sources_claim_names_both(monkeypatch):
    monkeypatch.setattr("youpdated.edit.all_sources", lambda: {"mirror": _Mirror(), "steam": STEAM})
    with pytest.raises(ConfigError, match="`440` could be mirror or steam"):
        guess_source("440")


def test_a_fallback_claim_gives_way_to_any_other(monkeypatch):
    monkeypatch.setattr(
        "youpdated.edit.all_sources", lambda: {"fallback": _Fallback(), "steam": STEAM}
    )
    assert guess_source("440") == ("steam", "440")
    assert guess_source("anything") == ("fallback", "anything")


def test_two_fallback_claims_name_both(monkeypatch):
    other = type("Other", (_Fallback,), {"name": "other"})
    monkeypatch.setattr(
        "youpdated.edit.all_sources", lambda: {"fallback": _Fallback(), "other": other()}
    )
    with pytest.raises(ConfigError, match="`x` could be fallback or other"):
        guess_source("x")


def test_add_without_a_source_guesses_one_per_entry(config, capsys):
    words = ["https://github.com/torvalds/linux", "https://pypi.org/project/rich/", "@types/react"]
    assert main(["add", "-c", str(config), *words]) == 0
    sources = load_config(config).sources
    assert "https://github.com/torvalds/linux" in sources["git"]
    assert "rich" in sources["pypi"]
    assert "@types/react" in sources["npm"]
    out = capsys.readouterr().out
    assert "Added rich under pypi" in out and "Added @types/react under npm" in out


def test_a_bare_name_without_a_source_is_refused(config, capsys):
    assert main(["add", "-c", str(config), "express"]) == 1
    assert "youpdated add [source type] express" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_a_source_with_nothing_after_it_is_refused(config, capsys):
    assert main(["add", "-c", str(config), "git"]) == 1
    assert "name what to change" in capsys.readouterr().out


def test_rm_guesses_the_source_too(config):
    assert main(["rm", "-c", str(config), "https://store.steampowered.com/app/730/"]) == 0
    assert load_config(config).sources["steam"] == [440]


# --wipe


def _history(path, passphrase=None):
    with State(path, passphrase=passphrase) as state:
        for target in ("github.com/python/cpython", "gitlab.com/gitlab-org/cli"):
            state.mark_seen([Update(source="git", target=target, uid=u, title=u, url="") for u in "ab"])
            state.mark_baselined([("git", target)])
            state.mark_ignore_rules([("git", target, [frozenset({"commit"})])])
            state.mark_highest_versions([("git", target, "3.14")])
        state.mark_seen([Update(source="steam", target="440", uid="n1", title="n1", url="")])
        state.mark_baselined([("steam", "440")])
        state.cache_set("steam_app_names", "440", "Team Fortress 2")
        for url in (CPYTHON_API, CPYTHON_ATOM, STEAM_NEWS, SHARED, UNRELATED):
            state.remember_validators(url, '"etag"', None)
        # STEAM_NEWS is unlinked, as stored before links were kept: only a probe finds it
        state.link_urls([
            ("git", "github.com/python/cpython", CPYTHON_API),
            ("git", "github.com/python/cpython", SHARED),
            ("git", "gitlab.com/gitlab-org/cli", SHARED),
        ])


def _rows(path, passphrase=None):
    with State(path, passphrase=passphrase) as state:
        conn = state._conn
        return (
            {tuple(r) for r in conn.execute("SELECT source, target FROM seen")},
            {tuple(r) for r in conn.execute("SELECT namespace, key FROM kv WHERE namespace != 'meta'")},
        )


def _cached(path, passphrase=None):
    with State(path, passphrase=passphrase) as state:
        return {r["url"] for r in state._conn.execute("SELECT url FROM http_cache")}


def test_forget_deletes_a_target_and_nothing_else(tmp_path):
    path = tmp_path / "state.sqlite3"
    _history(path)
    with State(path) as state:
        assert state.forget("steam", ["440"], ["steam_app_names"]) == (1, 0)
    seen, kv = _rows(path)
    assert ("steam", "440") not in seen
    assert not any("440" in key for _, key in kv)
    assert ("git", "github.com/python/cpython") in seen
    assert ("baseline", "git:github.com/python/cpython") in kv
    assert STEAM_NEWS in _cached(path), "an unlinked URL goes only when named"


def test_forget_clears_linked_and_named_addresses_but_not_shared_ones(tmp_path):
    path = tmp_path / "state.sqlite3"
    _history(path)
    with State(path) as state:
        seen, validators = state.forget(
            "git", ["github.com/python/cpython"], urls=[CPYTHON_ATOM, SHARED]
        )
        links = {tuple(r) for r in state._conn.execute("SELECT source, target, url FROM http_cache_targets")}
    assert (seen, validators) == (2, 2)
    assert _cached(path) == {STEAM_NEWS, SHARED, UNRELATED}
    assert links == {("git", "gitlab.com/gitlab-org/cli", SHARED)}


def test_remove_wipe_forgets_the_history_of_what_it_removed(config, tmp_path, capsys, monkeypatch):
    # With a token, git asks the API instead of the atom feed the probe should find
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    state = tmp_path / "state.sqlite3"
    _history(state)
    args = ["-c", str(config), "--state", str(state)]

    assert main(["rm", "--wipe", *args, "https://github.com/python/cpython"]) == 0
    assert "python/cpython" not in config.read_text(encoding="utf-8")
    seen, kv = _rows(state)
    assert ("git", "github.com/python/cpython") not in seen
    assert not any(key.endswith("github.com/python/cpython") for _, key in kv)
    assert ("git", "gitlab.com/gitlab-org/cli") in seen
    assert ("ignore", "git:gitlab.com/gitlab-org/cli") in kv
    out = capsys.readouterr().out
    assert "Forgot the history of github.com/python/cpython (2 seen item(s), 2 cached" in out
    # The API URL by its link, the atom feed by asking the source what it fetches
    assert _cached(state) == {STEAM_NEWS, SHARED, UNRELATED}


def test_remove_wipe_forgets_something_already_removed(config, tmp_path):
    state = tmp_path / "state.sqlite3"
    _history(state)
    args = ["-c", str(config), "--state", str(state)]
    assert main(["rm", *args, "steam", "440"]) == 0
    assert ("steam", "440") in _rows(state)[0]

    assert main(["rm", "--wipe", *args, "steam", "440"]) == 0
    seen, kv = _rows(state)
    assert ("steam", "440") not in seen
    assert ("steam_app_names", "440") not in kv
    assert STEAM_NEWS not in _cached(state)


def test_remove_wipe_test_changes_nothing(config, tmp_path, capsys):
    state = tmp_path / "state.sqlite3"
    _history(state)
    before = state.read_bytes()
    assert main(["rm", "--wipe", "--test", "-c", str(config), "--state", str(state), "440"]) == 0
    assert "would forget the history of steam: 440" in capsys.readouterr().out
    assert state.read_bytes() == before
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_remove_without_wipe_keeps_history(config, tmp_path):
    state = tmp_path / "state.sqlite3"
    _history(state)
    assert main(["rm", "-c", str(config), "--state", str(state), "440"]) == 0
    assert ("steam", "440") in _rows(state)[0]


@pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")
def test_remove_wipe_on_an_encrypted_state_keeps_it_encrypted(config, tmp_path, monkeypatch):
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    state = tmp_path / "state.sqlite3"
    _history(state, PASSPHRASE)

    assert main(["rm", "--wipe", "-c", str(config), "--state", str(state), "440"]) == 0
    assert crypto.is_encrypted_file(state)
    assert not crypto.is_encrypted_file(config), "a plain config stays plain"
    assert ("steam", "440") not in _rows(state, PASSPHRASE)[0]
