"""Releases, tags, and commits from any git forge: GitHub, GitLab, Forgejo/Gitea.

GitHub is read from its ``.atom`` feeds, which need no credentials. A token switches releases to
the REST API, which has better release info. COMMONLY RATE LIMITED without one!
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Iterable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from ..http import Client, Fetched
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, parse_iso, require
from .feed import html_to_text, is_feed_url, parse_feed

VALID_WATCH = ("releases", "tags", "commits")
FORGES = ("github", "gitlab", "forgejo")
# Same API under another name
FORGE_ALIASES = {"gitea": "forgejo", "codeberg": "forgejo"}
KNOWN_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "codeberg.org": "forgejo",
    "gitea.com": "forgejo",
}
# A token is only ever sent to the host it was issued for. Others need `token_env:`
DEFAULT_TOKEN_ENV = {"github.com": "GITHUB_TOKEN", "gitlab.com": "GITLAB_TOKEN"}

PER_PAGE = 20
_REDIRECTS = (301, 302, 303, 307, 308)

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d+)?$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Prerelease markers
_PRERELEASE_RE = re.compile(
    r"(?:^|[-._+])(?:alpha|beta|rc|pre(?:view)?|dev|nightly|canary|snapshot|eap?|insiders?"
    r"|next|init|branchpoint)[\d._-]*(?:-[a-z])?$"
    r"|(?:^|[-._])(?:m|cr)\d+$"
    r"|\.\d+(?:a|b|rc)\d+$"
    r"|\.\d+(?:alpha|beta|rc|pre(?:view)?|dev)\d*$",
    re.IGNORECASE,
)
# Hostname hints for self-hosted instances: gitlab.gnome.org, gitea.example.org, github.corp.com
_HOST_HINTS = (
    ("gitlab", "gitlab"),
    ("forgejo", "forgejo"),
    ("gitea", "forgejo"),
    ("codeberg", "forgejo"),
    ("github", "github"),
)


def _is_prerelease(version: str | None) -> bool:
    if not version:
        return False
    version = version.strip()
    return any(_PRERELEASE_RE.search(part) for part in {version, version.split("+", 1)[0]})


def _guess_forge(host: str) -> str | None:
    name = host.split(":")[0]
    if name in KNOWN_HOSTS:
        return KNOWN_HOSTS[name]
    for hint, forge in _HOST_HINTS:
        if hint in name:
            return forge
    return None


def _split_location(
    text: str, host: str | None, source: str
) -> tuple[str, str, str, bool]:
    """``(scheme, host, path, addressed)`` from a URL, ``host/owner/repo``, or ``owner/repo``"""
    scheme = "https"
    wanted = host.strip().lower().removeprefix("www.") if host else None
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https"):
            raise ConfigEntryError(f"sources.{source}: `{text}` is not an http(s) URL")
        scheme, url_host, path = parts.scheme, parts.netloc.lower(), parts.path
    else:
        first, _, rest = text.partition("/")
        named = first.lower().removeprefix("www.")
        # . in first segment marks a host: `codeberg.org/forgejo/forgejo`. With `host:` set
        # it has to be that host, so a dotted GitLab group (`my.group/sub/project`) is a path.
        # A well-known host is still one, so a mismatch with `host:` is reported below
        if (
            "." in first
            and rest.count("/") >= 1
            and (wanted in (None, named) or named in KNOWN_HOSTS)
        ):
            url_host, path = first.lower(), rest
        else:
            url_host, path = None, text

    url_host = url_host.removeprefix("www.") if url_host else None
    if url_host and wanted and url_host != wanted:
        raise ConfigEntryError(
            f"sources.{source}: `{text}` is on {url_host}, but `host:` says {wanted}"
        )
    resolved = url_host or wanted or "github.com"
    if not _HOST_RE.match(resolved):
        raise ConfigEntryError(f"sources.{source}: `{resolved}` is not a hostname")
    return scheme, resolved, path, url_host is not None


def _clean_path(path: str, forge: str, value: Any, source: str, addressed: bool) -> str:
    path = path.strip("/")
    if forge == "gitlab":
        # /group/project/-/releases: everything from /-/ on is a page within the project
        path = path.split("/-/")[0].strip("/")
    segments = [s for s in path.split("/") if s]
    if forge != "gitlab" and addressed:
        # /owner/repo/releases/tag/v1: only the first two name the repo. A bare
        # `hashicorp/tap/terraform` is not an address, and cutting it would name another repo
        segments = segments[:2]
    if forge != "gitlab" and len(segments) > 2:
        raise ConfigEntryError(f"sources.{source}: `{value}` does not name an owner/repo")
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
    # Config keys that list entries for this source (`github` was its own source <= 0.3.1)
    aliases: ClassVar[tuple[str, ...]] = ("github",)

    def claim(self, value: str) -> str | None:
        """`owner/repo`, or a repo on a forge known by its host"""
        if value.startswith("@") or is_feed_url(value):
            return None
        return accepted(self, value)

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "repo", self.name)
            value = require(fields, "repo", self.name)
            scheme, host, raw_path, addressed = _split_location(
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

            path = _clean_path(raw_path, forge, value, self.name, addressed)
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

    def icon(self, target: Target, client: Client) -> str | None:
        """Owner's avatar. GitHub by name; others by repo's API"""
        params = target.params
        base, path, forge = params["base"], params["path"], params["forge"]
        if forge == "github":
            return f"{base}/{quote(path.split('/')[0], safe='')}.png?size=128"
        token_env = params.get("token_env")
        token = os.environ.get(token_env) if token_env else None
        if forge == "gitlab":
            api = f"{base}/api/v4/projects/{quote(path, safe='')}"
            headers = _api_headers(client, {"PRIVATE-TOKEN": token} if token else {})
        else:
            owner, repo = (quote(part, safe="") for part in path.split("/"))
            api = f"{base}/api/v1/repos/{owner}/{repo}"
            headers = _api_headers(client, {"Authorization": f"token {token}"} if token else {})
        fetched = client.get(api, headers=headers)
        if fetched is None:
            return None
        repo_info = fetched.json()
        owner_info = repo_info.get("namespace" if forge == "gitlab" else "owner") or {}
        avatar = owner_info.get("avatar_url") or repo_info.get("avatar_url")
        return urljoin(f"{base}/", avatar) if isinstance(avatar, str) and avatar else None


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


def _with_latest(updates: list[Update], latest: Update | None) -> list[Update]:
    """``updates`` and latest release, if other releases pushed it off"""
    if latest is None or any(u.uid == latest.uid for u in updates):
        return updates
    return [*updates, latest]

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
            headers = _github_api_headers(token)
            fetched = client.get(url, conditional=True, headers=headers)

            def api_page(number: int, _: list[Update]) -> tuple[list[Update], bool]:
                later = client.get(f"{url}&page={number}", headers=headers)
                return _github_releases_via_api(source, target, later), _api_complete(later)

            complete = _api_complete(fetched)
            releases = _github_releases_via_api(source, target, fetched)
            releases = _with_match(target, releases, fetched, complete, api_page)
            if _wants_latest(client, target, fetched, complete):
                latest = f"{api}/repos/{repo}/releases/latest"
                releases = _with_latest(
                    releases, _github_latest_via_api(source, target, client, latest, token)
                )
            updates.extend(releases)
        elif kind == "releases":
            feed = f"{base}/{repo}/releases.atom"
            fetched = client.get(feed, conditional=True)

            def atom_page(_: int, previous: list[Update]) -> tuple[list[Update], bool]:
                # The feed has no page numbers: it goes on from a tag
                if not previous:
                    return [], True
                after = quote(previous[-1].uid.removeprefix("release:"), safe="")
                later = client.get(f"{feed}?after={after}")
                page = _github_atom_updates(source, target, later, "release")
                return page, _atom_complete(later)

            complete = _atom_complete(fetched)
            releases = _github_atom_updates(source, target, fetched, "release")
            releases = _with_match(target, releases, fetched, complete, atom_page)
            if _wants_latest(client, target, fetched, complete):
                latest = f"{base}/{repo}/releases/latest"
                releases = _with_latest(releases, _github_latest(source, target, client, latest))
            updates.extend(releases)
        elif kind == "tags":
            updates.extend(_github_atom(source, target, client, f"{base}/{repo}/tags.atom", "tag"))
        elif kind == "commits":
            branch = params.get("branch")
            path = f"commits/{branch}.atom" if branch else "commits.atom"
            updates.extend(_github_atom(source, target, client, f"{base}/{repo}/{path}", "commit"))

    return updates


# `track_latest:` asks again at least this often, even while the list is unchanged
LATEST_EVERY = timedelta(hours=12)
LATEST_ASKED = "latest_asked"
# In place of a time: the list held every release, so the latest too
LATEST_LISTED = "listed"
# GitHub's .atom feeds have this many entries
ATOM_PAGE = 10
_ATOM_ENTRY = re.compile(rb"<entry[\s>]")


def _atom_complete(fetched: Fetched | None) -> bool:
    """If a GitHub .atom list holds every release: a page short of full"""
    return fetched is not None and len(_ATOM_ENTRY.findall(fetched.content)) < ATOM_PAGE


def _api_complete(fetched: Fetched | None) -> bool:
    """If a forge API list holds every release: short of a page, with no next one"""
    if fetched is None or 'rel="next"' in fetched.headers.get("link", ""):
        return False
    try:
        listed = fetched.json()
    except ValueError:
        return False
    return isinstance(listed, list) and len(listed) < PER_PAGE


def _wants_latest(
    client: Client, target: Target, fetched: Fetched | None, complete: bool = False
) -> bool:
    """If `track_latest:` asks for the latest release this check"""
    if not target.track_latest:
        return False
    state = client.state
    if complete:
        if state is not None and not client.test:
            state.cache_set(LATEST_ASKED, target.key, LATEST_LISTED)
        return False
    if client.test or state is None:
        return True
    now = datetime.now(timezone.utc)
    if fetched is None:  # 304
        asked = state.cache_get(LATEST_ASKED, target.key)
        if asked == LATEST_LISTED:
            return False
        try:
            if asked and now - datetime.fromisoformat(asked) < LATEST_EVERY:
                return False
        except ValueError:
            pass
    state.cache_set(LATEST_ASKED, target.key, now.isoformat())
    return True


# `versions: {match:}` reads more pages for a release up to this
MATCH_PAGES = 5


def _with_match(
    target: Target,
    releases: list[Update],
    fetched: Fetched | None,
    complete: bool,
    later: Callable[[int, list[Update]], tuple[list[Update], bool]],
) -> list[Update]:
    """``releases`` and the newest one `versions: {match:}` fits"""
    if not target.versions.match or fetched is None or complete:
        return releases
    if any(target.keeps(update) for update in releases):
        return releases
    page = releases
    for number in range(2, MATCH_PAGES + 2):
        page, last = later(number, page)
        found = next((update for update in page if target.keeps(update)), None)
        if found is not None:
            return [*releases, found]
        if last or not page:
            break
    return releases


def _github_atom(source: str, target: Target, client: Client, url: str, tag: str) -> list[Update]:
    return _github_atom_updates(source, target, client.get(url, conditional=True), tag)


def _github_atom_updates(
    source: str, target: Target, fetched: Fetched | None, tag: str
) -> list[Update]:
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


def _github_api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_releases_via_api(source: str, target: Target, fetched: Fetched | None) -> list[Update]:
    if fetched is None:
        return []
    return [
        update
        for release in fetched.json()
        if (update := _github_release(source, target, release)) is not None
    ]


def _github_latest_via_api(
    source: str, target: Target, client: Client, url: str, token: str
) -> Update | None:
    fetched = client.get(
        url, conditional=True, headers=_github_api_headers(token), soft_statuses=(404,)
    )
    # 404: no release yet
    if fetched is None or fetched.status == 404:
        return None
    return _github_release(source, target, fetched.json())


def _github_latest(source: str, target: Target, client: Client, url: str) -> Update | None:
    """The latest release by where /releases/latest redirects, without loading the page"""
    fetched = client.get(url, follow_redirects=False, soft_statuses=_REDIRECTS)
    if fetched is None:
        return None
    location = fetched.headers.get("location", "")
    # A repo with no release redirects to /releases
    match = re.search(r"/releases/tag/([^/?#]+)$", location)
    if not match:
        return None
    tag = unquote(match.group(1))
    return Update(
        source=source,
        target=target.key,
        uid=f"release:{tag}",
        title=tag,
        url=location,
        version=tag,
        tags=_versioned("release", tag),
    )


def _github_release(source: str, target: Target, release: dict[str, Any]) -> Update | None:
    if release.get("draft"):
        return None
    published = release.get("published_at") or release.get("created_at")
    legacy = f"release:{release['id']}"
    tag_name = release.get("tag_name")
    return Update(
        source=source,
        target=target.key,
        uid=f"release:{tag_name}" if tag_name else legacy,
        title=(release.get("name") or release.get("tag_name") or "").strip()
        or "(untitled)",
        url=release.get("html_url", ""),
        published=parse_iso(published),
        version=release.get("tag_name"),
        body=html_to_text(release.get("body")),
        tags=("release",) + (("prerelease",) if release.get("prerelease") else ()),
        # <= 0.3.1 keyed this path by release id
        aliases=(legacy,) if tag_name else (),
    )


def _with_release_uid(update: Update) -> Update:
    match = re.search(r"/releases/tag/(.+)$", update.url)
    if not match:
        return update
    # <= 0.3.1 keyed this path by the atom entry id
    return replace(update, uid=f"release:{unquote(match.group(1))}", aliases=(update.uid,))


def _version_from_entry(entry: Any) -> str | None:
    """Recover the tag from an atom entry id like ``tag:github.com,...:Repository/1/v3.13.0``.

    A tag without a number (`nightly`) is kept
    """
    for candidate in (entry.get("id", ""), entry.get("link", "")):
        match = re.search(r"[/:]([^/:]+)$", str(candidate))
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
            url = f"{api}/releases?per_page={PER_PAGE}"

            def listed(fetched: Fetched | None) -> list[Update]:
                return [
                    update
                    for release in (fetched.json() if fetched else [])
                    if (update := _gitlab_release(source, target, web, release)) is not None
                ]

            def page(number: int, _: list[Update]) -> tuple[list[Update], bool]:
                later = client.get(f"{url}&page={number}", headers=headers)
                return listed(later), _api_complete(later)

            fetched = client.get(url, conditional=True, headers=headers)
            complete = _api_complete(fetched)
            releases = _with_match(target, listed(fetched), fetched, complete, page)
            if _wants_latest(client, target, fetched, complete):
                # Redirects to the release itself
                latest = client.get(f"{api}/releases/permalink/latest", conditional=True,
                                    headers=headers, soft_statuses=(404,))
                if latest is not None and latest.status != 404:
                    releases = _with_latest(
                        releases, _gitlab_release(source, target, web, latest.json())
                    )
            updates.extend(releases)
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
                        published=parse_iso(
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
                        published=parse_iso(commit.get("committed_date")),
                        body=html_to_text(commit.get("message")),
                        tags=("commit",),
                    )
                )

    return updates

def _gitlab_release(
    source: str, target: Target, web: str, release: dict[str, Any]
) -> Update | None:
    tag = release.get("tag_name")
    if not tag:
        return None
    links = release.get("_links") or {}
    return Update(
        source=source,
        target=target.key,
        uid=f"release:{tag}",
        title=(release.get("name") or tag).strip() or tag,
        url=links.get("self") or f"{web}/-/releases/{quote(tag, safe='')}",
        published=parse_iso(release.get("released_at") or release.get("created_at")),
        version=tag,
        body=html_to_text(release.get("description")),
        tags=_versioned("release", tag),
    )

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
            url = f"{api}/releases?limit={PER_PAGE}"

            def listed(fetched: Fetched | None) -> list[Update]:
                return [
                    update
                    for release in (fetched.json() if fetched else [])
                    if (update := _forgejo_release(source, target, web, release)) is not None
                ]

            def page(number: int, _: list[Update]) -> tuple[list[Update], bool]:
                later = client.get(f"{url}&page={number}", headers=headers)
                return listed(later), _api_complete(later)

            fetched = client.get(url, conditional=True, headers=headers)
            complete = _api_complete(fetched)
            releases = _with_match(target, listed(fetched), fetched, complete, page)
            if _wants_latest(client, target, fetched, complete):
                latest = client.get(f"{api}/releases/latest", conditional=True,
                                    headers=headers, soft_statuses=(404,))
                if latest is not None and latest.status != 404:
                    releases = _with_latest(
                        releases, _forgejo_release(source, target, web, latest.json())
                    )
            updates.extend(releases)
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
                        published=parse_iso(commit.get("created")),
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
                        published=parse_iso(committer.get("date") or item.get("created")),
                        body=html_to_text(detail.get("message")),
                        tags=("commit",),
                    )
                )

    return updates


def _forgejo_release(
    source: str, target: Target, web: str, release: dict[str, Any]
) -> Update | None:
    tag = release.get("tag_name")
    if release.get("draft") or not tag:
        return None
    return Update(
        source=source,
        target=target.key,
        uid=f"release:{tag}",
        title=(release.get("name") or tag).strip() or tag,
        url=release.get("html_url") or f"{web}/releases/tag/{quote(tag, safe='')}",
        published=parse_iso(release.get("published_at") or release.get("created_at")),
        version=tag,
        body=html_to_text(release.get("body")),
        tags=("release",) + (("prerelease",) if release.get("prerelease") else ()),
    )
