"""The library API: only names from ``youpdated.__all__``"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
import respx

import youpdated

from .conftest import FIXTURES, fixture

# Changing this list changes the library's semver promise
PUBLIC = {
    "__version__",
    "newer", "NoVersionsError", "parse_config", "load_config",
    "Config", "PrivacyConfig", "ConfigError",
    "State", "StateError", "StateBusy",
    "Client", "Fetched", "FetchError", "ProxyUnavailable",
    "run", "record", "RunResult", "ProgressReporter",
    "Update", "Target", "RunError",
    "Source", "ConfigEntryError",
    "entry_fields", "require", "parse_iso", "url_parts", "parse_feed",
    "register", "all_sources", "get_source",
    "EncryptionError", "WrongPassphrase", "YoupdatedError",
}

REPO = Path(__file__).resolve().parent.parent
PYPI = "https://pypi.org/simple/httpx/"
RELEASES = "https://github.com/python/cpython/releases.atom"
LATEST = "https://github.com/python/cpython/releases/latest"
UA = f"Youpdated/{youpdated.__version__} (+https://github.com/Void1-1/youpdated)"


def _pypi(versions: list[str] | None = None) -> httpx.Response:
    """The captured httpx index, or one listing ``versions`` a day apart, oldest first"""
    doc = json.loads(fixture("pypi_httpx_simple.json"))
    if versions is not None:
        # PyPI lists a version only while it has a file left
        doc["versions"] = versions
        doc["files"] = [
            {
                "filename": f"httpx-{version}.tar.gz",
                "upload-time": f"2026-01-{day:02d}T12:00:00Z",
                "url": f"https://files.example/httpx-{version}.tar.gz",
                "yanked": False,
            }
            for day, version in enumerate(versions, start=1)
        ]
    return httpx.Response(200, json=doc)


@pytest.fixture
def quick():
    """A library client that doesn't wait between requests"""
    privacy = youpdated.PrivacyConfig(user_agent="youpdated", jitter=(0.0, 0.0))
    with youpdated.Client(privacy, product="Host/2.0") as client:
        client.retry_backoff = 0
        yield client


def _versions(updates) -> list[str]:
    return [u.version for u in updates]


# public names


def test_all_is_the_pinned_public_api():
    assert set(youpdated.__all__) == PUBLIC
    assert len(youpdated.__all__) == len(PUBLIC)
    for name in youpdated.__all__:
        assert getattr(youpdated, name) is not None, name
    assert PUBLIC <= set(dir(youpdated))
    assert set(youpdated.__all__) - {"__version__"} == set(youpdated._EXPORTS)


# What docs/library.md promises on each public class and function; the rest is internal
PROMISED_PARAMS = {
    "newer": {"source", "entry", "current", "versions", "ignore", "prereleases", "client", "product"},
    "parse_config": {"raw", "allow_empty"},
    "load_config": {"path", "passphrase", "allow_empty"},
    "State": {"path", "passphrase", "expiry", "read_only"},
    "Client": {"privacy", "state", "product"},
    "run": {"config", "state", "client", "only_sources", "save", "progress", "by"},
    "record": {"state", "result", "by"},
    "entry_fields": {"entry", "scalar_key", "source"},
    "require": {"fields", "key", "source"},
    "parse_iso": {"value"},
    "url_parts": {"value"},
    "parse_feed": {"content", "source", "target", "tags"},
}
PROMISED_MEMBERS = {
    "Config": {"sources", "privacy", "warnings", "expiry"},
    "PrivacyConfig": {"proxy", "user_agent", "jitter", "concurrency", "timeout", "fast_mode"},
    "State": {"close", "__enter__", "__exit__", "cache_get", "cache_set"},
    "Client": {"close", "__enter__", "__exit__", "get", "state"},
    "Fetched": {"url", "status", "content", "headers", "text", "json"},
    "RunResult": {"updates", "errors", "warnings", "baseline", "baselined", "ok"},
    "ProgressReporter": {"run_started", "target_started", "target_finished"},
    "Update": {
        "source", "target", "uid", "title", "url", "published", "version", "body", "tags",
        "aliases", "dedupe_key", "to_dict",
    },
    "Target": {"source", "key", "label", "params", "display", "track_latest"},
    "RunError": {"source", "target", "message", "key"},
}


