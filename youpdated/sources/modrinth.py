"""Minecraft mods, plugins, and pack versions from the Modrinth API"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Iterable
from urllib.parse import urlencode

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import (
    ConfigEntryError, accepted, entry_fields, newest_first, parse_iso, recent_versions, require,
    url_parts,
)
from .feed import html_to_text

API = "https://api.modrinth.com/v2"
TITLE_CACHE = "modrinth_title"
SLUG_RE = re.compile(r"^[\w!@$()`.+,\"\-']{3,64}$")
PROJECT_TYPES = ("mod", "plugin", "resourcepack", "shader", "datapack", "modpack", "project")
_UNSTABLE = ("beta", "alpha")


def _string_list(fields: dict[str, Any], key: str, source: str) -> list[str]:
    value = fields.get(key)
    if value is None:
        return []
    values = [value] if isinstance(value, (str, int, float)) else value
    if not isinstance(values, list) or not all(isinstance(v, (str, int, float)) for v in values):
        raise ConfigEntryError(f"sources.{source}: `{key}` must be a value or a list of values")
    return [str(v).strip() for v in values if str(v).strip()]


@register
class ModrinthSource:
    name: ClassVar[str] = "modrinth"
    summary: ClassVar[str] = "New versions of a Modrinth mod, plugin, or pack"
    caches: ClassVar[tuple[str, ...]] = (TITLE_CACHE,)

    def claim(self, value: str) -> str | None:
        """project page on modrinth.com"""
        url = url_parts(value)
        if url and url[0] == "modrinth.com":
            segments = url[1].strip("/").split("/")
            if len(segments) >= 2 and segments[0] in PROJECT_TYPES:
                return accepted(self, segments[1])
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "project", self.name)
            project = str(require(fields, "project", self.name)).strip()
            if not SLUG_RE.match(project):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{project}` is not a Modrinth project slug or id"
                )
            targets.append(
                Target(
                    source=self.name,
                    key=project.lower(),
                    label=fields.get("name"),
                    params={
                        "loaders": _string_list(fields, "loaders", self.name),
                        "game_versions": _string_list(fields, "game_versions", self.name),
                    },
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        query = {
            key: json.dumps(target.params[key])
            for key in ("loaders", "game_versions")
            if target.params.get(key)
        }
        url = f"{API}/project/{target.key}/version" + (f"?{urlencode(query)}" if query else "")
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no project `{target.key}` on Modrinth")
        self._fill_label(target, client)

        versions = {str(v["id"]): v for v in fetched.json() or () if v.get("id")}
        unstable = lambda vid: versions[vid].get("version_type") in _UNSTABLE  # noqa: E731
        dated = newest_first([(vid, parse_iso(v.get("date_published"))) for vid, v in versions.items()])
        ordered = [vid for vid, _ in dated]
        latest = next((vid for vid in ordered if not unstable(vid)), None)
        listed = recent_versions(ordered, unstable, latest)

        updates = []
        for vid, published in dated:
            if vid not in listed:
                continue
            item = versions[vid]
            number = str(item.get("version_number") or vid)
            tags = ("release",)
            if unstable(vid):
                tags += ("prerelease", str(item["version_type"]))
            if vid == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{vid}",
                    title=item.get("name") or f"{target.display} {number}",
                    url=f"https://modrinth.com/project/{item.get('project_id') or target.key}"
                    f"/version/{vid}",
                    published=published,
                    version=number,
                    body=html_to_text(item.get("changelog")),
                    tags=tags,
                )
            )
        return updates

    def _fill_label(self, target: Target, client: Client) -> None:
        """the project's title, looked up once"""
        if target.label:
            return
        title = client.state.cache_get(TITLE_CACHE, target.key) if client.state else None
        if title is None:
            fetched = client.get(f"{API}/project/{target.key}", soft_statuses=(404,))
            if fetched is None or fetched.status != 200:
                return
            title = str((fetched.json() or {}).get("title") or "")
            if title and client.state:
                client.state.cache_set(TITLE_CACHE, target.key, title)
        target.label = title or None

# no curseforge source due to api restrictions, if that changes this may become either minecraft.py or mods.py