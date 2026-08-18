"""Steam patch notes and news for a game.

Config can be an appid; the store name is resolved and cached locally so reports stay readable
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import parse_feed

NAME_CACHE = "steam_app_names"


@register
class SteamSource:
    name: ClassVar[str] = "steam"
    summary: ClassVar[str] = "Patch notes and news for Steam games"

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

        return parse_feed(
            fetched.content,
            source=self.name,
            target=target.key,
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
