"""app's source icons"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from youpdated import api, crypto, icons
from youpdated.cli import main
from youpdated.http import USER_AGENTS, Client
from youpdated.models import Target
from youpdated.state import State, stamp

from .conftest import get_source
from .fake_source import write_items
from .test_api import Engine, check_event, encryption, item, make_config


def png(size: int = 4) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\x00" + b"\x10\x80\xc0" * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


IMAGE = png()
ICON_URL = "https://img.example.com/alpha.png"


@pytest.fixture
def engine(tmp_path, fake):
    engine = Engine(make_config(tmp_path), tmp_path / "state.db")
    write_items(fake, {"alpha": [item("1.1")], "_icon": {"alpha": ICON_URL}})
    return engine


def shown(engine) -> dict | None:
    return engine("icon.get", source="fake", key="alpha")["icon"]


def icon_shown(engine) -> dict:
    icon = shown(engine)
    assert icon is not None
    return icon


def icon_url(source: str, target: Target, client: Client) -> str | list[str] | None:
    """`icon` is optional on a source, so it isn't part of `Source`"""
    return getattr(get_source(source), "icon")(target, client)


@respx.mock
def test_off_by_default_and_fetches_nothing(engine):
    assert engine("settings.get")["settings"]["show_icons"] is False
    route = respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))

    engine("check")

    assert not route.called
    assert shown(engine) is None


@respx.mock
def test_a_check_fetches_it_through_the_client(engine):
    route = respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})
    assert shown(engine) is None  # onyl when a check fetches

    engine("check")

    assert route.call_count == 1
    assert route.calls.last.request.headers["User-Agent"] in USER_AGENTS
    assert shown(engine) == {"mime": "image/png", "data": base64.b64encode(IMAGE).decode()}
    engine("check")
    assert route.call_count == 1


@respx.mock
def test_turning_it_off_deletes_what_was_fetched(engine, tmp_path):
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})
    engine("check")

    engine("settings.set", settings={"show_icons": False})

    assert shown(engine) is None
    with State(tmp_path / "state.db") as state:
        assert state.icon("fake", "alpha") is None
    db = sqlite3.connect(tmp_path / "state.db")
    assert db.execute("SELECT COUNT(*) FROM icons").fetchone() == (0,)
    db.close()
    # on again, wait for the next check
    engine("settings.set", settings={"show_icons": True})
    assert shown(engine) is None


@respx.mock
def test_icon_waits_for_the_reply_then_says_it_arrived(engine):
    route = respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})

    reply = engine.call({"op": "check"})

    assert reply["ok"] and not route.called and engine.fetching_icons
    engine.follow_up()
    assert route.call_count == 1 and not engine.fetching_icons
    assert engine.events[-1] == {"event": "icon", "target": {"source": "fake", "key": "alpha"}}
    assert icon_shown(engine)["mime"] == "image/png"


@respx.mock
def test_no_event_when_there_is_no_icon(engine):
    respx.get(ICON_URL).mock(return_value=httpx.Response(404))
    engine("settings.set", settings={"show_icons": True})
    engine("check")
    assert not [e for e in engine.events if e["event"] == "icon"]


@respx.mock
def test_a_new_check_stops_the_last_ones_icons(engine, fake, tmp_path):
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta", "gamma"))
    write_items(fake, {"_icon": {k: f"https://img.example.com/{k}.png" for k in ("alpha", "beta", "gamma")}})
    started = threading.Event()

    def slow(request):
        started.set()
        time.sleep(0.3)
        return httpx.Response(200, content=IMAGE)

    route = respx.get(url__startswith="https://img.example.com/").mock(side_effect=slow)
    engine("settings.set", settings={"show_icons": True})
    first = threading.Thread(target=engine, args=("check",))
    first.start()
    assert started.wait(5)

    second = engine.call({"op": "check"})

    first.join(5)
    assert second["ok"]
    # the icon being fetched was kept, and the rest left to the new check
    kept = [e["target"]["key"] for e in engine.events if e["event"] == "icon"]
    assert len(kept) == route.call_count == 1
    engine.follow_up()
    assert route.call_count == 3
    assert sorted(e["target"]["key"] for e in engine.events if e["event"] == "icon") == [
        "alpha", "beta", "gamma",
    ]


