"""YouTube channels and playlists

YouTube's RSS endpoint (``/feeds/videos.xml``) is the easiest,
but its iffy and 404s, then serves fine later. (endpoints work, it's throttling)

Every fetch goes through a list:
    1. the official feed
    2. an Invidious instance (privacy-friendly, but flaky sometimes)
    3. the Data API, if YOUTUBE_API_KEY is set

Set ``privacy.proxy`` and step 1 generally works
"""

from __future__ import annotations

import ipaddress
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Iterable
from urllib.parse import urlsplit

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, parse_iso, require, url_parts
from .feed import feed_title, parse_document, parse_entries

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^(?:PL|UU|LL|FL|OL)[A-Za-z0-9_-]{10,}$")
EXTERNAL_ID_RE = re.compile(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"')
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

CHANNEL_CACHE = "youtube_channel_ids"
INSTANCE_CACHE = "youtube_invidious"
MAX_ITEMS = 15
# Instances asked per fetch, picked at random
MAX_INSTANCES = 4
KEPT_INSTANCES = 8
INSTANCE_LIST_AGE = timedelta(days=1)


@register
class YouTubeSource:
    name: ClassVar[str] = "youtube"
    summary: ClassVar[str] = "New videos on a channel or playlist"
    caches: ClassVar[tuple[str, ...]] = (CHANNEL_CACHE,)
    # Not keyed by a target
    shared_caches: ClassVar[tuple[str, ...]] = (INSTANCE_CACHE,)

    def claim(self, value: str) -> str | None:
        """@handle, channel id, or channel's URL"""
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
            fields = entry_fields(entry, "channel", self.name)

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
                        label=fields.get("name") or f"playlist {playlist}",
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

        for attempt in (self._official_feed, self._invidious, self._data_api):
            try:
                updates = attempt(target, client, feed_id, api_id)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                continue
            if updates is not None:
                return updates

        raise FetchError(
            "every YouTube path failed (" + "; ".join(failures) + "). "
            "The official feed throttles, retrying often works, or set "
            "privacy.proxy, or YOUTUBE_API_KEY."
        )

    # yt official feed

    def _official_feed(
        self, target: Target, client: Client, feed_id: str, api_id: str
    ) -> list[Update] | None:
        url = f"https://www.youtube.com/feeds/videos.xml?{feed_id}={api_id}"
        fetched = client.get(url, conditional=True, soft_statuses=(404, 403))
        if fetched is None:
            return []
        if fetched.status in (403, 404):
            raise FetchError(f"official feed returned {fetched.status} (likely blocked)")
        return self._label_and_parse(target, fetched.content)

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
                fetched = client.get(url, soft_statuses=(403, 404, 429), retries=0)
            except FetchError as exc:
                errors.append(str(exc))
                continue
            if fetched is None or fetched.status != 200:
                errors.append(f"{base}: HTTP {fetched.status if fetched else 'none'}")
                continue
            try:
                payload = fetched.json()
            except ValueError:
                errors.append(f"{base}: non-JSON response")
                continue
            videos = payload.get("videos") if isinstance(payload, dict) else payload
            if not isinstance(videos, list):
                errors.append(f"{base}: unexpected payload shape")
                continue
            try:
                updates = self._from_invidious(target, videos)
            except Exception as exc:  # the next instance might answer properly
                errors.append(f"{base}: {type(exc).__name__}: {exc}")
                continue
            if videos and not updates:
                errors.append(f"{base}: no readable videos in the reply")
                continue
            client.note(f"youtube: by Invidious instance {base}")
            return updates
        raise FetchError("no Invidious instance answered (" + "; ".join(errors[:3]) + ")")

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
        for video in videos[:MAX_ITEMS]:
            if not isinstance(video, dict):
                continue
            vid = video.get("videoId")
            if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid):
                continue
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
            if not target.label and isinstance(video.get("author"), str):
                target.label = video["author"]
        return updates

    # Data API

    def _data_api(
        self, target: Target, client: Client, feed_id: str, api_id: str
    ) -> list[Update] | None:
        key = os.environ.get("YOUTUBE_API_KEY")
        if not key:
            raise FetchError("YOUTUBE_API_KEY not set")

        # channel's uploads playlist is id with UC swapped for UU
        playlist_id = api_id if feed_id == "playlist_id" else "UU" + api_id[2:]
        url = (
            "https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=snippet&maxResults={MAX_ITEMS}&playlistId={playlist_id}"
        )
        # In a header, the key stays out of the URL that -v, --test, and errors print, and
        # a redirect off Google's host drops it
        fetched = client.get(url, headers={"X-Goog-Api-Key": key})
        if fetched is None:
            return []
        client.note("youtube: by Data API")

        payload = fetched.json()
        items = payload.get("items", []) if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise FetchError("Data API: unexpected payload shape")
        updates = []
        for item in items:
            snippet = item.get("snippet") if isinstance(item, dict) else None
            if not isinstance(snippet, dict):
                continue
            resource = snippet.get("resourceId")
            vid = resource.get("videoId") if isinstance(resource, dict) else None
            if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid):
                continue
            if not target.label and isinstance(snippet.get("channelTitle"), str):
                target.label = snippet["channelTitle"]
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
        return updates

    # helpers

    def _label_and_parse(self, target: Target, content: bytes) -> list[Update]:
        parsed = parse_document(content)
        # The Invidious and Data API paths both name the channel; take it from
        # feed so the label does not depend on which path answered.
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
