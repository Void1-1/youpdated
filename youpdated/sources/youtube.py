"""YouTube channels and playlists

YouTube's RSS endpoint (``/feeds/videos.xml``) is the easiest, but it has outages: every
channel answers 404 for hours, a few times a year or daily for a stretch, through a
proxy or VPN too. So every fetch goes through a list:
    1. the official feed
    2. an Invidious instance (privacy-friendly, but flaky sometimes)
    3. the Data API, if YOUTUBE_API_KEY is set
"""

from __future__ import annotations

import ipaddress
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Iterable
from urllib.parse import quote, urlsplit

from ..icons import page_image
from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, parse_iso, require, url_parts
from .feed import feed_title, parse_document, parse_entries

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^(?:PL|UU|LL|FL|OL)[A-Za-z0-9_-]{10,}$")
PLAYLIST_URL_RE = re.compile(
    r"^\s*(?:https?://)?(?:www\.|m\.)?youtube\.com/playlist/?\?(?:[^#]*&)?list=", re.IGNORECASE
)
EXTERNAL_ID_RE = re.compile(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"')
# A channel picture's size, in its URL: `=s900-c-k-…`
PICTURE_SIZE_RE = re.compile(r"=s\d+(?=-|$)")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

CHANNEL_CACHE = "youtube_channel_ids"
# The path that last answered for a target, and when: "feed|<time>"
PATH_CACHE = "youtube_path"
PATH_SWITCH_SLACK = timedelta(days=1)
INSTANCE_CACHE = "youtube_invidious"
# Where a playlist's last Data API pages start, so the next check needn't walk to them
PAGE_CACHE = "youtube_playlist_pages"
# Videos kept from a channel, and from each end of a playlist: new ones can go at either
MAX_ITEMS = 15
# The official feed lists a playlist's first 15 videos, and no more
FEED_LENGTH = 15
# Invidious's playlist pages start 100 videos apart (and overlap)
INVIDIOUS_PAGE = 100
DATA_API_PAGE = 50
# 5,000 videos, max YouTube has in a playlist
MAX_DATA_API_PAGES = 100
# Instances asked per fetch, picked at random
MAX_INSTANCES = 4
KEPT_INSTANCES = 8
INSTANCE_LIST_AGE = timedelta(days=1)


@register
class YouTubeSource:
    name: ClassVar[str] = "youtube"
    summary: ClassVar[str] = "New videos on a channel or playlist"
    caches: ClassVar[tuple[str, ...]] = (CHANNEL_CACHE, PATH_CACHE, PAGE_CACHE)
    # Not keyed by a target
    shared_caches: ClassVar[tuple[str, ...]] = (INSTANCE_CACHE,)

    def claim(self, value: str) -> str | None:
        """@handle, channel id, channel's URL, or playlist URL"""
        url = url_parts(value)
        if (
            (value.startswith("@") and "/" not in value)
            or CHANNEL_ID_RE.match(value)
            or (url and url[0] in ("youtube.com", "m.youtube.com"))
        ):
            return accepted(self, value)
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            # A playlist's URL is a playlist without `playlist:`
            is_playlist_url = isinstance(entry, str) and PLAYLIST_URL_RE.match(entry)
            fields = entry_fields(entry, "playlist" if is_playlist_url else "channel", self.name)

            if fields.get("playlist"):
                playlist = str(fields["playlist"]).strip()
                playlist = _extract_playlist_id(playlist)
                if not PLAYLIST_ID_RE.match(playlist):
                    raise ConfigEntryError(
                        f"sources.{self.name}: `{playlist}` is not a playlist id or URL"
                    )
                targets.append(
                    Target(
                        source=self.name,
                        key=f"playlist:{playlist}",
                        # The playlist's title, once fetched
                        label=fields.get("name"),
                        params={"kind": "playlist", "id": playlist},
                    )
                )
                continue

            raw = str(require(fields, "channel", self.name)).strip()
            ref = _extract_channel_ref(raw)
            if ref is None:
                raise ConfigEntryError(
                    f"sources.{self.name}: `{raw}` is not a channel id, @handle, or channel URL"
                )
            targets.append(
                Target(
                    source=self.name,
                    key=ref,
                    label=fields.get("name"),
                    params={"kind": "channel", "ref": ref},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        if target.params["kind"] == "playlist":
            feed_id, api_id = ("playlist_id", target.params["id"])
        else:
            channel_id = self._channel_id(target.params["ref"], client)
            if channel_id is None:
                raise FetchError(
                    f"could not resolve YouTube channel `{target.params['ref']}` to a channel id"
                )
            feed_id, api_id = ("channel_id", channel_id)

        failures: list[str] = []

        for path, attempt in (
            ("feed", self._official_feed),
            ("invidious", self._invidious),
            ("api", self._data_api),
        ):
            try:
                updates = attempt(target, client, feed_id, api_id)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                continue
            if updates is not None:
                return self._since_last_path(target, client, path, updates)

        raise FetchError(
            "every YouTube path failed (" + "; ".join(failures) + "). "
            "The official feed has outages often, a later check likely works. "
            "Set YOUTUBE_API_KEY for more consistancy."
        )

    def _since_last_path(
        self, target: Target, client: Client, path: str, updates: list[Update]
    ) -> list[Update]:
        """``updates``, less those another path's check would have listed"""
        state = client.state
        if state is None or client.test:
            return updates
        now = datetime.now(timezone.utc)
        last_path, _, last_at = (state.cache_get(PATH_CACHE, target.key) or "").partition("|")
        state.cache_set(PATH_CACHE, target.key, f"{path}|{now.isoformat()}")
        if last_path == path or not state.is_baselined(self.name, target.key):
            return updates
        try:
            checked = datetime.fromisoformat(last_at) if last_at else state.last_run()
        except ValueError:
            checked = state.last_run()
        if checked is None:
            return updates
        cutoff = checked - PATH_SWITCH_SLACK
        return [
            u for u in updates
            if u.published is None or u.published >= cutoff or not state.is_new(u)
        ]

    # yt official feed

    def _official_feed(
        self, target: Target, client: Client, feed_id: str, api_id: str
    ) -> list[Update] | None:
        playlist = feed_id == "playlist_id"
        url = f"https://www.youtube.com/feeds/videos.xml?{feed_id}={api_id}"
        # A playlist's feed is read in full every time
        fetched = client.get(url, conditional=not playlist, soft_statuses=(404, 403))
        if fetched is None:
            return []
        if fetched.status in (403, 404):
            raise FetchError(f"official feed returned {fetched.status}")
        updates = self._label_and_parse(target, fetched.content)
        if playlist and len(updates) >= FEED_LENGTH:
            # New videos usually go at the end, past what the feed shows
            raise FetchError(f"official feed shows only a playlist's first {FEED_LENGTH} videos")
        return updates

    # invidious

    def _invidious(
        self, target: Target, client: Client, feed_id: str, api_id: str
    ) -> list[Update] | None:
        errors = []
        for base in self._instances(client):
            if feed_id == "channel_id":
                url = f"{base}/api/v1/channels/{api_id}/latest"
            else:
                url = f"{base}/api/v1/playlists/{api_id}"
            try:
                payload, videos = self._invidious_videos(client, url)
                if feed_id == "channel_id":
                    videos = videos[:MAX_ITEMS]
                else:
                    videos = self._invidious_playlist_ends(client, url, payload, videos)
            except FetchError as exc:
                errors.append(f"{base}: {exc}")
                continue
            try:
                updates = self._from_invidious(target, videos)
            except Exception as exc:  # the next instance might answer properly
                errors.append(f"{base}: {type(exc).__name__}: {exc}")
                continue
            if videos and not updates:
                errors.append(f"{base}: no readable videos in the reply")
                continue
            if not target.label and feed_id == "playlist_id" and isinstance(payload, dict):
                target.label = _title(payload.get("title"))
            client.note(f"youtube: by Invidious instance {base}")
            return updates
        raise FetchError("no Invidious instance answered (" + "; ".join(errors[:3]) + ")")

    def _invidious_videos(self, client: Client, url: str) -> tuple[Any, list[Any]]:
        """An instance's reply, and the videos in it"""
        fetched = client.get(url, soft_statuses=(403, 404, 429), retries=0)
        if fetched is None or fetched.status != 200:
            raise FetchError(f"HTTP {fetched.status if fetched else 'none'}")
        try:
            payload = fetched.json()
        except ValueError:
            raise FetchError("non-JSON response") from None
        videos = payload.get("videos") if isinstance(payload, dict) else payload
        if not isinstance(videos, list):
            raise FetchError("unexpected payload shape")
        return payload, videos

    def _invidious_playlist_ends(
        self, client: Client, url: str, payload: Any, videos: list[Any]
    ) -> list[Any]:
        """A playlist's first and last videos; its first page may not reach the end"""
        count = payload.get("videoCount") if isinstance(payload, dict) else None
        if isinstance(count, int) and not isinstance(count, bool) and count > len(videos):
            page = max(2, -(-count // INVIDIOUS_PAGE))
            _, tail = self._invidious_videos(client, f"{url}?page={page}")
            if not tail:
                raise FetchError(f"no videos on page {page} of {count} videos")
            return videos[:MAX_ITEMS] + tail[-MAX_ITEMS:]
        return videos[:MAX_ITEMS] + videos[MAX_ITEMS:][-MAX_ITEMS:]

    def _instances(self, client: Client) -> list[str]:
        """A random few of the kept instances, so each of them gets a turn"""
        bases = self._instance_list(client)
        return random.sample(bases, min(MAX_INSTANCES, len(bases)))

    def _instance_list(self, client: Client) -> list[str]:
        state = client.state
        cached = state.cache_get(INSTANCE_CACHE, "list") if state else None
        kept = [b for b in map(_instance_base, (cached or "").split(",")) if b]
        fetched_at = state.cache_get(INSTANCE_CACHE, "fetched") if state else None
        fresh_since = (datetime.now(timezone.utc) - INSTANCE_LIST_AGE).isoformat()
        if kept and fetched_at and fetched_at > fresh_since:
            return kept

        # Out of date or missing; an old list still beats none if the directory is down
        try:
            fetched = client.get("https://api.invidious.io/instances.json", retries=1)
        except FetchError:
            return kept
        if fetched is None:
            return kept
        try:
            listing = fetched.json()
        except ValueError:
            return kept

        bases = []
        for item in listing if isinstance(listing, list) else ():
            if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict)):
                continue
            meta = item[1]
            base = _instance_base(meta.get("uri"))
            if meta.get("type") == "https" and meta.get("api") and base:
                bases.append(base)
        bases = bases[:KEPT_INSTANCES]
        if bases and state:
            state.cache_set(INSTANCE_CACHE, "list", ",".join(bases))
            state.cache_set(INSTANCE_CACHE, "fetched", datetime.now(timezone.utc).isoformat())
        return bases or kept

    def _from_invidious(self, target: Target, videos: list[Any]) -> list[Update]:
        updates = []
        found = set()
        for video in videos:
            if not isinstance(video, dict):
                continue
            vid = video.get("videoId")
            # A playlist's two ends can overlap, and a video can be in it twice
            if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid) or vid in found:
                continue
            found.add(vid)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"yt:video:{vid}",
                    title=video.get("title") or "(untitled)",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    published=_from_timestamp(video.get("published")),
                    body=(video.get("description") or None),
                    tags=("video",),
                )
            )
            # A playlist's videos can be by anyone; its title is in the reply around them
            if not target.label and target.params["kind"] == "channel":
                target.label = _title(video.get("author"))
        return updates

    # Data API

    def _data_api(
        self, target: Target, client: Client, feed_id: str, api_id: str
    ) -> list[Update] | None:
        key = os.environ.get("YOUTUBE_API_KEY")
        if not key:
            raise FetchError("YOUTUBE_API_KEY not set")

        playlist = feed_id == "playlist_id"
        # channel's uploads playlist is id with UC swapped for UU, newest first
        playlist_id = api_id if playlist else "UU" + api_id[2:]
        url = (
            "https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=snippet&maxResults={DATA_API_PAGE if playlist else MAX_ITEMS}"
            f"&playlistId={playlist_id}"
        )
        page = self._data_api_page(client, url, key, None)
        if page is None:
            return []
        client.note("youtube: by Data API")
        items, following = page
        items = items[:MAX_ITEMS]
        if playlist and following:
            items += self._data_api_playlist_end(target, client, url, key, following)

        updates = []
        found = set()
        for item in items:
            snippet = item.get("snippet") if isinstance(item, dict) else None
            if not isinstance(snippet, dict):
                continue
            resource = snippet.get("resourceId")
            vid = resource.get("videoId") if isinstance(resource, dict) else None
            # A playlist's two ends can overlap, and a video can be in it twice
            if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid) or vid in found:
                continue
            found.add(vid)
            # On a playlist's items, the channel is the playlist owner's
            if not target.label and target.params["kind"] == "channel":
                target.label = _title(snippet.get("channelTitle"))
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"yt:video:{vid}",
                    title=snippet.get("title") or "(untitled)",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    published=parse_iso(snippet.get("publishedAt")),
                    body=str(snippet.get("description") or "")[:400] or None,
                    tags=("video",),
                )
            )
        if not target.label and playlist:
            target.label = self._playlist_title(client, api_id, key)
        return updates

    def _data_api_page(
        self, client: Client, url: str, key: str, token: str | None
    ) -> tuple[list[Any], str | None] | None:
        """A page's items and the next page's token; None with ``--test``"""
        if token:
            url += f"&pageToken={quote(token, safe='')}"
        fetched = client.get(url, headers={"X-Goog-Api-Key": key})
        if fetched is None:
            return None
        payload = fetched.json()
        items = payload.get("items", []) if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise FetchError("Data API: unexpected payload shape")
        following = payload.get("nextPageToken")
        return items, following if isinstance(following, str) and following else None

    def _data_api_playlist_end(
        self, target: Target, client: Client, url: str, key: str, second: str
    ) -> list[Any]:
        """A long playlist's last items. Pages can only be read in order, so this starts
        from where the last check found the end, or else the second page"""
        state = client.state
        saved = state.cache_get(PAGE_CACHE, target.key) if state else None
        starts = [saved, second] if saved and saved != second else [second]
        for start in starts:
            # (token, items) of the last two pages read
            read: list[tuple[str, list[Any]]] = []
            token: str | None = start
            try:
                for _ in range(MAX_DATA_API_PAGES):
                    page = self._data_api_page(client, url, key, token)
                    if page is None or not page[0]:
                        break
                    read = [*read[-1:], (token, page[0])]
                    token = page[1]
                    if token is None:
                        break
                else:
                    raise FetchError(f"Data API: playlist longer than {MAX_DATA_API_PAGES} pages")
            except FetchError:
                if start == second:
                    raise
                continue  # the saved place is no longer valid; walk from the start
            if not read:
                continue  # past the end of a playlist that got shorter
            if state is not None:
                state.cache_set(PAGE_CACHE, target.key, read[0][0])
            return [item for _, items in read for item in items][-MAX_ITEMS:]
        return []

    def _playlist_title(self, client: Client, playlist_id: str, key: str) -> str | None:
        """A playlist's title from the Data API; None if it can't say"""
        url = f"https://www.googleapis.com/youtube/v3/playlists?part=snippet&id={playlist_id}"
        try:
            fetched = client.get(url, headers={"X-Goog-Api-Key": key}, retries=0)
            items = fetched.json().get("items") if fetched else None
            if not items:
                return None
            return _title(items[0]["snippet"]["title"])
        except Exception:  # the videos matter, the name is a nicety
            return None

    # helpers

    def _label_and_parse(self, target: Target, content: bytes) -> list[Update]:
        parsed = parse_document(content)
        # The Invidious and Data API paths both name the channel or playlist; take it
        # from feed so the label does not depend on which path answered.
        if not target.label:
            target.label = feed_title(parsed)
        updates = parse_entries(
            parsed,
            source=self.name,
            target=target.key,
            limit=MAX_ITEMS,
            tags=("video",),
        )
        # Rewrite uids to the video id so the three paths dedupe
        normalized = []
        for update in updates:
            # Entry ids are already ``yt:video:<id>``
            vid = _video_id(update.url) or update.uid.removeprefix("yt:video:")
            normalized.append(
                Update(
                    source=update.source,
                    target=update.target,
                    uid=f"yt:video:{vid}",
                    title=update.title,
                    url=update.url,
                    published=update.published,
                    version=update.version,
                    body=update.body,
                    tags=update.tags,
                )
            )
        return normalized

    def icon(self, target: Target, client: Client) -> str | None:
        """Channel's picture, from ``og:image``"""
        if target.params["kind"] != "channel":
            return None
        ref = target.params["ref"]
        if CHANNEL_ID_RE.match(ref):
            page = f"https://www.youtube.com/channel/{ref}"
        else:
            page = f"https://www.youtube.com/{ref}" if ref.startswith("@") else ref
        fetched = client.get(page)
        if fetched is None:
            return None
        url = page_image(fetched.text, page)
        return PICTURE_SIZE_RE.sub("=s176", url, count=1) if url else None

    def _channel_id(self, ref: str, client: Client) -> str | None:
        if CHANNEL_ID_RE.match(ref):
            return ref
        state = client.state
        if state is not None:
            cached = state.cache_get(CHANNEL_CACHE, ref)
            if cached:
                return cached

        page = f"https://www.youtube.com/{ref}" if ref.startswith("@") else ref
        try:
            fetched = client.get(page, soft_statuses=(404,))
        except FetchError:
            return None
        if fetched is None or fetched.status != 200:
            return None

        match = EXTERNAL_ID_RE.search(fetched.text)
        if not match:
            return None
        channel_id = match.group(1)
        if state is not None:
            state.cache_set(CHANNEL_CACHE, ref, channel_id)
        return channel_id