@respx.mock
def test_serve_sends_icon_events_after_the_reply(tmp_path, fake):
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    write_items(fake, {"alpha": [item("1.1")], "_icon": {"alpha": ICON_URL}})
    engine = api.Engine(make_config(tmp_path), tmp_path / "state.db")
    engine.call({"op": "settings.set", "settings": {"show_icons": True}})
    read_in, write_in = os.pipe()
    read_out, write_out = os.pipe()
    stdin, stdout = os.fdopen(read_in, "rb"), os.fdopen(write_out, "wb")
    serving = threading.Thread(target=api.serve, args=(engine, stdin, stdout), daemon=True)
    serving.start()
    requests, replies = os.fdopen(write_in, "wb"), os.fdopen(read_out, "rb")

    def ask(rid: int, op: str) -> list[dict]:
        requests.write(json.dumps({"id": rid, "op": op}).encode() + b"\n")
        requests.flush()
        lines = []
        while not lines or lines[-1].get("id") != rid:
            lines.append(json.loads(replies.readline()))
        return lines

    ask(1, "check")
    event = json.loads(replies.readline())
    check_event(event)
    assert event == {"event": "icon", "target": {"source": "fake", "key": "alpha"}}
    assert ask(2, "shutdown")[-1] == {"id": 2, "ok": True}
    serving.join(5)
    for stream in (requests, replies, stdin, stdout):
        stream.close()


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        b"<!doctype html><title>not an image</title>",
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        IMAGE + b"\0" * icons.MAX_BYTES,
    ],
    ids=["html", "svg", "too-big"],
)
def test_only_small_images_are_kept(engine, body):
    route = respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=body, headers={"Content-Type": "image/png"}))
    engine("settings.set", settings={"show_icons": True})

    engine("check")
    engine("check")

    assert shown(engine) is None
    assert route.call_count == 1  # miss waits for icons.RETRY


@respx.mock
def test_a_failed_fetch_is_tried_again_later(engine, tmp_path):
    route = respx.get(ICON_URL).mock(return_value=httpx.Response(404))
    engine("settings.set", settings={"show_icons": True})
    engine("check")
    assert shown(engine) is None
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("UPDATE icons SET fetched_at = '2020-01-01T00:00:00Z'")
    db.commit()
    db.close()
    route.mock(return_value=httpx.Response(200, content=IMAGE))

    engine("check")

    assert icon_shown(engine)["mime"] == "image/png"


@respx.mock
def test_the_next_url_is_tried_when_one_isnt_an_image(engine, fake):
    write_items(fake, {"alpha": [], "_icon": {"alpha": [ICON_URL, "https://img.example.com/icon.ico"]}})
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=b"\0" * (icons.MAX_BYTES + 1)))
    ico = b"\x00\x00\x01\x00" + b"\x01" * 60
    respx.get("https://img.example.com/icon.ico").mock(return_value=httpx.Response(200, content=ico))
    engine("settings.set", settings={"show_icons": True})

    engine("check")

    assert shown(engine) == {"mime": "image/x-icon", "data": base64.b64encode(ico).decode()}


@respx.mock
def test_a_check_fetches_only_a_few(engine, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "PER_CHECK", 2)
    engine.config_override = make_config(tmp_path, entries=("alpha", "beta", "gamma"))
    write_items(fake, {"_icon": {k: f"https://img.example.com/{k}.png" for k in ("alpha", "beta", "gamma")}})
    route = respx.get(url__startswith="https://img.example.com/").mock(
        return_value=httpx.Response(200, content=IMAGE)
    )
    engine("settings.set", settings={"show_icons": True})

    engine("check")
    assert route.call_count == 2
    engine("check")
    assert route.call_count == 3


def test_icons_are_refreshed_rarely():
    with State(None) as state:
        state.set_icon("fake", "kept", ("image/png", IMAGE))
        state.set_icon("fake", "missed", None)
        keys = [("fake", "kept"), ("fake", "missed"), ("fake", "new")]
        assert state.icons_due(keys, icons.REFRESH, icons.RETRY) == [("fake", "new")]
        old = stamp(datetime.now(timezone.utc) - timedelta(days=10))
        state._conn.execute("UPDATE icons SET fetched_at = ?", (old,))
        assert state.icons_due(keys, icons.REFRESH, icons.RETRY) == [("fake", "missed"), ("fake", "new")]


# encryption and wiping


@encryption
@respx.mock
def test_icons_are_encrypted_with_the_history(tmp_path, fake):
    config = make_config(tmp_path)
    crypto.write_private(config, crypto.encrypt(config.read_bytes(), "pw"))
    engine = Engine(config, tmp_path / "state.db")
    engine("unlock", passphrase="pw")
    write_items(fake, {"alpha": [item("1.1")], "_icon": {"alpha": ICON_URL}})
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})

    engine("check")

    assert icon_shown(engine)["data"] == base64.b64encode(IMAGE).decode()
    blob = (tmp_path / "state.db").read_bytes()
    assert crypto.is_encrypted(blob) and IMAGE not in blob


@respx.mock
def test_a_wipe_removes_a_targets_icon(engine, tmp_path):
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})
    engine("check")

    assert engine("targets.remove", source="fake", key="alpha", wipe=True)["ok"]

    with State(tmp_path / "state.db") as state:
        assert state.icon("fake", "alpha") is None


