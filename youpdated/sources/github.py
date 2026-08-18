"""GitHub releases, tags, and commits.

``.atom`` endpoints don't need credentials and no rate-limit (default here)
``GITHUB_TOKEN`` in the environment switches to the REST API, which has better release info

COMMONLY RATE LIMITED!
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import html_to_text, parse_feed

REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
VALID_WATCH = ("releases", "tags", "commits")


def _normalize_repo(value: Any, source: str) -> str:
    text = str(value).strip().rstrip("/")
    # Accept a full URL as well as owner/repo.
    match = re.search(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", text)
    if match:
        text = match.group(1)
    if not REPO_RE.match(text):
        raise ConfigEntryError(
            f"sources.{source}: `{value}` is not an owner/repo or a github.com URL"
        )
    return text


@register
class GitHubSource:
    name: ClassVar[str] = "github"
    summary: ClassVar[str] = "Releases, tags, and commits for a repository"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "repo", self.name)
            repo = _normalize_repo(require(fields, "repo", self.name), self.name)

            watch = fields.get("watch") or ["releases"]
            if isinstance(watch, str):
                watch = [watch]
            unknown = [w for w in watch if w not in VALID_WATCH]
            if unknown:
                raise ConfigEntryError(
                    f"sources.{self.name}: unknown watch {unknown} for {repo}; "
                    f"valid options are {list(VALID_WATCH)}"
                )

            targets.append(
                Target(
                    source=self.name,
                    key=repo,
                    label=fields.get("name"),
                    params={"watch": list(watch), "branch": fields.get("branch")},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        watch = target.params.get("watch") or ["releases"]
        token = os.environ.get("GITHUB_TOKEN")
        updates: list[Update] = []

        for kind in watch:
            if kind == "releases" and token:
                updates.extend(self._releases_via_api(target, client, token))
            elif kind == "releases":
                updates.extend(self._atom(target, client, "releases.atom", "release"))
            elif kind == "tags":
                updates.extend(self._atom(target, client, "tags.atom", "tag"))
            elif kind == "commits":
                branch = target.params.get("branch")
                path = f"commits/{branch}.atom" if branch else "commits.atom"
                updates.extend(self._atom(target, client, path, "commit"))

        return updates

    # anon path

    def _atom(self, target: Target, client: Client, path: str, tag: str) -> list[Update]:
        url = f"https://github.com/{target.key}/{path}"
        fetched = client.get(url, conditional=True)
        if fetched is None:  # 304 Not Modified, or test run
            return []
        return parse_feed(
            fetched.content,
            source=self.name,
            target=target.key,
            version_of=_version_from_entry if tag != "commit" else None,
            tags=(tag,),
        )

    # authed path

    def _releases_via_api(self, target: Target, client: Client, token: str) -> list[Update]:
        url = f"https://api.github.com/repos/{target.key}/releases?per_page=20"
        fetched = client.get(
            url,
            conditional=True,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if fetched is None:
            return []

        updates = []
        for release in fetched.json():
            if release.get("draft"):
                continue
            published = release.get("published_at") or release.get("created_at")
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"release:{release['id']}",
                    title=(release.get("name") or release.get("tag_name") or "").strip()
                    or "(untitled)",
                    url=release.get("html_url", ""),
                    published=_parse_iso(published),
                    version=release.get("tag_name"),
                    body=html_to_text(release.get("body")),
                    tags=("release",) + (("prerelease",) if release.get("prerelease") else ()),
                )
            )
        return updates


def _version_from_entry(entry: Any) -> str | None:
    """Recover the tag from an atom entry id like ``tag:github.com,...:Repository/1/v3.13.0``."""
    for candidate in (entry.get("id", ""), entry.get("link", "")):
        match = re.search(r"[/:]([^/:]*\d[^/:]*)$", str(candidate))
        if match:
            return match.group(1)
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
