"""GOG game builds from GOG's content system"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, parse_iso, recent_versions, require, url_parts

BUILDS = "https://content-system.gog.com/products/{id}/os/{os}/builds?generation=2"
TITLE_CACHE = "gog_title"
OS_NAMES = {"windows": "windows", "mac": "osx", "macos": "osx", "osx": "osx"}
PRODUCT_RE = re.compile(r"^\d{4,12}$")
# Product id on a store page
CARD_PRODUCT_RE = re.compile(r'card-product="(\d+)"')


def _game_page(value: str) -> bool:
    url = url_parts(value)
    if not url or url[0] != "gog.com":
        return False
    segments = url[1].strip("/").split("/")
    return "game" in segments[:2] and segments[-1] != "game"


@register
class GOGSource:
    name: ClassVar[str] = "gog"
    summary: ClassVar[str] = "New builds of a GOG game"
    caches: ClassVar[tuple[str, ...]] = (TITLE_CACHE,)

    def resolves(self, value: str) -> bool:
        """A game's store page: its product id is on the page"""
        return _game_page(value)

    def resolve(self, value: str, client: Client) -> tuple[str, str]:
        """``(entry, why)``: the product id on the game's store page"""
        fetched = client.get(value, soft_statuses=(404,))
        if fetched is None or fetched.status != 200:
            status = fetched.status if fetched is not None else "none"
            raise FetchError(f"couldn't open the game's page {value} (HTTP {status})")
        match = CARD_PRODUCT_RE.search(fetched.text)
        if not match:
            raise FetchError(f"no GOG product id on {value}")
        return match.group(1), "the game's product id"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "product", self.name)
            product = str(require(fields, "product", self.name)).strip()
            if not PRODUCT_RE.match(product):
                raise ConfigEntryError(
                    f"sources.{self.name}: `{product}` is not a GOG product id, "
                    "expected a number like 1207658924"
                )
            given_os = str(fields.get("os") or "windows").strip().lower()
            if given_os not in OS_NAMES:
                raise ConfigEntryError(
                    f"sources.{self.name}: os `{given_os}` is not one of windows or mac"
                )
            os_name = OS_NAMES[given_os]
            targets.append(
                Target(
                    source=self.name,
                    key=product if os_name == "windows" else f"{product}/mac",
                    label=fields.get("name"),
                    params={"product": product, "os": os_name},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        product = target.params["product"]
        url = BUILDS.format(id=product, os=target.params["os"])
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no GOG product `{product}`")
        page = self._fill_label(target, client)

        builds = {
            str(b["build_id"]): b
            for b in (fetched.json() or {}).get("items") or ()
            if b.get("build_id") and b.get("public", True)
        }
        if not builds and not self._exists(product, client):
            raise FetchError(f"no GOG product `{product}`")
        on_branch = lambda bid: bool(builds[bid].get("branch"))  # noqa: E731
        ordered = sorted(
            builds, key=lambda bid: str(builds[bid].get("date_published") or ""), reverse=True
        )
        latest = next((bid for bid in ordered if not on_branch(bid)), None)
        listed = recent_versions(ordered, on_branch, latest)

        updates = []
        for bid in ordered:
            if bid not in listed:
                continue
            build = builds[bid]
            version = str(build.get("version_name") or "").strip() or None
            tags = ("release",)
            if on_branch(bid):
                tags += ("prerelease", str(build["branch"]).lower())
            if bid == latest:
                tags += ("latest",)
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"build:{bid}",
                    title=f"{target.display} {version or 'build ' + bid}",
                    url=page,
                    published=parse_iso(build.get("date_published")),
                    version=version,
                    tags=tags,
                )
            )
        return updates

    def _exists(self, product: str, client: Client) -> bool:
        """If the product API knows ``product``"""
        fetched = client.get(f"https://api.gog.com/products/{product}", soft_statuses=(404,))
        return fetched is None or fetched.status != 404

    def _fill_label(self, target: Target, client: Client) -> str:
        """the game's title, looked up once with its store page, which is returned"""
        product = target.params["product"]
        fallback = f"https://www.gogdb.org/product/{product}"
        cached = client.state.cache_get(TITLE_CACHE, target.key) if client.state else None
        if cached is None:
            fetched = client.get(f"https://api.gog.com/products/{product}", soft_statuses=(404,))
            if fetched is None or fetched.status != 200:
                return fallback
            doc = fetched.json() or {}
            found = {
                "title": str(doc.get("title") or ""),
                "page": str((doc.get("links") or {}).get("product_card") or ""),
            }
            cached = json.dumps(found, sort_keys=True)
            if found["title"] and client.state:
                client.state.cache_set(TITLE_CACHE, target.key, cached)
        found = json.loads(cached)
        if not target.label:
            target.label = found.get("title") or None
        return found.get("page") or fallback
