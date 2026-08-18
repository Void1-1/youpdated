"""npm package releases from the public registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable
from urllib.parse import quote

from ..http import Client
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require

# keys alongside real versions in the registry `time` map
_NON_VERSION_KEYS = {"created", "modified"}
MAX_VERSIONS = 10


@register
class NpmSource:
    name: ClassVar[str] = "npm"
    summary: ClassVar[str] = "New published versions of a package"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            package = str(require(fields, "package", self.name)).strip()
            if not package or package.startswith("."):
                raise ConfigEntryError(f"sources.{self.name}: `{package}` is not a package name")
            targets.append(
                Target(
                    source=self.name,
                    key=package,
                    label=fields.get("name"),
                    params={"tag": fields.get("tag")},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        # name keeps @ but slash escaped
        url = f"https://registry.npmjs.org/{quote(target.key, safe='@')}"
        fetched = client.get(url, conditional=True)
        if fetched is None:
            return []

        doc = fetched.json()
        latest = (doc.get("dist-tags") or {}).get("latest")
        only_tag = target.params.get("tag")
        if only_tag:
            wanted = (doc.get("dist-tags") or {}).get(only_tag)
            versions = [wanted] if wanted else []
        else:
            versions = [v for v in (doc.get("time") or {}) if v not in _NON_VERSION_KEYS]

        times = doc.get("time") or {}
        dated = [(v, _parse_iso(times.get(v))) for v in versions if v]
        dated.sort(key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        updates = []
        for version, published in dated[:MAX_VERSIONS]:
            meta = (doc.get("versions") or {}).get(version) or {}
            tags = ("release",) if version == latest else ()
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{target.key} {version}",
                    url=f"https://www.npmjs.com/package/{target.key}/v/{version}",
                    published=published,
                    version=version,
                    body=meta.get("description") or doc.get("description"),
                    tags=tags + (("latest",) if version == latest else ()),
                )
            )
        return updates


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
