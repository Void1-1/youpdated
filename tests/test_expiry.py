"""`expiry:` pruning of the seen history, without re-reporting anything still live."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from youpdated.config import DEFAULT_EXPIRY, ConfigError, parse_config
from youpdated.models import Update
from youpdated.runner import parse_since, run
from youpdated.state import State

YEAR = timedelta(days=365)


def make_update(uid: str = "1", target: str = "a/b", source: str = "github") -> Update:
    return Update(source=source, target=target, uid=uid, title=uid, url="https://e.com")


def backdate(state: State, age: timedelta, *, table: str = "seen") -> None:
    """Pretend every row in ``table`` was last touched ``age`` ago."""
    stamp = (datetime.now(timezone.utc) - age).isoformat()
    with state._lock:
        if table == "seen":
            state._conn.execute("UPDATE seen SET first_seen=?, last_seen=?", (stamp, stamp))
        elif table == "baseline":
            state._conn.execute("UPDATE kv SET value=? WHERE namespace='baseline'", (stamp,))
        else:
            state._conn.execute("UPDATE http_cache SET fetched_at=?", (stamp,))
        state._conn.commit()


def npm_doc(*versions: str) -> dict:
    return {
        "name": "express",
        "dist-tags": {"latest": versions[-1]},
        "time": {v: f"2024-01-0{i + 1}T00:00:00.000Z" for i, v in enumerate(versions)},
        "versions": {},
    }


# config


def test_expiry_defaults_to_a_year():
    assert parse_config({"sources": {"npm": ["e"]}}).expiry == DEFAULT_EXPIRY == YEAR


@pytest.mark.parametrize(
    "raw, expected",
    [("180d", timedelta(days=180)), ("2y", 2 * YEAR), ("52w", timedelta(weeks=52)),
     ("never", None), (False, None)],
)
def test_expiry_parses(raw, expected):
    assert parse_config({"sources": {"npm": ["e"]}, "expiry": raw}).expiry == expected


@pytest.mark.parametrize("raw", ["soon", 365, "12h", None, ""])
def test_bad_expiry_is_rejected(raw):
    with pytest.raises(ConfigError, match="expiry"):
        parse_config({"sources": {"npm": ["e"]}, "expiry": raw})


def test_since_accepts_years_too():
    assert parse_since("1y") == YEAR


# state


def test_prune_forgets_only_items_not_seen_within_the_expiry():
    with State(None, expiry=YEAR) as state:
        state.mark_seen([make_update("old")])
        backdate(state, YEAR + timedelta(days=1))
        state.mark_seen([make_update("fresh")])

        assert state.prune() == 1
        assert state.is_new(make_update("old"))
        assert not state.is_new(make_update("fresh"))


def test_seeing_an_item_again_keeps_it_alive():
    with State(None, expiry=YEAR) as state:
        state.mark_seen([make_update("still-listed")])
        backdate(state, 2 * YEAR)
        state.mark_seen([make_update("still-listed")])

        assert state.prune() == 0
        assert not state.is_new(make_update("still-listed"))


def test_prune_leaves_kept_targets_and_sources_alone():
    with State(None, expiry=YEAR) as state:
        state.mark_seen([
            make_update(target="failing"),
            make_update(target="dropped"),
            make_update(source="npm", target="express"),
        ])
        backdate(state, 2 * YEAR)

        removed = state.prune(keep_targets={("github", "failing")}, keep_sources={"npm"})
        assert removed == 1
        assert state.is_new(make_update(target="dropped"))
        assert not state.is_new(make_update(target="failing"))
        assert not state.is_new(make_update(source="npm", target="express"))


def test_no_expiry_never_prunes():
    with State(None) as state:
        state.mark_seen([make_update()])
        backdate(state, 10 * YEAR)
        assert state.prune() == 0
        assert state.seen_count() == 1


def test_validators_half_way_to_expiry_are_not_sent():
    """A 304 refreshes nothing, so an old validator must give way to a full fetch."""
    with State(None, expiry=YEAR) as state:
        state.remember_validators("https://e.com/f", '"abc"', None)
        assert state.conditional_headers("https://e.com/f")

        backdate(state, YEAR / 2 + timedelta(days=1), table="http_cache")
        assert state.conditional_headers("https://e.com/f") == {}


def test_pruning_shrinks_the_encrypted_file(tmp_path):
    path = tmp_path / "state.enc"
    with State(path, passphrase="pw", expiry=YEAR) as state:
        state.mark_seen([make_update(str(i)) for i in range(5000)])
    full = path.stat().st_size

    with State(path, passphrase="pw", expiry=YEAR) as state:
        backdate(state, 2 * YEAR)
        assert state.prune() == 5000
    assert path.stat().st_size < full / 4


def test_an_old_database_gains_last_seen(tmp_path):
    path = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE seen (source TEXT NOT NULL, target TEXT NOT NULL, uid TEXT NOT NULL, "
        "first_seen TEXT NOT NULL, PRIMARY KEY (source, target, uid))"
    )
    conn.execute("INSERT INTO seen VALUES ('github', 'a/b', '1', '2020-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    with State(path, expiry=YEAR) as state:
        row = state._conn.execute("SELECT last_seen FROM seen").fetchone()
        assert row["last_seen"] == "2020-01-01T00:00:00+00:00"
        assert not state.is_new(make_update())
        assert state.prune() == 1


def test_double_prefixed_youtube_shorts_are_migrated(tmp_path):
    path = tmp_path / "state.sqlite3"
    State(path).close()
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO seen VALUES ('youtube', ?, ?, '2026-01-01T00:00:00+00:00', NULL)",
        [
            ("@a", "yt:video:yt:video:AAAAAAAAAAA"),
            ("@a", "yt:video:yt:video:BBBBBBBBBBB"),
            ("@a", "yt:video:BBBBBBBBBBB"),
        ],
    )
    conn.commit()
    conn.close()

    with State(path) as state:
        uids = {row["uid"] for row in state._conn.execute("SELECT uid FROM seen")}
    assert uids == {"yt:video:AAAAAAAAAAA", "yt:video:BBBBBBBBBBB"}


# runner


@pytest.fixture
def expiring_state(state):
    state.expiry = YEAR
    return state


@respx.mock
def test_an_item_still_in_its_feed_is_never_re_reported(expiring_state, client):
    state = expiring_state
    config = parse_config({"sources": {"npm": ["express"]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0"))
    )
    run(config, state, client)  # baseline
    backdate(state, 2 * YEAR)

    later = run(config, state, client)
    assert later.updates == [] and later.pruned == 0


@respx.mock
def test_a_long_304_streak_forces_a_refresh_before_anything_expires(expiring_state, client):
    state = expiring_state
    config = parse_config({"sources": {"npm": ["express"]}})
    route = respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0"), headers={"ETag": '"v1"'})
    )
    run(config, state, client)  # baseline, stores the validator
    backdate(state, 2 * YEAR)
    backdate(state, 2 * YEAR, table="http_cache")

    # The server would 304 a conditional request, refreshing nothing
    route.side_effect = lambda request: (
        httpx.Response(304) if "If-None-Match" in request.headers
        else httpx.Response(200, json=npm_doc("1.0.0"), headers={"ETag": '"v1"'})
    )
    later = run(config, state, client)

    assert "If-None-Match" not in route.calls[-1].request.headers
    assert later.updates == [] and later.pruned == 0


@respx.mock
def test_removed_targets_are_pruned_but_failing_ones_kept(expiring_state, client):
    state = expiring_state
    both = parse_config({"sources": {"npm": ["express", "left-pad", "broken"]}})
    for name in ("express", "left-pad", "broken"):
        respx.get(f"https://registry.npmjs.org/{name}").mock(
            return_value=httpx.Response(200, json=npm_doc("1.0.0"))
        )
    run(both, state, client)
    backdate(state, 2 * YEAR)

    respx.get("https://registry.npmjs.org/broken").mock(return_value=httpx.Response(500))
    without_left_pad = parse_config({"sources": {"npm": ["express", "broken"]}})
    result = run(without_left_pad, state, client)

    assert result.pruned == 1
    targets = {row["target"] for row in state._conn.execute("SELECT target FROM seen")}
    assert targets == {"express", "broken"}


@respx.mock
def test_a_target_re_added_after_its_history_expired_is_baselined_again(
    expiring_state, client
):
    """Its seen items are gone, so a surviving baseline mark would report them all."""
    state = expiring_state
    for name in ("express", "left-pad"):
        respx.get(f"https://registry.npmjs.org/{name}").mock(
            return_value=httpx.Response(200, json=npm_doc("1.0.0"))
        )
    run(parse_config({"sources": {"npm": ["express", "left-pad"]}}), state, client)
    for table in ("seen", "http_cache", "baseline"):
        backdate(state, 2 * YEAR, table=table)

    run(parse_config({"sources": {"npm": ["express"]}}), state, client)
    assert state.is_baselined("npm", "express")
    assert not state.is_baselined("npm", "left-pad")

    respx.get("https://registry.npmjs.org/left-pad").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0", "1.1.0"))
    )
    back = run(parse_config({"sources": {"npm": ["express", "left-pad"]}}), state, client)
    assert [t.key for t in back.baselined] == ["left-pad"]
    assert back.updates == []


@respx.mock
def test_baseline_marks_of_kept_targets_survive_pruning(expiring_state, client):
    state = expiring_state
    config = parse_config({"sources": {"npm": ["express"], "steam": [440]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0"))
    )
    state.mark_baselined([("steam", "440")])
    backdate(state, 2 * YEAR, table="baseline")

    run(config, state, client, only_sources=["npm"])
    assert state.cache_get("baseline", "steam:440") is not None


def test_recorded_ignore_rules_go_with_their_expired_baseline_mark(expiring_state):
    state = expiring_state
    state.mark_baselined([("npm", "gone"), ("npm", "kept")])
    state.mark_ignore_rules(
        [("npm", "gone", frozenset({"prerelease"})), ("npm", "kept", frozenset())]
    )
    backdate(state, 2 * YEAR, table="baseline")
    state.mark_baselined([("npm", "kept")])

    state.prune()

    assert state.cache_get("ignore", "npm:gone") is None
    assert state.cache_get("ignore", "npm:kept") == ""

@respx.mock
def test_targets_skipped_by_source_filter_are_kept(expiring_state, client):
    state = expiring_state
    config = parse_config({"sources": {"npm": ["express"], "steam": [440]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0"))
    )
    state.mark_seen([make_update(source="steam", target="440")])
    state.set_last_run()
    backdate(state, 2 * YEAR)

    result = run(config, state, client, only_sources=["npm"])
    assert result.pruned == 0
    assert not state.is_new(make_update(source="steam", target="440"))


@respx.mock
def test_a_dry_run_prunes_nothing(expiring_state, client):
    state = expiring_state
    config = parse_config({"sources": {"npm": ["express"]}})
    respx.get("https://registry.npmjs.org/express").mock(
        return_value=httpx.Response(200, json=npm_doc("1.0.0"))
    )
    state.mark_seen([make_update(source="npm", target="gone")])
    state.set_last_run()
    backdate(state, 2 * YEAR)

    assert run(config, state, client, save=False).pruned == 0
    assert state.seen_count() == 1
