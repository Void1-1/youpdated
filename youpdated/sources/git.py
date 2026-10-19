"""Releases, tags, and commits from any git forge: GitHub, GitLab, Forgejo/Gitea.

GitHub is read from its ``.atom`` feeds, which need no credentials. A token switches releases to
the REST API, which has better release info. COMMONLY RATE LIMITED without one!
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable
from urllib.parse import quote, unquote, urlsplit

import httpx

from ..http import Client
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import html_to_text, parse_feed

VALID_WATCH = ("releases", "tags", "commits")
FORGES = ("github", "gitlab", "forgejo")
#: Same API under another name
FORGE_ALIASES = {"gitea": "forgejo", "codeberg": "forgejo"}
KNOWN_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "codeberg.org": "forgejo",
    "gitea.com": "forgejo",
}
#: A token is only ever sent to the host it was issued for. Others need `token_env:`
DEFAULT_TOKEN_ENV = {"github.com": "GITHUB_TOKEN", "gitlab.com": "GITLAB_TOKEN"}

PER_PAGE = 20

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d+)?$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: Prerelease markers
_PRERELEASE_RE = re.compile(
    r"(?:^|[-._+])(?:alpha|beta|rc|pre(?:view)?|dev|nightly|canary|snapshot|eap|insiders?)"
    r"[\d._-]*$"
    r"|\d(?:a|b|rc)\d+$",
    re.IGNORECASE,
)
#: Hostname hints for self-hosted instances: gitlab.gnome.org, gitea.example.org, github.corp.com
_HOST_HINTS = (
    ("gitlab", "gitlab"),
    ("forgejo", "forgejo"),
    ("gitea", "forgejo"),
    ("codeberg", "forgejo"),
    ("github", "github"),
)


def _is_prerelease(version: str | None) -> bool:
    return bool(version and _PRERELEASE_RE.search(version.strip()))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _guess_forge(host: str) -> str | None:
    name = host.split(":")[0]
    if name in KNOWN_HOSTS:
        return KNOWN_HOSTS[name]
    for hint, forge in _HOST_HINTS:
        if hint in name:
            return forge
    return None


def _split_location(text: str, host: str | None, source: str) -> tuple[str, str, str]:
    """``(scheme, host, path)`` from a URL, ``host/owner/repo``, or ``owner/repo``"""
    scheme = "https"
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https"):
            raise ConfigEntryError(f"sources.{source}: `{text}` is not an http(s) URL")
        scheme, url_host, path = parts.scheme, parts.netloc.lower(), parts.path
    else:
        first, _, rest = text.partition("/")
        # . in first segment marks a host: `codeberg.org/forgejo/forgejo`
        if "." in first and rest.count("/") >= 1:
            url_host, path = first.lower(), rest
        else:
            url_host, path = None, text

    url_host = url_host.removeprefix("www.") if url_host else None
    wanted = host.strip().lower().removeprefix("www.") if host else None
    if url_host and wanted and url_host != wanted:
        raise ConfigEntryError(
            f"sources.{source}: `{text}` is on {url_host}, but `host:` says {wanted}"
        )
    resolved = url_host or wanted or "github.com"
    if not _HOST_RE.match(resolved):
        raise ConfigEntryError(f"sources.{source}: `{resolved}` is not a hostname")
    return scheme, resolved, path


def _clean_path(path: str, forge: str, value: Any, source: str) -> str:
    path = path.strip("/")
    if forge == "gitlab":
        # /group/project/-/releases: everything from /-/ on is a page within the project
        path = path.split("/-/")[0].strip("/")
    segments = [s for s in path.split("/") if s]
    if forge != "gitlab":
        # /owner/repo/releases/tag/v1: only the first two name the repo
        segments = segments[:2]
    if segments:
        segments[-1] = segments[-1].removesuffix(".git")
    if len(segments) < 2 or not all(_SEGMENT_RE.match(s) for s in segments):
        shape = "group/project" if forge == "gitlab" else "owner/repo"
        raise ConfigEntryError(f"sources.{source}: `{value}` does not name a {shape}")
    return "/".join(segments)


@register
class GitSource:
    name: ClassVar[str] = "git"
    summary: ClassVar[str] = "Releases, tags, and commits on GitHub, GitLab, or Forgejo/Gitea"
    #: Config keys that list entries for this source (`github` was its own source <= 0.3.1)
    aliases: ClassVar[tuple[str, ...]] = ("github",)

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "repo", self.name)
            value = require(fields, "repo", self.name)
            scheme, host, raw_path = _split_location(
                str(value).strip(), fields.get("host"), self.name
            )

            forge = fields.get("forge")
            if forge is not None:
                forge = str(forge).strip().lower()
                forge = FORGE_ALIASES.get(forge, forge)
                if forge not in FORGES:
                    raise ConfigEntryError(
                        f"sources.{self.name}: unknown forge `{fields['forge']}`; "
                        f"valid options are {list(FORGES)} (gitea is forgejo)"
                    )
            else:
                forge = _guess_forge(host)
                if forge is None:
                    raise ConfigEntryError(
                        f"sources.{self.name}: can't tell which forge {host} runs; "
                        f"add `forge:` ({' | '.join(FORGES)})"
                    )

            path = _clean_path(raw_path, forge, value, self.name)
            key = f"{host}/{path}"

            watch = fields.get("watch") or ["releases"]
            if isinstance(watch, str):
                watch = [watch]
            unknown = [w for w in watch if w not in VALID_WATCH]
            if unknown:
                raise ConfigEntryError(
                    f"sources.{self.name}: unknown watch {unknown} for {key}; "
                    f"valid options are {list(VALID_WATCH)}"
                )

            token_env = fields.get("token_env")
            if token_env is not None and not (
                isinstance(token_env, str) and _ENV_RE.match(token_env)
            ):
                raise ConfigEntryError(
                    f"sources.{self.name}: `token_env` for {key} must be an environment "
                    "variable name, not the token"
                )

            targets.append(
                Target(
                    source=self.name,
                    key=key,
                    label=fields.get("name"),
                    params={
                        "forge": forge,
                        "base": f"{scheme}://{host}",
                        "path": path,
                        "watch": list(watch),
                        "branch": fields.get("branch"),
                        "token_env": token_env or DEFAULT_TOKEN_ENV.get(host),
                    },
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        params = target.params
        token_env = params.get("token_env")
        token = os.environ.get(token_env) if token_env else None
        forge = params["forge"]

        if forge == "github":
            return _github(self.name, target, client, token)
        if forge == "gitlab":
            return _gitlab(self.name, target, client, token)
        return _forgejo(self.name, target, client, token)


def _versioned(kind: str, version: str | None) -> tuple[str, ...]:
    """tags for a release or tag"""
    return (kind,) + (("prerelease",) if _is_prerelease(version) else ())


def _api_headers(client: Client, headers: dict[str, str]) -> dict[str, str]:
    if client.privacy.user_agent == "rotate":
        # Forges sit behind scraper walls (Codeberg's, Anubis on gitlab.gnome.org) that prevent browser user agents
        headers = {**headers, "User-Agent": f"python-httpx/{httpx.__version__}"}
    return headers


def _first_line(message: str | None) -> str:
    lines = (message or "").strip().splitlines()
    return lines[0].strip() if lines else ""

# GitHub: anonymous .atom feeds, or the REST API for releases with a token

def _github(source: str, target: Target, client: Client, token: str | None) -> list[Update]:
    params = target.params
    base, repo = params["base"], params["path"]
    # Enterprise serves its API under /api/v3 on the same host
    api = "https://api.github.com" if base == "https://github.com" else f"{base}/api/v3"
    updates: list[Update] = []

    for kind in params.get("watch") or ["releases"]:
        if kind == "releases" and token:
            url = f"{api}/repos/{repo}/releases?per_page={PER_PAGE}"
            updates.extend(_github_releases_via_api(source, target, client, url, token))
        elif kind == "releases":
            url = f"{base}/{repo}/releases.atom"
            updates.extend(_github_atom(source, target, client, url, "release"))
        elif kind == "tags":
            updates.extend(_github_atom(source, target, client, f"{base}/{repo}/tags.atom", "tag"))
        elif kind == "commits":
            branch = params.get("branch")
            path = f"commits/{branch}.atom" if branch else "commits.atom"
            updates.extend(_github_atom(source, target, client, f"{base}/{repo}/{path}", "commit"))

    return updates


def _github_atom(source: str, target: Target, client: Client, url: str, tag: str) -> list[Update]:
    fetched = client.get(url, conditional=True)
    if fetched is None:  # 304 Not Modified, or test
        return []
    updates = parse_feed(
        fetched.content,
        source=source,
        target=target.key,
        version_of=_version_from_entry if tag != "commit" else None,
        tags=(tag,),
    )
    if tag == "commit":
        return updates
    if tag == "release":
        updates = [_with_release_uid(update) for update in updates]
    # match the authenticated path, which gets `prerelease` from the api
    return [
        replace(update, tags=update.tags + ("prerelease",))
        if _is_prerelease(update.version)
        else update
        for update in updates
    ]


def _github_releases_via_api(
    source: str, target: Target, client: Client, url: str, token: str
) -> list[Update]:
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
        legacy = f"release:{release['id']}"
        tag_name = release.get("tag_name")
        updates.append(
            Update(
                source=source,
                target=target.key,
                # Keyed by tag so the atom path, which has no release id, agrees
                uid=f"release:{tag_name}" if tag_name else legacy,
                title=(release.get("name") or release.get("tag_name") or "").strip()
                or "(untitled)",
                url=release.get("html_url", ""),
                published=_parse_iso(published),
                version=release.get("tag_name"),
                body=html_to_text(release.get("body")),
                tags=("release",) + (("prerelease",) if release.get("prerelease") else ()),
                # <= 0.3.1 keyed this path by release id
                aliases=(legacy,) if tag_name else (),
            )
        )
    return updates


def _with_release_uid(update: Update) -> Update:
    match = re.search(r"/releases/tag/(.+)$", update.url)
    if not match:
        return update
    # <= 0.3.1 keyed this path by the atom entry id
    return replace(update, uid=f"release:{unquote(match.group(1))}", aliases=(update.uid,))


def _version_from_entry(entry: Any) -> str | None:
    """Recover the tag from an atom entry id like ``tag:github.com,...:Repository/1/v3.13.0``."""
    for candidate in (entry.get("id", ""), entry.get("link", "")):
        match = re.search(r"[/:]([^/:]*\d[^/:]*)$", str(candidate))
        if match:
            return match.group(1)
    return None

# GitLab: /api/v4, nested groups allowed

def _gitlab(source: str, target: Target, client: Client, token: str | None) -> list[Update]:
    params = target.params
    base, path = params["base"], params["path"]
    api = f"{base}/api/v4/projects/{quote(path, safe='')}"
    web = f"{base}/{path}"
    headers = _api_headers(client, {"PRIVATE-TOKEN": token} if token else {})
    updates: list[Update] = []

    for kind in params.get("watch") or ["releases"]:
        if kind == "releases":
            fetched = client.get(f"{api}/releases?per_page={PER_PAGE}", conditional=True,
                                 headers=headers)
            for release in fetched.json() if fetched else []:
                tag = release.get("tag_name")
                if not tag:
                    continue
                links = release.get("_links") or {}
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"release:{tag}",
                        title=(release.get("name") or tag).strip() or tag,
                        url=links.get("self") or f"{web}/-/releases/{quote(tag, safe='')}",
                        published=_parse_iso(
                            release.get("released_at") or release.get("created_at")
                        ),
                        version=tag,
                        body=html_to_text(release.get("description")),
                        tags=_versioned("release", tag),
                    )
                )
        elif kind == "tags":
            fetched = client.get(f"{api}/repository/tags?per_page={PER_PAGE}",
                                 conditional=True, headers=headers)
            for tag in fetched.json() if fetched else []:
                name = tag.get("name")
                if not name:
                    continue
                commit = tag.get("commit") or {}
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"tag:{name}",
                        title=name,
                        url=f"{web}/-/tags/{quote(name, safe='')}",
                        # created_at is the tag's own date
                        published=_parse_iso(
                            tag.get("created_at") or commit.get("committed_date")
                        ),
                        version=name,
                        body=html_to_text(
                            tag.get("message") or (tag.get("release") or {}).get("description")
                        ),
                        tags=_versioned("tag", name),
                    )
                )
        elif kind == "commits":
            url = f"{api}/repository/commits?per_page={PER_PAGE}"
            if params.get("branch"):
                url += f"&ref_name={quote(str(params['branch']), safe='')}"
            fetched = client.get(url, conditional=True, headers=headers)
            for commit in fetched.json() if fetched else []:
                sha = commit.get("id")
                if not sha:
                    continue
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"commit:{sha}",
                        title=commit.get("title") or _first_line(commit.get("message"))
                        or sha[:12],
                        url=commit.get("web_url") or f"{web}/-/commit/{sha}",
                        published=_parse_iso(commit.get("committed_date")),
                        body=html_to_text(commit.get("message")),
                        tags=("commit",),
                    )
                )

    return updates

# Forgejo and Gitea: /api/v1, the same on every instance

def _forgejo(source: str, target: Target, client: Client, token: str | None) -> list[Update]:
    params = target.params
    base, path = params["base"], params["path"]
    owner, repo = (quote(part, safe="") for part in path.split("/"))
    api = f"{base}/api/v1/repos/{owner}/{repo}"
    web = f"{base}/{path}"
    headers = _api_headers(client, {"Authorization": f"token {token}"} if token else {})
    updates: list[Update] = []

    for kind in params.get("watch") or ["releases"]:
        if kind == "releases":
            fetched = client.get(f"{api}/releases?limit={PER_PAGE}", conditional=True,
                                 headers=headers)
            for release in fetched.json() if fetched else []:
                tag = release.get("tag_name")
                if release.get("draft") or not tag:
                    continue
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"release:{tag}",
                        title=(release.get("name") or tag).strip() or tag,
                        url=release.get("html_url") or f"{web}/releases/tag/{quote(tag, safe='')}",
                        published=_parse_iso(
                            release.get("published_at") or release.get("created_at")
                        ),
                        version=tag,
                        body=html_to_text(release.get("body")),
                        tags=("release",)
                        + (("prerelease",) if release.get("prerelease") else ()),
                    )
                )
        elif kind == "tags":
            fetched = client.get(f"{api}/tags?limit={PER_PAGE}", conditional=True,
                                 headers=headers)
            for tag in fetched.json() if fetched else []:
                name = tag.get("name")
                if not name:
                    continue
                commit = tag.get("commit") or {}
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"tag:{name}",
                        title=name,
                        url=f"{web}/src/tag/{quote(name, safe='')}",
                        # `created` is absent before Gitea 1.15
                        published=_parse_iso(commit.get("created")),
                        version=name,
                        body=html_to_text(tag.get("message")),
                        tags=_versioned("tag", name),
                    )
                )
        elif kind == "commits":
            url = f"{api}/commits?limit={PER_PAGE}&stat=false&verification=false&files=false"
            if params.get("branch"):
                url += f"&sha={quote(str(params['branch']), safe='')}"
            fetched = client.get(url, conditional=True, headers=headers)
            for item in fetched.json() if fetched else []:
                sha = item.get("sha")
                if not sha:
                    continue
                detail = item.get("commit") or {}
                committer = detail.get("committer") or {}
                updates.append(
                    Update(
                        source=source,
                        target=target.key,
                        uid=f"commit:{sha}",
                        title=_first_line(detail.get("message")) or sha[:12],
                        url=item.get("html_url") or f"{web}/commit/{sha}",
                        published=_parse_iso(committer.get("date") or item.get("created")),
                        body=html_to_text(detail.get("message")),
                        tags=("commit",),
                    )
                )

    return updates
