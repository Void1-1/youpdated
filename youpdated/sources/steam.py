"""Steam patch notes and news for a game.

Config can be an appid; the store name is resolved and cached locally so reports stay readable
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, require, url_parts
from .feed import parse_feed

NAME_CACHE = "steam_app_names"
# Store event types of update posts: small, regular, and major updates
UPDATE_EVENTS = "12,13,14"


@register
class SteamSource:
    name: ClassVar[str] = "steam"
    summary: ClassVar[str] = "Patch notes and news for Steam games"
    caches: ClassVar[tuple[str, ...]] = (NAME_CACHE,)

    def claim(self, value: str) -> str | None:
        """plain number, or store or community page for an app"""
        url = url_parts(value)
        if value.isdigit() or (
            url and url[0] in ("store.steampowered.com", "steamcommunity.com") and "/app/" in url[1]
        ):
            return accepted(self, value)
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "appid", self.name)
            raw = str(require(fields, "appid", self.name)).strip()
            # Accept store URL and id
            match = re.search(r"(?:app/)?(\d+)", raw)
            if not match:
                raise ConfigEntryError(
                    f"sources.{self.name}: `{raw}` is not an appid or a store URL"
                )
            appid = match.group(1)
            targets.append(
                Target(
                    source=self.name,
                    key=appid,
                    label=fields.get("name"),
                    params={},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        if not target.label:
            target.label = self._app_name(target.key, client)

        url = (
            f"https://store.steampowered.com/feeds/news/app/{target.key}/"
            "?cc=US&l=english"
        )
        fetched = client.get(url, conditional=True)
        if fetched is None:
            return []

        updates = parse_feed(
            fetched.content,
            source=self.name,
            target=target.key,
            tags=("news",),
        )
        if target.track_latest:
            latest = self._latest_update_post(target, client)
            if latest is not None and all(u.uid != latest.uid for u in updates):
                updates.append(latest)
        return updates

    def _latest_update_post(self, target: Target, client: Client) -> Update | None:
        """The newest patch notes post"""
        fetched = client.get(
            "https://store.steampowered.com/events/ajaxgetadjacentpartnerevents/"
            f"?appid={target.key}&count_before=0&count_after=1"
            f"&event_type_filter={UPDATE_EVENTS}&lang_list=0",
            conditional=True,
        )
        if fetched is None:
            return None
        try:
            event = (fetched.json().get("events") or [None])[0]
        except (ValueError, AttributeError):
            return None
        if not isinstance(event, dict) or not event.get("gid"):
            return None
        posted = (event.get("announcement_body") or {}).get("posttime")
        posted = posted or event.get("rtime32_start_time")
        page = f"https://store.steampowered.com/news/app/{target.key}/view/{event['gid']}"
        return Update(
            source=self.name,
            target=target.key,
            uid=page,
            title=str(event.get("event_name") or "").strip() or "(untitled)",
            url=page,
            published=datetime.fromtimestamp(posted, timezone.utc)
            if isinstance(posted, int)
            else None,
            tags=("news",),
        )

    def _app_name(self, appid: str, client: Client) -> str | None:
        """Resolve store name once then cache"""
        state = client.state
        if state is not None:
            cached = state.cache_get(NAME_CACHE, appid)
            if cached:
                return cached
        try:
            fetched = client.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
            )
        except FetchError:
            return None
        if fetched is None:
            return None
        try:
            payload = fetched.json().get(appid) or {}
            name = (payload.get("data") or {}).get("name")
        except (ValueError, AttributeError):
            return None
        if name and state is not None:
            state.cache_set(NAME_CACHE, appid, name)
        return name