def _extract_channel_ref(raw: str) -> str | None:
    """Reduce input to channel id or @handle"""
    if CHANNEL_ID_RE.match(raw):
        return raw
    if raw.startswith("@") and len(raw) > 1:
        return raw
    match = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})", raw)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/(@[A-Za-z0-9._-]+)", raw)
    if match:
        return match.group(1)
    return None


def _extract_playlist_id(raw: str) -> str:
    match = re.search(r"[?&]list=([A-Za-z0-9_-]+)", raw)
    return match.group(1) if match else raw


def _title(value: Any) -> str | None:
    """A channel or playlist name from a reply, or None"""
    return value.strip() or None if isinstance(value, str) else None


def _video_id(url: str) -> str | None:
    # Shorts are linked as /shorts/<id> rather than watch?v=<id>
    match = re.search(r"(?:[?&]v=|/shorts/)([A-Za-z0-9_-]{11})", url or "")
    return match.group(1) if match else None


def _from_timestamp(value: Any) -> datetime | None:
    """Invidious's ``published`` (Unix seconds), or None"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _instance_base(uri: Any) -> str | None:
    """``https://host`` of a listed Invidious instance, or None unless its public"""
    if not isinstance(uri, str):
        return None
    try:
        parts = urlsplit(uri.strip())
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return None
    if port not in (None, 443) or "." not in host or host.endswith((".local", ".localhost")):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return f"https://{host}"
    return None