@pytest.mark.parametrize("name", sorted(PROMISED_PARAMS))
def test_promised_arguments_are_there(name):
    import inspect

    params = set(inspect.signature(getattr(youpdated, name)).parameters)
    assert PROMISED_PARAMS[name] <= params, PROMISED_PARAMS[name] - params


@pytest.mark.parametrize("name", sorted(PROMISED_MEMBERS))
def test_promised_members_are_there(name):
    import dataclasses

    cls = getattr(youpdated, name)
    fields = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
    # Client sets `state` in __init__
    instance_only = {"state"} if name == "Client" else set()
    missing = {m for m in PROMISED_MEMBERS[name] - instance_only if m not in fields and not hasattr(cls, m)}
    assert not missing, missing


def test_client_get_takes_the_documented_arguments():
    import inspect

    params = inspect.signature(youpdated.Client.get).parameters
    assert {"url", "headers", "conditional", "soft_statuses", "retries"} <= set(params)
    assert all(params[name].kind is inspect.Parameter.KEYWORD_ONLY
               for name in ("headers", "conditional", "soft_statuses", "retries"))


@pytest.mark.parametrize("name", ["Update", "Target", "RunError", "PrivacyConfig"])
def test_public_dataclasses_take_keywords_only(name):
    """So a field added in a minor release never shifts anyone's arguments"""
    import dataclasses

    assert all(f.kw_only for f in dataclasses.fields(getattr(youpdated, name)))


def test_errors_are_related_as_documented():
    errors = [
        "ConfigError", "ConfigEntryError", "StateError", "StateBusy", "FetchError",
        "ProxyUnavailable", "EncryptionError", "WrongPassphrase", "NoVersionsError",
    ]
    for name in errors:
        assert issubclass(getattr(youpdated, name), youpdated.YoupdatedError), name
    assert issubclass(youpdated.StateBusy, youpdated.StateError)
    assert issubclass(youpdated.WrongPassphrase, youpdated.EncryptionError)
    assert issubclass(youpdated.ProxyUnavailable, youpdated.FetchError)
    assert not issubclass(youpdated.ConfigEntryError, youpdated.ConfigError)


def test_adding_sources_imports_only_public_names():
    """A plugin following the guide keeps working through 1.x"""
    import ast
    import re

    guide = (REPO / "ADDING_SOURCES.md").read_text(encoding="utf-8")
    seen = set()
    for block in re.findall(r"```python\n(.*?)```", guide, re.S):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("youpdated"):
                names = {alias.name for alias in node.names}
                seen |= names
                if node.module == "youpdated":
                    assert names <= set(youpdated.__all__), names - set(youpdated.__all__)
                else:
                    # Only the provisional claim() helper lives outside the API
                    assert (node.module, names) == ("youpdated.sources.base", {"accepted"}), node.module
    assert {"Target", "Update", "Client", "register", "entry_fields", "parse_feed"} <= seen


def test_unknown_names_still_raise_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'runner_thing'"):
        youpdated.runner_thing  # noqa: B018


@respx.mock
def test_dependency_tracker_flow_with_public_names_only():
    route = respx.get(PYPI).mock(return_value=_pypi(["0.27.0", "0.28.0"]))
    config = youpdated.parse_config({
        "sources": {"pypi": ["httpx"]},
        "settings": {"privacy": {"jitter": [0, 0]}},
    })
    assert isinstance(config, youpdated.Config)

    with youpdated.State() as state, youpdated.Client(config.privacy, state, product="Host/2.0") as client:
        first = youpdated.run(config, state, client, by="library")
        assert isinstance(first, youpdated.RunResult)
        assert first.ok and first.baseline and first.updates == []
        assert [t.key for t in first.baselined] == ["httpx"]

        route.mock(return_value=_pypi(["0.27.0", "0.28.0", "0.28.1"]))
        second = youpdated.run(config, state, client, by="Host")
        assert [u.version for u in second.updates] == ["0.28.1"]
        assert all(isinstance(u, youpdated.Update) for u in second.updates)
        assert state.cache_get("app", "last_run_by") == "Host"
        # A host that doesn't say isn't recorded as the CLI
        youpdated.run(config, state, client)
        assert state.cache_get("app", "last_run_by") == "library"

    assert route.calls.last.request.headers["User-Agent"] == f"Host/2.0 {UA}"


