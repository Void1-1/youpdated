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

import os
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import parse_feed

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^(?:PL|UU|LL|FL|OL)[A-Za-z0-9_-]{10,}$")
EXTERNAL_ID_RE = re.compile(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"')

CHANNEL_CACHE = "youtube_channel_ids"
INSTANCE_CACHE = "youtube_invidious"
MAX_ITEMS = 15
MAX_INSTANCES = 4


@register
class YouTubeSource:
    name: ClassVar[str] = "youtube"
    summary: ClassVar[str] = "New videos on a channel or playlist"

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
        return self._label_and_parse(target, fetched.content, via="feed")

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
            client.note(f"youtube: by Invidious instance {base}")
            return self._from_invidious(target, videos)
        raise FetchError("no Invidious instance answered (" + "; ".join(errors[:3]) + ")")

    def _instances(self, client: Client) -> list[str]:
        cached = client.state.cache_get(INSTANCE_CACHE, "list") if client.state else None
        if cached:
            return cached.split(",")[:MAX_INSTANCES]
        try:
            fetched = client.get("https://api.invidious.io/instances.json", retries=1)
        except FetchError:
            return []
        if fetched is None:
            return []
        try:
            listing = fetched.json()
        except ValueError:
            return []

        bases = []
        for item in listing:
            if not (isinstance(item, list) and len(item) == 2):
                continue
            meta = item[1] or {}
            if meta.get("type") == "https" and meta.get("api") and meta.get("uri"):
                bases.append(str(meta["uri"]).rstrip("/"))
        if bases and client.state:
            client.state.cache_set(INSTANCE_CACHE, "list", ",".join(bases[:8]))
        return bases[:MAX_INSTANCES]

    def _from_invidious(self, target: Target, videos: list[dict]) -> list[Update]:
        updates = []
        for video in videos[:MAX_ITEMS]:
            vid = video.get("videoId")
            if not vid:
                continue
            published = video.get("published")
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"yt:video:{vid}",
                    title=video.get("title") or "(untitled)",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    published=(
                        datetime.fromtimestamp(published, tz=timezone.utc)
                        if isinstance(published, (int, float))
                        else None
                    ),
                    body=(video.get("description") or None),
                    tags=("video",),
                )
            )
            if not target.label and video.get("author"):
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
            f"?part=snippet&maxResults={MAX_ITEMS}&playlistId={playlist_id}&key={key}"
        )
        fetched = client.get(url)
        if fetched is None:
            return []
        client.note("youtube: by Data API")

        updates = []
        for item in fetched.json().get("items", []):
            snippet = item.get("snippet") or {}
            vid = (snippet.get("resourceId") or {}).get("videoId")
            if not vid:
                continue
            if not target.label and snippet.get("channelTitle"):
                target.label = snippet["channelTitle"]
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"yt:video:{vid}",
                    title=snippet.get("title") or "(untitled)",
                    url=f"https://www.youtube.com/watch?v={vid}",
                    published=_parse_iso(snippet.get("publishedAt")),
                    body=(snippet.get("description") or None)[:400] if snippet.get("description") else None,
                    tags=("video",),
                )
            )
        return updates

    # helpers

    def _label_and_parse(self, target: Target, content: bytes, via: str) -> list[Update]:
        updates = parse_feed(
            content,
            source=self.name,
            target=target.key,
            limit=MAX_ITEMS,
            tags=("video",),
        )
        # Rewrite uids to the video id so the three paths dedupe
        normalized = []
        for update in updates:
            vid = _video_id(update.url) or update.uid
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
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url or "")
    return match.group(1) if match else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
