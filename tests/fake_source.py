"""source for api tests

$YOUPDATED_FAKE_ITEMS for the file, ``{"<entry>": [{"uid": "1.0", ...}], ...}``, read on every fetch
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, ClassVar, Iterable

from youpdated import registry
from youpdated.http import Client
from youpdated.models import Target, Update
from youpdated.sources.base import entry_fields, parse_iso

ITEMS_ENV = "YOUPDATED_FAKE_ITEMS"


class FakeSource:
    name: ClassVar[str] = "fake"
    summary: ClassVar[str] = "Items from a file, for tests"

    def claim(self, value: str) -> str | None:
        return value.removeprefix("fake:") if value.startswith("fake:") else None

    def targets(self, entries: list[Any]) -> list[Target]:
        return [
            Target(source=self.name, key=str(entry_fields(entry, "name", self.name)["name"]))
            for entry in entries
        ]

    def icon(self, target: Target, client: Client) -> str | list[str] | None:
        return _read().get("_icon", {}).get(target.key)

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        data = _read()
        label = data.get("_labels", {}).get(target.key)
        if label:
            target.label = label
        items = data.get(target.key, [])
        if items == "fail":
            raise RuntimeError(f"{target.key} is down")
        if isinstance(items, dict):
            time.sleep(items.get("sleep", 0))
            items = items.get("items", [])
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=item["uid"],
                title=item.get("title", f"{target.key} {item['uid']}"),
                url=item.get("url", f"https://example.com/{target.key}/{item['uid']}"),
                published=parse_iso(item.get("published")),
                version=item.get("version", item["uid"]),
                tags=tuple(item.get("tags", ("release",))),
            )
            for item in items
        ]


def _read() -> dict[str, Any]:
    return json.loads(Path(os.environ[ITEMS_ENV]).read_text(encoding="utf-8"))


def register() -> None:
    registry.register(FakeSource)


def unregister() -> None:
    registry._SOURCES.pop(FakeSource.name, None)


def write_items(path: Path, items: dict[str, Any]) -> None:
    path.write_text(json.dumps(items), encoding="utf-8")