def test_sources_are_listed_and_named():
    sources = youpdated.all_sources()
    assert {"pypi", "npm", "git"} <= set(sources)
    assert youpdated.get_source("github") is sources["git"]
    assert isinstance(sources["pypi"], youpdated.Source)


# configs


def test_library_config_names_itself_unless_told_otherwise():
    assert youpdated.parse_config({"sources": {}}).privacy.user_agent == "youpdated"
    told = youpdated.parse_config({"sources": {}, "settings": {"privacy": {"user_agent": "rotate"}}})
    assert told.privacy.user_agent == "rotate"


def test_cli_config_still_rotates_by_default():
    from youpdated.config import parse_config

    assert parse_config({"sources": {"pypi": ["httpx"]}}).privacy.user_agent == "rotate"


def test_library_config_may_be_empty(tmp_path):
    from youpdated.config import parse_config

    assert youpdated.parse_config({}).sources == {}
    with pytest.raises(youpdated.ConfigError, match="sources"):
        youpdated.parse_config({}, allow_empty=False)
    path = tmp_path / "host.yaml"
    path.write_text("sources: {}\n")
    assert youpdated.load_config(path).sources == {}
    with pytest.raises(youpdated.ConfigError, match="has no entries") as caught:
        youpdated.load_config(path, allow_empty=False)
    assert "youpdated add" not in str(caught.value)
    with pytest.raises(youpdated.ConfigError, match="start with `youpdated add`"):
        parse_config({"sources": {}})


def test_load_config_reads_only_the_given_path(tmp_path, monkeypatch):
    path = tmp_path / "host.yaml"
    path.write_text("sources:\n  pypi: [httpx]\n")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "youpdated.yaml").write_text("sources:\n  npm: [express]\n")

    config = youpdated.load_config(path)
    assert config.sources == {"pypi": ["httpx"]}
    assert config.privacy.user_agent == "youpdated"
    assert config.warnings == []


