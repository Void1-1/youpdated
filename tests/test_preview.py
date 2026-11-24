"""Looking a value up before it's added: a page's feed, a video's channel, addresses refused"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

from youpdated import preview
from youpdated.cli import main
from youpdated.config import ConfigError, load_config
from youpdated.edit import claims, guess_source, is_bare, readers
from youpdated.http import FetchError
from youpdated.models import Target
from youpdated.registry import all_sources
from youpdated.sources.base import ConfigEntryError
from youpdated.sources.feed import parse_feed

from .conftest import fixture, get_source
from .test_api import Engine

MASTODON = "https://mastodon.social/@Gargron"
MASTODON_FEED = "https://mastodon.social/@Gargron.rss"
BLUESKY = "https://bsky.app/profile/bsky.app"
BLUESKY_FEED = "https://bsky.app/profile/did:plc:z72i7hdynmk6r22z27h6tvur/rss"
VIDEO_PAGE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
CHANNEL_PAGE = "https://www.youtube.com/@RickAstleyYT"
SITE = "https://lumen.example/"
SITE_FEED = "https://lumen.example/news.xml"
HTML = {"Content-Type": "text/html; charset=utf-8"}
RSS = {"Content-Type": "application/rss+xml; charset=utf-8"}
CONFIG = "settings:\n  privacy:\n    jitter: [0, 0]\nsources:\n  npm:\n    - express\n"


def _page(body: str, head: str = "") -> bytes:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>".encode()


LINKS_FEED = '<link rel="alternate" type="application/rss+xml" href="/news.xml">'
NAMES_VERSION = '<a href="/dl/Lumen-2.4.1.dmg">Download Lumen</a>'


def _serve_mastodon() -> respx.Route:
    page = respx.get(MASTODON).mock(
        return_value=httpx.Response(200, content=fixture("mastodon_profile.html"), headers=HTML)
    )
    respx.get(MASTODON_FEED).mock(
        return_value=httpx.Response(200, content=fixture("mastodon_feed.rss"), headers=RSS)
    )
    return page


def _serve_site(head: str, body: str) -> respx.Route:
    page = respx.get(SITE).mock(
        return_value=httpx.Response(200, content=_page(body, head), headers=HTML)
    )
    respx.get(SITE_FEED).mock(
        return_value=httpx.Response(200, content=fixture("generic_feed.xml"), headers=RSS)
    )
    return page


def _serve_video(page: bytes | None = None) -> None:
    respx.get(VIDEO_PAGE).mock(
        return_value=httpx.Response(200, content=page or fixture("youtube_watch.html"), headers=HTML)
    )
    respx.get(CHANNEL_PAGE).mock(return_value=httpx.Response(404))


@pytest.mark.parametrize(
    "value, source, entry",
    [
        (
            "https://raw.githubusercontent.com/lumen/app/main/latest.json",
            "json",
            "https://raw.githubusercontent.com/lumen/app/main/latest.json",
        ),
        ("https://gist.github.com/someone/0123abcd", "webpage", "https://gist.github.com/someone/0123abcd"),
        ("https://lumen.github.io/docs/app", "webpage", "https://lumen.github.io/docs/app"),
        ("https://lumen.gitlab.io/site/page", "webpage", "https://lumen.gitlab.io/site/page"),
        # Only the app id is kept
        ("https://steamdb.info/app/413150/", "steam", "413150"),
        ("https://steamdb.info/app/413150/charts/", "steam", "413150"),
        # Looked up when added
        (MASTODON, "webpage", MASTODON),
        (BLUESKY, "webpage", BLUESKY),
    ],
)
def test_a_source_is_guessed_without_a_fetch(value, source, entry):
    assert guess_source(value) == (source, entry)


@pytest.mark.parametrize(
    "value, message",
    [
        ("https://github.com/neovim", "is the page of neovim on github.com, not a repo"),
        ("https://github.com/neovim/", "youpdated add github.com/neovim/REPO"),
        ("github.com/neovim", "is the page of neovim on github.com"),
        ("https://github.com/orgs/neovim/repositories", "youpdated add github.com/neovim/REPO"),
        ("https://github.com/neovim?tab=repositories", "the page of neovim"),
        ("https://gitlab.com/gnome", "youpdated add gitlab.com/gnome/REPO"),
        ("https://www.reddit.com/r/linux/", "feed https://www.reddit.com/r/linux/.rss"),
        ("https://old.reddit.com/r/linux", "Reddit refuses automated readers"),
        (VIDEO_PAGE, "is a video, not a channel"),
        ("https://youtu.be/dQw4w9WgXcQ", "is a video, not a channel"),
    ],
)
def test_an_address_that_names_nothing_to_watch_says_why(value, message):
    with pytest.raises(ConfigError, match=message.replace("?", r"\?")):
        guess_source(value)


def test_another_sources_claim_wins_over_a_refusal(monkeypatch):
    class Mirror:
        name = "mirror"

        def claim(self, value):
            return value

    git = get_source("git")
    monkeypatch.setattr("youpdated.edit.all_sources", lambda: {"git": git, "mirror": Mirror()})
    assert claims("https://github.com/neovim") == {"mirror": "https://github.com/neovim"}


def test_a_known_hosts_account_is_not_a_repo_owned_by_the_host():
    # It was `github.com/github.com/neovim`
    with pytest.raises(ConfigEntryError, match="does not name an owner/repo"):
        get_source("git").targets(["github.com/neovim"])
    (target,) = get_source("git").targets(["github.com/neovim/neovim"])
    assert target.key == "github.com/neovim/neovim"


def test_a_reddit_feed_named_as_one_is_still_read():
    assert get_source("feed").targets(["https://www.reddit.com/r/linux/.rss"])


@respx.mock
@pytest.mark.parametrize(
    "value",
    [
        VIDEO_PAGE,
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
    ],
)
def test_a_videos_channel_is_read_from_its_page(client, value):
    _serve_video()
    assert preview.resolver(value) == "youtube"
    assert preview.resolve("youtube", value, client) == ("@RickAstleyYT", "the video's channel")


@respx.mock
def test_a_video_page_without_a_handle_gives_the_channels_id(client):
    page = fixture("youtube_watch.html").replace(b'href="http://www.youtube.com/@RickAstleyYT"', b"")
    _serve_video(page)
    entry, _ = preview.resolve("youtube", VIDEO_PAGE, client)
    assert entry == "UCuAXFkgsw1L7xaCfnd5JJOw"


@respx.mock
def test_a_video_page_naming_no_channel_is_an_error(client):
    _serve_video(_page("Something went wrong"))
    with pytest.raises(FetchError, match="doesn't name its channel"):
        preview.resolve("youtube", VIDEO_PAGE, client)


@pytest.mark.parametrize(
    "value",
    ["https://www.youtube.com/@NASA", "https://www.youtube.com/playlist?list=PLabcdefghijk", MASTODON],
)
def test_only_a_video_is_resolved(value):
    assert preview.resolver(value) is None


def test_a_picked_source_is_the_only_one_asked():
    assert preview.resolver(VIDEO_PAGE, "webpage") is None
    assert preview.resolver(VIDEO_PAGE, "youtube") == "youtube"


@respx.mock
@pytest.mark.parametrize(
    "page, page_fixture, feed",
    [
        (MASTODON, "mastodon_profile.html", MASTODON_FEED),
        # The page links its feed by the account's DID
        (BLUESKY, "bluesky_profile.html", BLUESKY_FEED),
    ],
)
def test_a_profile_page_gives_way_to_its_feed(client, page, page_fixture, feed):
    served = respx.get(page).mock(
        return_value=httpx.Response(200, content=fixture(page_fixture), headers=HTML)
    )
    respx.get(feed).mock(
        return_value=httpx.Response(200, content=fixture("mastodon_feed.rss"), headers=RSS)
    )
    (option,) = preview.options("webpage", page, client)
    assert (option.source, option.entry, option.why) == ("feed", feed, "the feed the page links")
    assert option.error is None and option.newest is not None
    # The page is fetched once
    assert served.call_count == 1


@respx.mock
def test_a_page_naming_a_version_and_linking_a_feed_offers_both(client):
    _serve_site(LINKS_FEED, NAMES_VERSION)
    found = preview.options("webpage", SITE, client)
    assert [(o.source, o.entry) for o in found] == [("webpage", SITE), ("feed", SITE_FEED)]
    assert found[0].newest is not None and found[0].newest.version == "2.4.1"


@respx.mock
def test_an_address_that_is_a_feed_is_watched_as_one(client):
    respx.get(SITE).mock(
        return_value=httpx.Response(200, content=fixture("generic_feed.xml"), headers=RSS)
    )
    (option,) = preview.options("webpage", SITE, client)
    assert (option.source, option.entry, option.why) == ("feed", SITE, "the address is a feed")


@respx.mock
def test_a_page_with_nothing_to_offer_keeps_its_error(client):
    _serve_site("", "<p>Hello</p>")
    (option,) = preview.options("webpage", SITE, client)
    assert option.source == "webpage" and "no version found" in option.error


@respx.mock
def test_a_failing_feed_doesnt_replace_a_failing_page(client):
    respx.get(SITE).mock(
        return_value=httpx.Response(200, content=_page("<p>Hello</p>", LINKS_FEED), headers=HTML)
    )
    respx.get(SITE_FEED).mock(return_value=httpx.Response(200, content=_page("not a feed")))
    found = preview.options("webpage", SITE, client)
    assert [o.source for o in found] == ["webpage", "feed"]
    assert all(o.error for o in found)


def test_an_untitled_post_is_titled_by_its_text():
    first, second = parse_feed(fixture("mastodon_feed.rss"), source="feed", target=MASTODON_FEED)
    assert first.title.startswith("A beautiful woman") and first.title.endswith("…")
    assert len(first.title) <= 80
    assert first.uid == "https://mastodon.social/@Gargron/117655862440888668"


# targets.preview


@pytest.fixture
def engine(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    return Engine(config, tmp_path / "state.db")


@respx.mock
def test_preview_offers_a_profiles_feed(engine):
    _serve_mastodon()
    reply = engine("targets.preview", value=MASTODON)
    assert (reply["source"], reply["entry"]) == ("feed", MASTODON_FEED)
    assert reply["display"] == "Eugen Rochko" and reply["fetch_error"] is None
    assert reply["newest"]["url"].startswith("https://mastodon.social/@Gargron/")


@respx.mock
def test_preview_finds_a_videos_channel(engine):
    _serve_video()
    reply = engine("targets.preview", value="https://youtu.be/dQw4w9WgXcQ")
    assert (reply["source"], reply["entry"]) == ("youtube", "@RickAstleyYT")


@respx.mock
def test_preview_says_when_a_video_cant_be_looked_up(engine):
    respx.get(VIDEO_PAGE).mock(return_value=httpx.Response(404))
    error = engine("targets.preview", value=VIDEO_PAGE)["error"]
    assert error["code"] == "bad_entry" and "couldn't look up" in error["message"]


@pytest.mark.parametrize(
    "value, message",
    [("https://github.com/neovim", "not a repo"), ("https://reddit.com/r/linux", "Reddit")],
)
def test_preview_refuses_what_names_nothing_to_watch(engine, value, message):
    error = engine("targets.preview", value=value)["error"]
    assert error["code"] == "bad_entry" and message in error["message"]


# youpdated add


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def tty(monkeypatch, interactive: bool = True) -> None:
    class Stdin:
        def isatty(self) -> bool:
            return interactive

    monkeypatch.setattr(sys, "stdin", Stdin())


def answers(monkeypatch, *replies: str) -> list[str]:
    """Answer prompts with ``replies`` in turn"""
    asked: list[str] = []
    left = list(replies)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        if not left:
            raise EOFError
        return left.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def _sources(config: Path) -> dict:
    return load_config(config).sources


FETCH_IT = "Fetch it? [Y/n, n adds as given] "


@respx.mock
def test_add_asks_before_fetching_a_page(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch, "")
    _serve_mastodon()
    assert main(["add", "-c", str(config), MASTODON]) == 0
    # One result: nothing more to ask once it's fetched
    assert asked == [FETCH_IT]
    assert _sources(config)["feed"] == [MASTODON_FEED]
    out = capsys.readouterr().out
    assert "would be fetched to look for its feed" in out
    assert "the feed the page links" in out and "Eugen Rochko, newest:" in out


@respx.mock
def test_add_declining_the_fetch_adds_the_page_as_given(config, monkeypatch):
    tty(monkeypatch)
    answers(monkeypatch, "n")
    page = _serve_mastodon()
    assert main(["add", "-c", str(config), MASTODON]) == 0
    assert _sources(config)["webpage"] == [MASTODON]
    assert page.call_count == 0


@respx.mock
def test_add_asks_once_for_several_values(config, monkeypatch):
    tty(monkeypatch)
    asked = answers(monkeypatch, "y")
    _serve_mastodon()
    _serve_video()
    assert main(["add", "-c", str(config), MASTODON, "https://youtu.be/dQw4w9WgXcQ"]) == 0
    assert asked == ["Fetch them? [Y/n, n adds as given] "]
    assert _sources(config)["youtube"] == ["@RickAstleyYT"]


@respx.mock
def test_add_asks_which_when_a_page_offers_several(config, monkeypatch):
    tty(monkeypatch)
    asked = answers(monkeypatch, "", "3", "2")
    _serve_site(LINKS_FEED, NAMES_VERSION)
    assert main(["add", "-c", str(config), SITE]) == 0
    assert asked == [FETCH_IT] + ["Add which? [1/2/n] "] * 2
    assert _sources(config)["feed"] == [SITE_FEED]
    assert "webpage" not in _sources(config)


@respx.mock
def test_add_picking_none_writes_nothing(config, monkeypatch, capsys):
    tty(monkeypatch)
    answers(monkeypatch, "", "n")
    _serve_site(LINKS_FEED, NAMES_VERSION)
    assert main(["add", "-c", str(config), SITE]) == 0
    assert f"Left out {SITE}" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG


@respx.mock
def test_add_without_a_terminal_still_adds_the_rest_beside_a_video(config, monkeypatch):
    tty(monkeypatch, interactive=False)
    assert main(["add", "-c", str(config), "https://youtu.be/dQw4w9WgXcQ", MASTODON]) == 0
    assert _sources(config)["webpage"] == [MASTODON] and "youtube" not in _sources(config)


@respx.mock
def test_add_shows_what_couldnt_be_checked_and_adds_it(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch, "")
    _serve_site("", "<p>Hello</p>")
    assert main(["add", "-c", str(config), SITE]) == 0
    assert asked == [FETCH_IT]
    assert "couldn't check it" in capsys.readouterr().out
    assert _sources(config)["webpage"] == [SITE]


@respx.mock
def test_add_canceled_at_the_prompt_sends_and_writes_nothing(config, monkeypatch, capsys):
    tty(monkeypatch)
    answers(monkeypatch)
    page = _serve_mastodon()
    assert main(["add", "-c", str(config), "https://github.com/lumen/app", MASTODON]) == 1
    assert "Canceled. Nothing was sent." in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG
    assert page.call_count == 0


@respx.mock
def test_add_without_a_terminal_fetches_nothing_without_yes(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    page = _serve_mastodon()
    assert main(["add", "-c", str(config), MASTODON, "https://github.com/lumen/app"]) == 0
    assert page.call_count == 0
    sources = _sources(config)
    assert sources["webpage"] == [MASTODON] and "https://github.com/lumen/app" in sources["git"]


@respx.mock
def test_add_without_a_terminal_cant_add_a_video_without_yes(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    assert main(["add", "-c", str(config), "https://youtu.be/dQw4w9WgXcQ"]) == 1
    assert "Not added" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG


@respx.mock
def test_add_yes_fetches_and_takes_the_best_match(config, monkeypatch):
    tty(monkeypatch, interactive=False)
    _serve_site(LINKS_FEED, NAMES_VERSION)
    assert main(["add", "-y", "-c", str(config), SITE, "https://github.com/lumen/app"]) == 0
    sources = _sources(config)
    assert sources["webpage"] == [SITE] and "https://github.com/lumen/app" in sources["git"]


@respx.mock
def test_add_test_asks_to_fetch_and_writes_nothing(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch, "")
    _serve_site(LINKS_FEED, NAMES_VERSION)
    assert main(["add", "--test", "-c", str(config), SITE]) == 0
    # Only the fetch is asked
    assert asked == [FETCH_IT]
    out = capsys.readouterr().out
    assert "the feed the page links" in out and f"+    - {SITE}" in out
    assert config.read_text(encoding="utf-8") == CONFIG


@respx.mock
def test_add_no_check_adds_a_page_as_given(config, monkeypatch):
    tty(monkeypatch)
    asked = answers(monkeypatch)
    assert main(["add", "--no-check", "-c", str(config), MASTODON]) == 0
    assert asked == []
    assert _sources(config)["webpage"] == [MASTODON]


@respx.mock
def test_add_finds_a_videos_channel(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    _serve_video()
    assert main(["add", "-y", "-c", str(config), "https://youtu.be/dQw4w9WgXcQ"]) == 0
    assert _sources(config)["youtube"] == ["@RickAstleyYT"]
    assert "the video's channel" in capsys.readouterr().out


@respx.mock
def test_add_says_when_a_video_cant_be_looked_up(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    respx.get(VIDEO_PAGE).mock(return_value=httpx.Response(404))
    assert main(["add", "-y", "-c", str(config), VIDEO_PAGE]) == 1
    assert "Couldn't look up" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG


def test_add_no_check_refuses_a_video(config, capsys):
    assert main(["add", "--no-check", "-c", str(config), VIDEO_PAGE]) == 1
    assert "is a video, not a" in capsys.readouterr().out


@respx.mock
def test_add_skips_the_lookup_for_what_is_already_watched(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    config.write_text(CONFIG + f"  webpage:\n    - {SITE}\n", encoding="utf-8")
    assert main(["add", "-y", "-c", str(config), SITE]) == 0
    assert f"Already watching {SITE} under webpage" in capsys.readouterr().out


def test_add_names_an_already_watched_entry_as_the_config_spells_it(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    config.write_text(CONFIG + "  crates:\n    - serde_json\n", encoding="utf-8")
    assert main(["add", "-y", "--no-check", "-c", str(config), "crates", "serde-json"]) == 0
    assert "Already watching serde_json under crates" in capsys.readouterr().out


# A single word or number: its source is shown and confirmed

ADD_IT = "Add it? [Y/n] "


class _Numbered:
    """Another source with plain-number ids"""

    name = "numbered"
    summary = "Something with numeric ids"

    def claim(self, value: str) -> str | None:
        return value if value.isdigit() else None

    def targets(self, entries):
        return [Target(source=self.name, key=str(e)) for e in entries]


def test_add_confirms_the_source_detected_for_a_number(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch, "")
    assert main(["add", "-c", str(config), "440"]) == 0
    assert asked == [ADD_IT]
    assert _sources(config)["steam"] == [440]
    out = capsys.readouterr().out
    assert "440 source detected: steam (Patch notes and news for Steam games)" in out


def test_add_declining_the_detected_source_writes_nothing(config, monkeypatch, capsys):
    tty(monkeypatch)
    answers(monkeypatch, "n")
    assert main(["add", "-c", str(config), "firefox"]) == 0
    assert "Left out firefox" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG


def test_add_canceled_at_the_detected_source_writes_nothing(config, monkeypatch, capsys):
    tty(monkeypatch)
    answers(monkeypatch)
    assert main(["add", "-c", str(config), "440", "https://github.com/lumen/app"]) == 1
    assert "Canceled. Nothing was written." in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == CONFIG


def test_add_asks_which_when_several_sources_read_a_bare_name(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch, "", "9", "6")
    assert main(["add", "-c", str(config), "rich"]) == 0
    assert asked == ["Add which? [1/2/3/4/5/6/7/n] "] * 3
    assert _sources(config)["pypi"] == ["rich"]
    out = capsys.readouterr().out
    assert "rich could be:" in out and "1. crates" in out and "6. pypi" in out


def test_add_asks_which_when_two_sources_claim_a_number(config, monkeypatch, capsys):
    sources = {**all_sources(), "numbered": _Numbered()}
    monkeypatch.setattr("youpdated.edit.all_sources", lambda: sources)
    monkeypatch.setattr("youpdated.cli.get_source", lambda name: sources.get(name))
    tty(monkeypatch)
    asked = answers(monkeypatch, "2")
    assert main(["add", "--test", "-c", str(config), "440"]) == 0
    assert asked == ["Add which? [1/2/n] "]
    out = capsys.readouterr().out
    assert "1. numbered (Something with numeric ids)" in out and "2. steam" in out
    assert "+  steam:" in out


def test_add_without_a_terminal_takes_the_only_source_detected(config, monkeypatch, capsys):
    tty(monkeypatch, interactive=False)
    asked = answers(monkeypatch)
    assert main(["add", "-c", str(config), "440", "@types/react"]) == 0
    assert asked == []
    sources = _sources(config)
    assert sources["steam"] == [440] and "@types/react" in sources["npm"]
    assert "440 source detected: steam" in capsys.readouterr().out


def test_add_yes_takes_the_only_source_detected_without_asking(config, monkeypatch):
    tty(monkeypatch)
    asked = answers(monkeypatch)
    assert main(["add", "-y", "-c", str(config), "440"]) == 0
    assert asked == []
    assert _sources(config)["steam"] == [440]


def test_add_yes_still_refuses_a_bare_name_several_sources_read(config, monkeypatch, capsys):
    tty(monkeypatch)
    asked = answers(monkeypatch)
    assert main(["add", "-y", "-c", str(config), "440", "rich"]) == 1
    assert asked == []
    assert "`rich` could be crates or homebrew or modrinth or npm or nuget or pypi or rubygems" in " ".join(capsys.readouterr().out.split())
    assert config.read_text(encoding="utf-8") == CONFIG


def test_add_no_check_takes_a_detected_source_without_asking(config, monkeypatch):
    tty(monkeypatch)
    asked = answers(monkeypatch)
    assert main(["add", "--no-check", "-c", str(config), "440"]) == 0
    assert asked == []
    assert _sources(config)["steam"] == [440]


@pytest.mark.parametrize(
    "value, bare",
    [
        ("440", True),
        ("rich", True),
        ("zope.interface", True),
        ("UCuAXFkgsw1L7xaCfnd5JJOw", True),
        ("@NASA", False),
        ("@types/react", False),
        ("neovim/neovim", False),
        ("cask:firefox", False),
        ("https://example.com", False),
    ],
)
def test_only_a_single_word_or_number_is_bare(value, bare):
    assert is_bare(value) is bare


@pytest.mark.parametrize(
    "value, appid",
    [
        ("440", "440"),
        ("https://store.steampowered.com/app/730/CounterStrike_2/", "730"),
        ("https://steamcommunity.com/app/570", "570"),
        ("store.steampowered.com/app/413150?l=english", "413150"),
        ("log4j", None),
        ("python3", None),
        ("UCuAXFkgsw1L7xaCfnd5JJOw", None),
    ],
)
def test_steam_reads_only_a_whole_number_or_an_app_link(value, appid):
    steam = get_source("steam")
    if appid is None:
        with pytest.raises(ConfigEntryError, match="is not an appid"):
            steam.targets([value])
    else:
        assert steam.targets([value])[0].key == appid


def test_a_word_with_a_digit_isnt_offered_as_a_steam_app():
    assert "steam" not in readers("log4j")
