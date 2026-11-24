"""App Store apps (iPhone, iPad, and Mac) from Apple's public lookup API

The lookup gives only the current version, with its release notes and date: one item per check.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable
from urllib.parse import urlencode

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, parse_iso, require, url_parts

LOOKUP = "https://itunes.apple.com/lookup"
DEFAULT_COUNTRY = "us"
ID_RE = re.compile(r"^(?:id)?(\d{6,12})$")
BUNDLE_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
COUNTRY_RE = re.compile(r"^[a-z]{2}$")


def _parse_app(value: str, source: str) -> tuple[str, str]:
    """``(kind, value)`` where kind is `id` or `bundleId`"""
    match = ID_RE.match(value)
    if match:
        return "id", match.group(1)
    if BUNDLE_RE.match(value):
        return "bundleId", value
    raise ConfigEntryError(
        f"sources.{source}: `{value}` is not an App Store app, "
        "expected its number (284882215 or id284882215) or bundle id (com.example.App)"
    )


@register
class AppStoreSource:
    name: ClassVar[str] = "appstore"
    summary: ClassVar[str] = "New versions of an App Store app"

    def claim(self, value: str) -> str | None:
        """app page on apps.apple.com, as `cc/id` outside the US"""
        url = url_parts(value)
        if url and url[0] in ("apps.apple.com", "itunes.apple.com"):
            segments = url[1].strip("/").split("/")
            match = ID_RE.match(segments[-1]) if segments else None
            if match and "app" in segments:
                country = segments[0].lower() if COUNTRY_RE.match(segments[0].lower()) else None
                if country and country != DEFAULT_COUNTRY:
                    return accepted(self, f"{country}/{match.group(1)}")
                return accepted(self, match.group(1))
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "app", self.name)
            value = str(require(fields, "app", self.name)).strip()
            country = str(fields.get("country") or "").strip().lower()
            # `gb/284882215` for the UK store
            prefix, sep, rest = value.partition("/")
            if sep:
                country, value = country or prefix.lower(), rest.strip()
            country = country or DEFAULT_COUNTRY
            if not COUNTRY_RE.match(country):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{country}` is not a two-letter country code"
                )
            kind, app = _parse_app(value, self.name)
            key = app if country == DEFAULT_COUNTRY else f"{country}/{app}"
            targets.append(
                Target(
                    source=self.name,
                    key=key,
                    label=fields.get("name"),
                    params={"kind": kind, "app": app, "country": country},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        params = {target.params["kind"]: target.params["app"], "country": target.params["country"]}
        fetched = client.get(f"{LOOKUP}?{urlencode(params)}", conditional=True)
        if fetched is None:
            return []
        results = fetched.json().get("results") or []
        if not results:
            raise FetchError(
                f"no app `{target.params['app']}` in the {target.params['country'].upper()} App Store"
            )
        app = results[0]
        if not target.label:
            target.label = app.get("trackName")
        version = app.get("version")
        if not version:
            return []
        name = app.get("trackName") or target.display
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"version:{version}",
                title=f"{name} {version}",
                url=app.get("trackViewUrl") or f"https://apps.apple.com/app/id{app.get('trackId')}",
                published=parse_iso(app.get("currentVersionReleaseDate")),
                version=str(version),
                body=app.get("releaseNotes"),
                tags=("release", "latest"),
            )
        ]