def test_load_config_names_a_missing_file(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(youpdated.ConfigError, match="nope.yaml"):
        youpdated.load_config(missing)
    with pytest.raises(TypeError):
        youpdated.load_config(None)


def test_load_config_decrypts_with_a_passphrase(tmp_path):
    pytest.importorskip("cryptography")
    from youpdated import crypto

    path = tmp_path / "host.yaml"
    path.write_bytes(crypto.encrypt(b"sources:\n  pypi: [httpx]\n", "pw"))
    assert youpdated.load_config(path, passphrase="pw").sources == {"pypi": ["httpx"]}
    with pytest.raises(youpdated.ConfigError, match="passphrase"):
        youpdated.load_config(path)


def test_library_warnings_point_at_no_cli_command(tmp_path):
    from youpdated.config import parse_config

    old_style = {"privacy": {"jitter": [0, 0]}, "sources": {"pypi": ["httpx"]}}
    (warning,) = youpdated.parse_config(old_style).warnings
    assert "`privacy` belongs under `settings:`" in warning
    assert "youpdated edit" not in warning
    path = tmp_path / "host.yaml"
    path.write_text("privacy:\n  jitter: [0, 0]\nsources:\n  pypi: [httpx]\n")
    assert youpdated.load_config(path).warnings == [warning]
    assert parse_config(old_style).warnings == [f"{warning}. Run `youpdated edit` to fix"]


def test_encrypted_history_without_a_passphrase_asks_for_one(tmp_path):
    pytest.importorskip("cryptography")
    path = tmp_path / "history.sqlite3"
    with youpdated.State(path, "pw") as state:
        state.cache_set("host", "key", "value")
    before = path.read_bytes()
    for read_only in (False, True):
        with pytest.raises(youpdated.EncryptionError, match="encrypted; a passphrase is required"):
            youpdated.State(path, read_only=read_only)
    assert path.read_bytes() == before
    with youpdated.State(path, "pw") as state:
        assert state.cache_get("host", "key") == "value"


@respx.mock
def test_client_without_privacy_names_youpdated_and_the_host():
    route = respx.get("https://example.com/").mock(return_value=httpx.Response(200))
    with youpdated.Client() as client:
        client.get("https://example.com/")
    assert route.calls.last.request.headers["User-Agent"] == UA

    with youpdated.Client(product="MyApp/1.2 (+https://myapp.example)") as client:
        client.get("https://example.com/")
    assert route.calls.last.request.headers["User-Agent"] == (
        f"MyApp/1.2 (+https://myapp.example) {UA}"
    )


@respx.mock
def test_privacy_config_names_youpdated_by_default():
    """A host that only sets a proxy or pacing still names itself"""
    route = respx.get("https://example.com/").mock(return_value=httpx.Response(200))
    assert youpdated.PrivacyConfig().user_agent == "youpdated"
    with youpdated.Client(youpdated.PrivacyConfig(jitter=(0.0, 0.0)), product="MyApp/1.2") as client:
        client.get("https://example.com/")
    assert route.calls.last.request.headers["User-Agent"] == f"MyApp/1.2 {UA}"


@respx.mock
def test_product_is_left_off_rotating_and_custom_agents():
    route = respx.get("https://example.com/").mock(return_value=httpx.Response(200))
    for agent in ("rotate", "Custom/1.0"):
        privacy = youpdated.PrivacyConfig(user_agent=agent, jitter=(0.0, 0.0))
        with youpdated.Client(privacy, product="MyApp/1.2") as client:
            client.get("https://example.com/")
        assert "MyApp" not in route.calls.last.request.headers["User-Agent"]


@pytest.mark.parametrize("bad", [
    "MyApp/1.2;", "MyApp/1.2\r\nX-Evil: 1", "", "MyApp/", "(comment)", "MyApp  /1.2",
    "MyApp/1.0 (f\u00fcr \u20ac)", "M\u00e9App/1.0", "MyApp/1.0 (a\\b)",
])
def test_bad_product_tokens_are_refused(bad):
    with pytest.raises(ValueError, match="product"):
        youpdated.Client(product=bad)


@pytest.fixture
def down_proxy():
    """A `settings.privacy` with a closed proxy"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return youpdated.PrivacyConfig(
        proxy=f"socks5://127.0.0.1:{port}", user_agent="youpdated", jitter=(0.0, 0.0), timeout=2
    )


@respx.mock
def test_an_unreachable_proxy_stops_run_before_any_request(down_proxy):
    route = respx.get(PYPI).mock(return_value=_pypi())
    config = youpdated.parse_config({"sources": {"pypi": ["httpx"]}})
    with youpdated.State() as state, youpdated.Client(down_proxy, state) as client:
        with pytest.raises(youpdated.ProxyUnavailable, match="cannot reach the proxy"):
            youpdated.run(config, state, client)
        # Nothing recorded, so the next run is still the baseline
        assert not state.is_baselined("pypi", "httpx")
    assert not route.called


@respx.mock
def test_an_unreachable_proxy_stops_newer(down_proxy):
    route = respx.get(PYPI).mock(return_value=_pypi())
    with youpdated.Client(down_proxy) as client:
        with pytest.raises(youpdated.ProxyUnavailable, match="cannot reach the proxy"):
            youpdated.newer("pypi", "httpx", current="0.27.0", client=client)
    assert not route.called


def test_a_test_client_needs_no_proxy(down_proxy):
    # `check --test` sends nothing, and tolerates a proxy that is down
    with youpdated.Client(down_proxy, test=True) as client:
        client.check_proxy()


def test_a_callers_own_probe_skips_only_the_next_check(down_proxy):
    with youpdated.Client(down_proxy, proxy_probed=True) as client:
        client.check_proxy()
        with pytest.raises(youpdated.ProxyUnavailable):
            client.check_proxy()


@respx.mock
def test_run_refuses_a_client_without_the_configs_proxy(down_proxy):
    route = respx.get(PYPI).mock(return_value=_pypi())
    config = youpdated.parse_config({
        "sources": {"pypi": ["httpx"]},
        "settings": {"privacy": {"proxy": down_proxy.proxy}},
    })
    with youpdated.State() as state:
        for privacy in (None, youpdated.PrivacyConfig(proxy="socks5://127.0.0.1:1")):
            with youpdated.Client(privacy, state) as client:
                with pytest.raises(ValueError, match="Client\\(config.privacy"):
                    youpdated.run(config, state, client)
    assert not route.called


# newer()


@respx.mock
def test_newer_lists_newer_releases_highest_first():
    route = respx.get(PYPI).mock(return_value=_pypi())
    found = youpdated.newer("pypi", "httpx", current="0.27.0", product="Host/2.0")
    assert _versions(found) == ["0.28.1", "0.28.0", "0.27.2", "0.27.1"]
    assert route.calls.last.request.headers["User-Agent"] == f"Host/2.0 {UA}"


@respx.mock
def test_newer_is_empty_when_up_to_date():
    respx.get(PYPI).mock(return_value=_pypi())
    assert youpdated.newer("pypi", "httpx", current="0.28.1") == []
    assert youpdated.newer("pypi", "httpx", current="v0.28.1") == []


@respx.mock
def test_newer_leaves_out_prereleases_unless_asked():
    respx.get(PYPI).mock(return_value=_pypi())
    found = youpdated.newer("pypi", "httpx", current="0.28.1", prereleases=True)
    assert _versions(found) == [f"1.0.dev{n}" for n in range(6, 0, -1)]


@respx.mock
def test_a_final_release_is_newer_than_its_prerelease():
    respx.get(PYPI).mock(return_value=_pypi(["1.2.0", "1.3.0rc1", "1.3.0"]))
    assert _versions(youpdated.newer("pypi", "httpx", current="1.3.0rc1")) == ["1.3.0"]


@pytest.mark.parametrize("version, pre", [
    ("2.0-beta.3", True), ("v2.0.0-rc.1", True), ("1.0-pre", True), ("release-2.0-preview1", True),
    ("nightly-2026-01-01", True), ("1.0.0-dev.3", True), ("2.0beta", True), ("1.0-SNAPSHOT", True),
    ("express-5.0.0", False), ("preact-10.0.0", False), ("source-1.2.3", False),
    ("1.3.0-src", False), ("redux-devtools@3.0.0", False), ("prettier@3.1.0", False),
])
def test_prerelease_words_must_stand_alone(version, pre):
    from youpdated.library import _looks_prerelease

    # As for a version PEP 440 can't read
    assert _looks_prerelease(version, None) is pre


def test_same_numbers_without_pep440_compare_by_prerelease_words():
    from youpdated.library import _is_newer

    # `2.0-beta.3` isn't PEP 440
    assert _is_newer((2,), None, False, (2,), None, True)
    assert not _is_newer((2,), None, True, (2,), None, True)
    assert not _is_newer((2,), None, False, (2,), None, False)


@respx.mock
def test_newer_honors_version_rules():
    respx.get(PYPI).mock(return_value=_pypi(["0.27.0", "0.27.2", "0.28.1", "1.0.0", "1.1.0"]))
    assert _versions(youpdated.newer("pypi", "httpx", current="0.27.0", versions={"bump": "major"})) == [
        "1.0.0"
    ]
    assert _versions(youpdated.newer("pypi", "httpx", current="0.27.0", versions={"min": "0.28"})) == [
        "1.1.0", "1.0.0", "0.28.1"
    ]
    entry = {"package": "httpx", "versions": {"match": "0.*"}}
    assert _versions(youpdated.newer("pypi", entry, current="0.27.0")) == ["0.28.1", "0.27.2"]


@respx.mock
def test_newer_honors_ignore(quick, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get(RELEASES).mock(return_value=httpx.Response(200, content=fixture("github_releases.atom")))
    respx.get(LATEST).mock(return_value=httpx.Response(
        302, headers={"Location": "https://github.com/python/cpython/releases/tag/v3.14.7"}
    ))
    found = youpdated.newer("github", "python/cpython", current="3.14.6", client=quick)
    assert _versions(found) == ["v3.14.7"]
    assert youpdated.newer(
        "github", "python/cpython", current="3.14.6", ignore=["release"], client=quick
    ) == []


@respx.mock
def test_newer_asks_github_for_the_latest_release(quick, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    respx.get(RELEASES).mock(return_value=httpx.Response(200, content=fixture("github_releases.atom")))
    respx.get(LATEST).mock(return_value=httpx.Response(
        302, headers={"Location": "https://github.com/python/cpython/releases/tag/v3.16.0"}
    ))
    found = youpdated.newer("github", "python/cpython", current="3.14.7", client=quick)
    assert _versions(found) == ["v3.16.0"]
    with_pre = youpdated.newer(
        "github", "python/cpython", current="3.14.7", prereleases=True, client=quick
    )
    assert _versions(with_pre) == ["v3.16.0", "v3.15.0rc1", "v3.15.0b4", "v3.15.0b3"]


@respx.mock
def test_newer_leaves_a_given_client_open(quick):
    respx.get(PYPI).mock(return_value=_pypi())
    youpdated.newer("pypi", "httpx", current="0.27.0", client=quick)
    youpdated.newer("pypi", "httpx", current="0.27.0", client=quick)
    assert not quick._client.is_closed


@respx.mock
def test_newer_calls_on_one_client_each_fetch_afresh(quick):
    respx.get(PYPI).mock(side_effect=[_pypi(["1.0.0", "1.1.0"]), _pypi(["1.0.0", "1.1.0", "1.2.0"])])
    assert _versions(youpdated.newer("pypi", "httpx", current="1.0.0", client=quick)) == ["1.1.0"]
    assert _versions(youpdated.newer("pypi", "httpx", current="1.0.0", client=quick)) == [
        "1.2.0", "1.1.0"
    ]


@respx.mock
def test_newer_inside_a_run_keeps_the_runs_copies(quick):
    """A source may call `newer()` with the run's client; the run still reuses what it fetched"""
    route = respx.get(PYPI).mock(return_value=_pypi(["1.0.0", "1.1.0"]))
    with quick.run_scope():
        quick.get(PYPI)
        # The pypi source sends its own Accept header, so this is a request of its own
        assert _versions(youpdated.newer("pypi", "httpx", current="1.0.0", client=quick)) == ["1.1.0"]
        assert route.call_count == 2
        quick.get(PYPI)
    assert route.call_count == 2


def test_newer_refuses_unknown_sources_and_bad_entries():
    with pytest.raises(youpdated.ConfigError, match="unknown source `nope`"):
        youpdated.newer("nope", "x", current="1.0")
    with pytest.raises(youpdated.ConfigEntryError):
        youpdated.newer("pypi", {"nmae": "httpx"}, current="1.0")
    with pytest.raises(youpdated.ConfigError, match="bump"):
        youpdated.newer("pypi", "httpx", current="1.0", versions={"bump": "huge"})
    # The same rule on a dict entry is the same error, not ConfigEntryError
    for entry in ({"package": "httpx", "versions": {"bump": "huge"}}, {"package": "httpx", "ignore": 3}):
        with pytest.raises(youpdated.ConfigError) as caught:
            youpdated.newer("pypi", entry, current="1.0")
        assert not isinstance(caught.value, youpdated.ConfigEntryError)
    with pytest.raises(ValueError, match="'latest'"):
        youpdated.newer("pypi", "httpx", current="latest")


@respx.mock
def test_newer_raises_when_the_fetch_fails(quick):
    # Not [], which would be "up to date"
    respx.get(PYPI).mock(return_value=httpx.Response(404))
    with pytest.raises(youpdated.FetchError, match="no package named `httpx`"):
        youpdated.newer("pypi", "httpx", current="0.27.0", client=quick)


@respx.mock
def test_newer_raises_fetch_error_for_an_answer_the_source_cant_read(quick):
    # A captive portal's sign-in page where PyPI's JSON should be
    respx.get(PYPI).mock(return_value=httpx.Response(200, html="<html>Sign in to Wi-Fi</html>"))
    with pytest.raises(youpdated.FetchError, match="pypi `httpx`") as caught:
        youpdated.newer("pypi", "httpx", current="0.27.0", client=quick)
    assert isinstance(caught.value.__cause__, ValueError)


@respx.mock
def test_newer_raises_when_nothing_has_a_version(quick):
    respx.get(url__startswith="https://www.youtube.com/feeds/videos.xml").mock(
        return_value=httpx.Response(200, content=fixture("youtube_feed.xml"))
    )
    with pytest.raises(youpdated.NoVersionsError, match="no item has a version"):
        youpdated.newer("youtube", "UCLA_DiR1FfKNvjuUpBHmylQ", current="1.0", client=quick)


# side effects


def _python(script: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=home, env=env, capture_output=True, text=True, timeout=60,
    )


@respx.mock
def test_newer_with_a_runs_client_doesnt_hide_a_release_from_the_next_run():
    """newer() records no items, so a validator it stored would answer the next run 304"""
    page = {"versions": ["1.0.0"], "etag": '"e1"'}

    def answer(request):
        if request.headers.get("If-None-Match") == page["etag"]:
            return httpx.Response(304)
        return httpx.Response(200, json=_pypi(page["versions"]).json(), headers={"ETag": page["etag"]})

    respx.get(PYPI).mock(side_effect=answer)
    config = youpdated.parse_config({"sources": {"pypi": ["httpx"]}, "settings": {"privacy": {"jitter": [0, 0]}}})
    with youpdated.State() as state, youpdated.Client(config.privacy, state) as client:
        assert youpdated.run(config, state, client).baseline
        page.update(versions=["1.0.0", "1.1.0"], etag='"e2"')
        assert _versions(youpdated.newer("pypi", "httpx", current="1.0.0", client=client)) == ["1.1.0"]
        assert _versions(youpdated.run(config, state, client).updates) == ["1.1.0"]


def test_version_is_defined_before_anything_else():
    """http.py imports it while the package loads"""
    import ast

    tree = ast.parse((REPO / "youpdated" / "__init__.py").read_text())
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    body = [n for n in body if not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    first = body[0]
    assert isinstance(first, ast.Assign) and [t.id for t in first.targets] == ["__version__"]


def test_import_youpdated_is_cheap(tmp_path):
    done = _python(
        """
        import sys, youpdated
        assert youpdated.__version__
        heavy = [m for m in ("httpx", "rich", "yaml", "youpdated.runner") if m in sys.modules]
        assert not heavy, heavy
        """,
        tmp_path,
    )
    assert done.returncode == 0, done.stderr


def test_library_use_reads_writes_and_prints_nothing(tmp_path):
    decoy = "sources:\n  npm: [express]\n"
    (tmp_path / "youpdated.yaml").write_text(decoy)
    done = _python(
        f"""
        import json, sys
        import httpx, respx
        import youpdated

        doc = json.loads(open({str(FIXTURES / "pypi_httpx_simple.json")!r}, "rb").read())
        with respx.mock:
            respx.get("https://pypi.org/simple/httpx/").mock(return_value=httpx.Response(200, json=doc))
            assert youpdated.newer("pypi", "httpx", current="0.28.0")
            config = youpdated.parse_config({{"sources": {{"pypi": ["httpx"]}}}})
            with youpdated.State() as state, youpdated.Client(config.privacy, state) as client:
                assert youpdated.run(config, state, client, by="library").ok
        terminal = [m for m in sys.modules if m == "youpdated.cli" or m.startswith("youpdated.render")]
        assert not terminal, terminal
        """,
        tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "" and done.stderr == ""
    # Nothing written to the home or working directory: no config, history, or lock
    assert sorted(p.name for p in tmp_path.iterdir()) == ["youpdated.yaml"]
    assert (tmp_path / "youpdated.yaml").read_text() == decoy