@respx.mock
def test_rm_wipe_from_the_cli_removes_it_too(engine, tmp_path):
    respx.get(ICON_URL).mock(return_value=httpx.Response(200, content=IMAGE))
    engine("settings.set", settings={"show_icons": True})
    engine("check")
    state_path = str(tmp_path / "state.db")

    assert main(["rm", "-c", str(engine.config_override), "--wipe", "--state", state_path, "fake", "alpha"]) == 0

    with State(tmp_path / "state.db") as state:
        assert state.icon("fake", "alpha") is None


# where each source finds it


def _target(source: str, entry):
    (target,) = get_source(source).targets([entry])
    return target


@respx.mock
def test_steam_uses_the_store_header(client):
    header = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/440/abc/header.jpg"
    respx.get(url__startswith="https://store.steampowered.com/api/appdetails").mock(
        return_value=httpx.Response(200, json={"440": {"success": True, "data": {"header_image": header}}})
    )
    assert icon_url("steam", _target("steam", "440"), client) == header


def test_github_names_the_owners_avatar_without_a_request(client):
    url = icon_url("git", _target("git", "python/cpython"), client)
    assert url == "https://github.com/python.png?size=128"


@respx.mock
def test_gitlab_and_forgejo_ask_the_repo_api(client):
    respx.get("https://gitlab.com/api/v4/projects/gnome%2Fgtk").mock(
        return_value=httpx.Response(200, json={"namespace": {"avatar_url": "/uploads/-/system/group/avatar/8/gnome.png"}})
    )
    respx.get("https://codeberg.org/api/v1/repos/forgejo/forgejo").mock(
        return_value=httpx.Response(200, json={"owner": {"avatar_url": "https://codeberg.org/avatars/abc"}})
    )
    assert icon_url("git", _target("git", "gitlab.com/gnome/gtk"), client) == (
        "https://gitlab.com/uploads/-/system/group/avatar/8/gnome.png"
    )
    assert icon_url("git", _target("git", "codeberg.org/forgejo/forgejo"), client) == (
        "https://codeberg.org/avatars/abc"
    )


@respx.mock
def test_youtube_uses_the_channel_picture_at_a_small_size(client):
    page = '<meta content="https://yt3.googleusercontent.com/abc=s900-c-k-c0x00ffffff-no-rj" property="og:image">'
    respx.get("https://www.youtube.com/@NASA").mock(return_value=httpx.Response(200, text=page))
    assert icon_url("youtube", _target("youtube", "@NASA"), client) == (
        "https://yt3.googleusercontent.com/abc=s176-c-k-c0x00ffffff-no-rj"
    )
    assert icon_url("youtube", _target("youtube", {"playlist": "PLabcdefghijkl"}), client) is None


@respx.mock
def test_itch_uses_the_cover(client):
    page = "<meta property='og:image' content='https://img.itch.zone/aW1n/315x250%23c/cover.png'/>"
    respx.get("https://foo.itch.io/bar").mock(return_value=httpx.Response(200, text=page))
    assert icon_url("itch", _target("itch", "https://foo.itch.io/bar"), client) == (
        "https://img.itch.zone/aW1n/315x250%23c/cover.png"
    )


@respx.mock
def test_a_feed_uses_its_image_else_its_sites_icon(client):
    with_image = (
        "<rss><channel><title>Blog</title><link>https://blog.example.com/</link>"
        "<image><url>https://blog.example.com/logo.png</url></image></channel></rss>"
    )
    respx.get("https://blog.example.com/feed.xml").mock(return_value=httpx.Response(200, text=with_image))
    assert icon_url("feed", _target("feed", "https://blog.example.com/feed.xml"), client) == [
        "https://blog.example.com/logo.png",
        "https://blog.example.com/favicon.ico",
    ]

    plain = "<rss><channel><title>News</title><link>https://news.example.org/</link></channel></rss>"
    home = (
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
    )
    respx.get("https://news.example.org/rss.xml").mock(return_value=httpx.Response(200, text=plain))
    respx.get("https://news.example.org/").mock(return_value=httpx.Response(200, text=home))
    assert icon_url("feed", _target("feed", "https://news.example.org/rss.xml"), client) == [
        "https://news.example.org/apple-touch-icon.png"
    ]


def test_sources_without_icons_have_no_hook():
    assert not any(hasattr(get_source(name), "icon") for name in ("npm", "pypi", "homebrew", "browser"))


def test_images_are_known_by_their_bytes():
    assert icons.sniff(IMAGE) == "image/png"
    assert icons.sniff(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert icons.sniff(b"GIF89a...") == "image/gif"
    assert icons.sniff(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert icons.sniff(b"<svg") is None
