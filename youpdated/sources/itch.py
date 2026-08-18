"""itch.io devlogs and builds.

Two watched per game:
- devlogs (``<game-url>/devlog.rss``): itch answers 404 when a game doesn't have one, which is normal and not an error
- releases: many games ship new builds without a devlog, so 404 above moves here. The game page has "Updated" timestamps 
  and the list of downloadable files; they fingerprint the current build, and a change means a new release.

Both are on by default. ``watch: [devlog]`` or ``watch: [releases]`` to choose
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import parse_feed

GAME_URL_RE = re.compile(
    r"^(?:https?://)?(?P<user>[A-Za-z0-9-]+)\.itch\.io/(?P<game>[A-Za-z0-9._-]+)/?$"
)

# info panel: <tr><td>Updated</td><td><abbr title="17 August 2026 @ 10:34 UTC">…
INFO_ROW_RE = re.compile(r"<td>([^<]{1,30})</td><td>(.{0,400}?)</td>", re.S)
ABBR_TITLE_RE = re.compile(r'<abbr title="([^"]+)"')
UPLOAD_NAME_RE = re.compile(r'<strong title="([^"]*)" class="name">')
UPLOAD_SIZE_RE = re.compile(r'class="file_size"><span>([^<]*)</span>')
ITCH_DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})(?:\s*@\s*(\d{1,2}):(\d{2}))?", re.I
)

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

VALID_WATCH = ("devlog", "releases")


@register
class ItchSource:
    name: ClassVar[str] = "itch"
    summary: ClassVar[str] = "Devlog posts and new builds for an itch.io game"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "url", self.name)

            if fields.get("user") and fields.get("game"):
                user, game = str(fields["user"]).strip(), str(fields["game"]).strip()
            else:
                raw = str(require(fields, "url", self.name)).strip()
                match = GAME_URL_RE.match(raw)
                if not match:
                    raise ConfigEntryError(
                        f"sources.{self.name}: `{raw}` is not an itch.io game URL "
                        "(expected https://user.itch.io/game)"
                    )
                user, game = match.group("user"), match.group("game")

            watch = fields.get("watch") or list(VALID_WATCH)
            if isinstance(watch, str):
                watch = [watch]
            unknown = [w for w in watch if w not in VALID_WATCH]
            if unknown:
                raise ConfigEntryError(
                    f"sources.{self.name}: unknown watch {unknown} for {user}/{game}; "
                    f"valid options are {list(VALID_WATCH)}"
                )

            targets.append(
                Target(
                    source=self.name,
                    key=f"{user}/{game}",
                    label=fields.get("name") or game.replace("-", " "),
                    params={
                        "page": f"https://{user}.itch.io/{game}",
                        "watch": list(watch),
                    },
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        watch = target.params.get("watch") or list(VALID_WATCH)
        updates: list[Update] = []
        if "devlog" in watch:
            updates.extend(self._devlog(target, client))
        if "releases" in watch:
            updates.extend(self._release(target, client))
        return updates

    # devlog feed

    def _devlog(self, target: Target, client: Client) -> list[Update]:
        url = f"{target.params['page']}/devlog.rss"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            # No devlog has ever been posted for this game
            return []

        return parse_feed(
            fetched.content,
            source=self.name,
            target=target.key,
            tags=("devlog",),
        )

    # build fingerprint from the game page

    def _release(self, target: Target, client: Client) -> list[Update]:
        page = target.params["page"]
        fetched = client.get(page, conditional=True, soft_statuses=(404,))
        if fetched is None or fetched.status == 404:
            return []

        html = fetched.text
        info = self._info_rows(html)
        uploads = self._uploads(html)
        updated = _parse_itch_date(info.get("Updated") or info.get("Published"))
        version = info.get("Version")

        # Nothing observable on page
        if not uploads and updated is None and not version:
            return []

        # Any change to the build produces a different uid
        fingerprint = "|".join(
            [
                updated.isoformat() if updated else "",
                version or "",
                *(f"{name}:{size}" for name, size in uploads),
            ]
        )
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

        if version:
            title = f"New build — {version}"
        elif uploads:
            title = f"New build — {len(uploads)} file(s)"
        else:
            title = "Game page updated"

        body = ", ".join(f"{name} ({size})" for name, size in uploads) or None

        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"build:{digest}",
                title=title,
                url=page,
                published=updated,
                version=version,
                body=body,
                tags=("release",),
            )
        ]

    def _info_rows(self, html: str) -> dict[str, str]:
        """Pull "More information" table into a plain dict.

        Values keep their timestamp when supplied. (visible text is relative ("20 hours ago") fyi)
        """
        rows: dict[str, str] = {}
        for key, cell in INFO_ROW_RE.findall(html):
            key = key.strip()
            if key in rows:
                continue
            abbr = ABBR_TITLE_RE.search(cell)
            value = abbr.group(1) if abbr else re.sub(r"<[^>]+>", "", cell).strip()
            if value:
                rows[key] = value
        return rows

    def _uploads(self, html: str) -> list[tuple[str, str]]:
        names = UPLOAD_NAME_RE.findall(html)
        sizes = UPLOAD_SIZE_RE.findall(html)
        # Sizes can be missing for some entries
        return [(n, sizes[i] if i < len(sizes) else "") for i, n in enumerate(names)]


def _parse_itch_date(value: str | None) -> datetime | None:
    """Parse itch's "17 August 2026 @ 10:34 UTC" (time optional)."""
    if not value:
        return None
    match = ITCH_DATE_RE.search(value)
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = MONTHS.get(month_name.lower())
    if month is None:
        return None
    return datetime(
        int(year), month, int(day), int(hour or 0), int(minute or 0), tzinfo=timezone.utc
    )
