"""The `git` source: GitHub, GitLab, and Forgejo/Gitea with one entry"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
import respx

from youpdated.config import parse_config
from youpdated.models import Update
from youpdated.registry import get_source
from youpdated.runner import build_targets, run
from youpdated.sources.base import ConfigEntryError
from youpdated.state import _SCHEMA, State

from .conftest import fixture

GL_API = "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli"
CB_API = "https://codeberg.org/api/v1/repos/forgejo/forgejo"


def one(entry):
    (target,) = get_source("git").targets([entry])
    return target


# config


@pytest.mark.parametrize(
    "entry,key,forge",
    [
        ("python/cpython", "github.com/python/cpython", "github"),
        ("https://github.com/astral-sh/uv/releases", "github.com/astral-sh/uv", "github"),
        ("https://gitlab.com/gitlab-org/cli", "gitlab.com/gitlab-org/cli", "gitlab"),
        ("https://gitlab.com/gitlab-org/cli/-/releases", "gitlab.com/gitlab-org/cli", "gitlab"),
        ("gitlab.com/inkscape/inbox/issues", "gitlab.com/inkscape/inbox/issues", "gitlab"),
        ("https://codeberg.org/forgejo/forgejo.git", "codeberg.org/forgejo/forgejo", "forgejo"),
        ("codeberg.org/forgejo/forgejo", "codeberg.org/forgejo/forgejo", "forgejo"),
        ({"repo": "gitlab-org/cli", "host": "gitlab.com"}, "gitlab.com/gitlab-org/cli", "gitlab"),
        ("https://gitlab.gnome.org/GNOME/gtk", "gitlab.gnome.org/GNOME/gtk", "gitlab"),
        ("https://gitea.example.org/o/r", "gitea.example.org/o/r", "forgejo"),
        ({"repo": "o/r", "host": "git.example.org", "forge": "gitea"}, "git.example.org/o/r",
         "forgejo"),
    ],
)
def test_entries_normalize(entry, key, forge):
    target = one(entry)
    assert target.key == key
    assert target.params["forge"] == forge


@pytest.mark.parametrize(
    "entry",
    [
        "not a repo",
        "https://git.example.org/o/r",                              # unknown forge
        {"repo": "o/r", "host": "git.example.org", "forge": "svn"},
        {"repo": "https://gitlab.com/a/b", "host": "codeberg.org"},  # contradicts itself
        {"repo": "a/b", "watch": ["nonsense"]},
        {"repo": "a/b", "token_env": "glpat-abc123-not-a-name"},
        "https://gitlab.com/onlygroup",
        "ftp://gitlab.com/a/b",
    ],
)
def test_bad_entries_are_rejected(entry):
    with pytest.raises(ConfigEntryError):
        get_source("git").targets([entry])


def test_default_tokens_stay_on_their_own_host():
    assert one("python/cpython").params["token_env"] == "GITHUB_TOKEN"
    assert one("gitlab.com/a/b").params["token_env"] == "GITLAB_TOKEN"
    assert one("codeberg.org/a/b").params["token_env"] is None
    assert one("https://gitlab.gnome.org/a/b").params["token_env"] is None
    assert one({"repo": "gitlab.gnome.org/a/b", "token_env": "GNOME_TOKEN"}).params[
        "token_env"
    ] == "GNOME_TOKEN"


# GitLab


@respx.mock
def test_gitlab_releases(client):
    respx.get(f"{GL_API}/releases?per_page=20").mock(
        return_value=httpx.Response(200, content=fixture("gitlab_releases.json"))
    )
    target = one("https://gitlab.com/gitlab-org/cli")
    updates = list(get_source("git").fetch(target, client))

    assert [u.uid for u in updates] == ["release:v1.123.0", "release:v1.122.0", "release:v1.121.0"]
    first = updates[0]
    assert first.source == "git" and first.target == "gitlab.com/gitlab-org/cli"
    assert first.url == "https://gitlab.com/gitlab-org/cli/-/releases/v1.123.0"
    assert first.version == "v1.123.0" and first.tags == ("release",)
    assert first.published and first.published.tzinfo
    assert first.body and "Changelog" in first.body


@respx.mock
def test_gitlab_tags_and_commits_on_a_branch(client):
    respx.get(f"{GL_API}/repository/tags?per_page=20").mock(
        return_value=httpx.Response(200, content=fixture("gitlab_tags.json"))
    )
    commits = respx.get(f"{GL_API}/repository/commits?per_page=20&ref_name=main").mock(
        return_value=httpx.Response(200, content=fixture("gitlab_commits.json"))
    )
    target = one({"repo": "gitlab.com/gitlab-org/cli", "watch": ["tags", "commits"],
                  "branch": "main"})
    updates = list(get_source("git").fetch(target, client))

    tags = [u for u in updates if "tag" in u.tags]
    assert [u.uid for u in tags] == ["tag:v1.123.0", "tag:v1.122.0", "tag:v1.121.0"]
    assert tags[0].url == "https://gitlab.com/gitlab-org/cli/-/tags/v1.123.0"
    assert commits.called
    found = [u for u in updates if "commit" in u.tags]
    assert len(found) == 3
    assert found[0].uid == "commit:a300719a2aac26acb25eee65fe08398188c99a0e"
    assert found[0].title.startswith("Merge branch")
    assert found[0].version is None


@respx.mock
def test_gitlab_infers_prereleases_from_the_tag(client):
    respx.get(f"{GL_API}/releases?per_page=20").mock(
        return_value=httpx.Response(200, json=[
            {"tag_name": "v2.0.0-rc.1", "name": "v2.0.0-rc.1"},
            {"tag_name": "v1.9.0", "name": "v1.9.0"},
        ])
    )
    updates = list(get_source("git").fetch(one("gitlab.com/gitlab-org/cli"), client))
    assert updates[0].tags == ("release", "prerelease")
    assert updates[1].tags == ("release",)


@respx.mock
def test_gitlab_token_is_sent_to_gitlab_com_only(client, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")
    on_com = respx.get(f"{GL_API}/releases?per_page=20").mock(
        return_value=httpx.Response(200, json=[])
    )
    self_hosted = respx.get(
        "https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgtk/releases?per_page=20"
    ).mock(return_value=httpx.Response(200, json=[]))

    source = get_source("git")
    list(source.fetch(one("gitlab.com/gitlab-org/cli"), client))
    list(source.fetch(one("https://gitlab.gnome.org/GNOME/gtk"), client))

    assert on_com.calls.last.request.headers["PRIVATE-TOKEN"] == "glpat-secret"
    assert "PRIVATE-TOKEN" not in self_hosted.calls.last.request.headers


@respx.mock
def test_nested_gitlab_groups_are_encoded_whole(client):
    route = respx.get(
        "https://gitlab.com/api/v4/projects/group%2Fsub%2Fproject/releases?per_page=20"
    ).mock(return_value=httpx.Response(200, json=[]))
    target = one("https://gitlab.com/group/sub/project/-/tags")
    assert target.key == "gitlab.com/group/sub/project"
    list(get_source("git").fetch(target, client))
    assert route.called


# Forgejo / Gitea


@respx.mock
def test_forgejo_releases(client):
    respx.get(f"{CB_API}/releases?limit=20").mock(
        return_value=httpx.Response(200, content=fixture("forgejo_releases.json"))
    )
    updates = list(get_source("git").fetch(one("codeberg.org/forgejo/forgejo"), client))

    assert [u.uid for u in updates] == ["release:v17.0.0", "release:v16.0.6", "release:v15.0.10"]
    assert updates[0].url == "https://codeberg.org/forgejo/forgejo/releases/tag/v17.0.0"
    assert updates[0].published and updates[0].published.tzinfo


@respx.mock
def test_forgejo_skips_drafts_and_uses_the_prerelease_flag(client):
    respx.get(f"{CB_API}/releases?limit=20").mock(
        return_value=httpx.Response(200, json=[
            {"tag_name": "v3.0.0", "draft": True},
            {"tag_name": "v2.0.0", "prerelease": True},
            {"tag_name": "v1.0.0"},
        ])
    )
    updates = list(get_source("git").fetch(one("codeberg.org/forgejo/forgejo"), client))
    assert [(u.version, u.tags) for u in updates] == [
        ("v2.0.0", ("release", "prerelease")),
        ("v1.0.0", ("release",)),
    ]


@respx.mock
def test_forgejo_tags_and_commits(client):
    respx.get(f"{CB_API}/tags?limit=20").mock(
        return_value=httpx.Response(200, content=fixture("forgejo_tags.json"))
    )
    respx.get(f"{CB_API}/commits?limit=20&stat=false&verification=false&files=false").mock(
        return_value=httpx.Response(200, content=fixture("forgejo_commits.json"))
    )
    target = one({"repo": "codeberg.org/forgejo/forgejo", "watch": ["tags", "commits"]})
    updates = list(get_source("git").fetch(target, client))

    tags = [u for u in updates if "tag" in u.tags]
    assert tags[0].uid == "tag:v17.0.0"
    assert tags[0].url == "https://codeberg.org/forgejo/forgejo/src/tag/v17.0.0"
    assert tags[0].published is not None
    commits = [u for u in updates if "commit" in u.tags]
    assert commits[0].uid == "commit:0475ce4868b9110dd69c7e4a4f24d37ff5fd1e10"
    assert commits[0].title == "chore: migrate toast module to Typescript (#14890)"


@respx.mock
@pytest.mark.parametrize(
    "entry,url",
    [
        ("codeberg.org/forgejo/forgejo", f"{CB_API}/releases?limit=20"),
        ("gitlab.com/gitlab-org/cli", f"{GL_API}/releases?per_page=20"),
    ],
)
def test_forge_apis_are_not_sent_a_browser_user_agent(client, entry, url):
    # Scraper walls in front of forges turn browser strings away
    route = respx.get(url).mock(return_value=httpx.Response(200, json=[]))
    for _ in range(10):
        list(get_source("git").fetch(one(entry), client))
    agents = {call.request.headers["User-Agent"] for call in route.calls}
    assert agents == {f"python-httpx/{httpx.__version__}"}


def test_a_fixed_user_agent_is_still_respected(client):
    client.privacy.user_agent = "my-agent/1.0"
    with respx.mock:
        route = respx.get(f"{CB_API}/releases?limit=20").mock(
            return_value=httpx.Response(200, json=[])
        )
        list(get_source("git").fetch(one("codeberg.org/forgejo/forgejo"), client))
    assert route.calls.last.request.headers["User-Agent"] == "my-agent/1.0"


@respx.mock
def test_forgejo_token_needs_token_env(client, monkeypatch):
    monkeypatch.setenv("CODEBERG_TOKEN", "abc")
    route = respx.get(f"{CB_API}/releases?limit=20").mock(
        return_value=httpx.Response(200, json=[])
    )
    source = get_source("git")
    list(source.fetch(one("codeberg.org/forgejo/forgejo"), client))
    assert "Authorization" not in route.calls.last.request.headers

    list(source.fetch(
        one({"repo": "codeberg.org/forgejo/forgejo", "token_env": "CODEBERG_TOKEN"}), client
    ))
    assert route.calls.last.request.headers["Authorization"] == "token abc"


# GitHub


@respx.mock
def test_github_entries_are_git_entries(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get("https://github.com/python/cpython/releases.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    config = parse_config({"sources": {"github": ["python/cpython"]}})
    (target,) = build_targets(config)[0]
    assert (target.source, target.key) == ("git", "github.com/python/cpython")

    updates = list(get_source("git").fetch(target, client))
    assert updates and {(u.source, u.target) for u in updates} == {
        ("git", "github.com/python/cpython")
    }


def test_github_ignore_rules_still_cover_github_entries():
    config = parse_config({
        "sources": {"github": ["a/one"], "git": ["gitlab.com/b/two"]},
        "ignore": {"github": ["commit"], "git": ["tag"]},
    })
    by_key = {t.key: t.ignore for t in build_targets(config)[0]}
    assert by_key == {
        "github.com/a/one": frozenset({"commit", "tag"}),
        "gitlab.com/b/two": frozenset({"tag"}),
    }


@respx.mock
def test_source_filter_accepts_the_old_name(state, client):
    respx.get(f"{CB_API}/releases?limit=20").mock(return_value=httpx.Response(200, json=[]))
    config = parse_config({"sources": {"git": ["codeberg.org/forgejo/forgejo"], "npm": ["x"]}})
    result = run(config, state, client, only_sources=["github"])
    assert [t.key for t in result.targets] == ["codeberg.org/forgejo/forgejo"]


@respx.mock
def test_a_repo_listed_under_both_names_reports_once(state, client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get("https://github.com/python/cpython/releases.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    config = parse_config(
        {"sources": {"github": ["python/cpython"], "git": ["https://github.com/python/cpython"]}}
    )
    result = run(config, state, client, show_all=True)
    uids = [u.uid for u in result.updates]
    assert uids and len(uids) == len(set(uids))


def test_github_history_moves_to_git(tmp_path):
    path = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    now = "2026-10-01T00:00:00+00:00"
    conn.executemany("INSERT INTO seen VALUES (?,?,?,?,?)", [
        ("github", "python/cpython", "release:v3.15.0", now, now),
        ("github", "python/cpython", "release:v3.14.0", now, now),
        # Already seen under `git` as well: the old copy just goes
        ("git", "github.com/python/cpython", "release:v3.14.0", now, now),
        ("npm", "express", "version:5.0.0", now, now),
    ])
    conn.executemany("INSERT INTO kv VALUES (?,?,?)", [
        ("baseline", "github:python/cpython", now),
        ("ignore", "github:python/cpython", "prerelease"),
        ("cache", "github:unrelated", "kept"),
    ])
    conn.commit()
    conn.close()

    with State(path) as state:
        assert not state.is_new(
            Update("git", "github.com/python/cpython", "release:v3.15.0", "", "")
        )
        assert state.is_baselined("git", "github.com/python/cpython")
        assert not state.ignore_rules_changed(
            "git", "github.com/python/cpython", frozenset({"prerelease"})
        )
        assert state.cache_get("cache", "github:unrelated") == "kept"
        assert state.seen_count() == 3

    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM seen WHERE source = 'github'").fetchall()


@respx.mock
def test_github_enterprise_uses_its_own_api_and_never_github_com_token(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-public")
    monkeypatch.setenv("CORP_TOKEN", "ghp-corp")
    atom = respx.get("https://github.corp.com/team/app/releases.atom").mock(
        return_value=httpx.Response(200, content=fixture("github_releases.atom"))
    )
    api = respx.get("https://github.corp.com/api/v3/repos/team/app/releases?per_page=20").mock(
        return_value=httpx.Response(200, json=[])
    )
    source = get_source("git")

    list(source.fetch(one("https://github.corp.com/team/app"), client))
    assert atom.called and not api.called

    list(source.fetch(
        one({"repo": "https://github.corp.com/team/app", "token_env": "CORP_TOKEN"}), client
    ))
    assert api.calls.last.request.headers["Authorization"] == "Bearer ghp-corp"


# runner


@respx.mock
def test_git_targets_baseline_then_report_new(state, client):
    route = respx.get(f"{CB_API}/releases?limit=20")
    releases = json.loads(fixture("forgejo_releases.json"))
    route.mock(return_value=httpx.Response(200, json=releases[1:]))
    config = parse_config({"sources": {"git": ["https://codeberg.org/forgejo/forgejo"]}})

    first = run(config, state, client)
    assert first.baseline and not first.updates

    route.mock(return_value=httpx.Response(200, json=releases))
    second = run(config, state, client)
    assert [u.uid for u in second.updates] == ["release:v17.0.0"]
