"""Browser releases: Chrome, Brave, Firefox, and Edge.

Each publishes version history public and unauthenticated;
This normalizes the four into one Update, and the platform names for the user
(``mac`` each translates custom)

    sources:
      browser:
        - chrome
        - brave
        - browser: chrome
          platform: windows
          channel: beta
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields, require
from .feed import html_to_text, parse_feed

MAX_VERSIONS = 10
VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)+)")

# user input -> vendor name
PLATFORM_ALIASES = {
    "mac": "mac", "macos": "mac", "osx": "mac", "darwin": "mac",
    "win": "windows", "windows": "windows", "win64": "windows", "win32": "windows",
    "linux": "linux",
    "android": "android",
    "ios": "ios",
}

CHROME_PLATFORMS = {
    "mac": "mac", "windows": "win64", "linux": "linux",
    "android": "android", "ios": "ios",
}
CHROME_CHANNELS = ("stable", "beta", "dev", "canary", "extended")

EDGE_PLATFORMS = {
    "mac": "MacOS", "windows": "Windows", "linux": "Linux",
    "android": "Android", "ios": "iOS",
}
EDGE_CHANNELS = {"stable": "Stable", "beta": "Beta", "dev": "Dev", "canary": "Canary"}
# Microsoft only publishes release notes for stable and beta. (Again, who uses EDGE?)
EDGE_RELNOTES = {
    "stable": "https://learn.microsoft.com/deployedge/microsoft-edge-relnote-stable-channel",
    "beta": "https://learn.microsoft.com/deployedge/microsoft-edge-relnote-beta-channel",
}
EDGE_RELNOTE_FALLBACK = "https://www.microsoft.com/edge/download/insider"

# Brave publishes every channel into one repo feed, distinguished by title prefix ("Release v1.95.79", "Beta v1.94.112", "Nightly v1.96.0")
BRAVE_CHANNELS = {
    "stable": "Release",
    "release": "Release",
    "beta": "Beta",
    "nightly": "Nightly",
    "dev": "Dev",
}

# Mozilla publishes one document
FIREFOX_CHANNELS = {
    "stable": ("LATEST_FIREFOX_VERSION", "LAST_RELEASE_DATE"),
    "release": ("LATEST_FIREFOX_VERSION", "LAST_RELEASE_DATE"),
    "beta": ("LATEST_FIREFOX_DEVEL_VERSION", None),
    "dev": ("FIREFOX_DEVEDITION", None),
    "nightly": ("FIREFOX_NIGHTLY", None),
    "esr": ("FIREFOX_ESR", None),
}

BROWSERS = ("chrome", "brave", "firefox", "edge")

def _host_platform() -> str:
    if sys.platform.startswith("darwin"):
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


@register
class BrowserSource:
    name: ClassVar[str] = "browser"
    summary: ClassVar[str] = "New releases of Chrome, Brave, Firefox, or Edge"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "browser", self.name)
            browser = str(require(fields, "browser", self.name)).strip().lower()
            if browser not in BROWSERS:
                raise ConfigEntryError(
                    f"sources.{self.name}: unknown browser `{browser}`; "
                    f"valid options are {list(BROWSERS)}"
                )

            raw_platform = str(fields.get("platform") or _host_platform()).lower()
            platform = PLATFORM_ALIASES.get(raw_platform)
            if platform is None:
                raise ConfigEntryError(
                    f"sources.{self.name}: unknown platform `{raw_platform}`; "
                    f"valid options are {sorted(set(PLATFORM_ALIASES.values()))}"
                )

            channel = str(fields.get("channel") or "stable").lower()
            self._validate_channel(browser, channel)

            # Brave publishes one feed for every platform, so the platform is not part of identity but channel is.
            key = (
                f"brave/{channel}"
                if browser == "brave"
                else f"{browser}/{platform}/{channel}"
            )
            targets.append(
                Target(
                    source=self.name,
                    key=key,
                    label=fields.get("name") or _pretty(browser, platform, channel),
                    params={"browser": browser, "platform": platform, "channel": channel},
                )
            )
        return targets

    def _validate_channel(self, browser: str, channel: str) -> None:
        valid = {
            "chrome": CHROME_CHANNELS,
            "edge": tuple(EDGE_CHANNELS),
            "firefox": tuple(FIREFOX_CHANNELS),
            "brave": tuple(BRAVE_CHANNELS),
        }[browser]
        if channel not in valid:
            raise ConfigEntryError(
                f"sources.{self.name}: `{channel}` is not a {browser} channel; "
                f"valid options are {list(valid)}"
            )

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        browser = target.params["browser"]
        return {
            "chrome": self._chrome,
            "brave": self._brave,
            "firefox": self._firefox,
            "edge": self._edge,
        }[browser](target, client)

    # Chrome

    def _chrome(self, target: Target, client: Client) -> list[Update]:
        platform = CHROME_PLATFORMS[target.params["platform"]]
        channel = target.params["channel"]
        url = (
            "https://versionhistory.googleapis.com/v1/chrome/platforms/"
            f"{platform}/channels/{channel}/versions/all/releases"
        )
        fetched = client.get(url, conditional=True)
        if fetched is None:
            return []

        # One version can appear several times as its rollout fraction grows: keeps the earliest start time per version.
        starts: dict[str, datetime] = {}
        for release in fetched.json().get("releases", []):
            version = release.get("version")
            started = _parse_iso((release.get("serving") or {}).get("startTime"))
            if not version:
                continue
            if version not in starts or (started and started < starts[version]):
                starts[version] = started

        ordered = sorted(
            starts.items(), key=lambda kv: kv[1] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"chrome:{platform}:{channel}:{version}",
                title=f"Chrome {version}",
                url="https://chromereleases.googleblog.com/",
                published=started,
                version=version,
                body=f"{channel} channel on {target.params['platform']}",
                tags=("release", channel),
            )
            for version, started in ordered[:MAX_VERSIONS]
        ]

    # Brave

    def _brave(self, target: Target, client: Client) -> list[Update]:
        """Brave ships all channels as GitHub releases in one repo.

        The ``.atom`` feed only carries the latest 10, and Brave has nightlies
        The REST API is primary; the atom as fallback for rate-limits.
        """
        channel = target.params["channel"]
        prefix = BRAVE_CHANNELS[channel].lower()

        try:
            fetched = client.get(
                "https://api.github.com/repos/brave/brave-browser/releases?per_page=100",
                conditional=True,
                headers={"Accept": "application/vnd.github+json"},
                soft_statuses=(403, 429),
            )
        except FetchError as exc:
            client.note(f"brave: GitHub API failed ({exc}), falling back to the atom feed")
            return self._brave_from_atom(target, client, channel, prefix)

        if fetched is None:
            return []  # 304: nothing changed
        if fetched.status == 200:
            return self._brave_from_api(target, fetched.json(), channel, prefix)

        client.note(
            f"brave: GitHub API returned {fetched.status}, falling back to the atom feed"
        )
        return self._brave_from_atom(target, client, channel, prefix)

    def _brave_from_api(
        self, target: Target, releases: list[dict], channel: str, prefix: str
    ) -> list[Update]:
        updates = []
        for release in releases:
            if release.get("draft"):
                continue
            # Release names can carry trailing whitespace and newlines
            name = (release.get("name") or release.get("tag_name") or "").strip()
            if not name.lower().startswith(prefix):
                continue
            tag = release.get("tag_name") or name
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"brave:{tag}",
                    title=name,
                    url=release.get("html_url", ""),
                    published=_parse_iso(release.get("published_at")),
                    version=_version_from_title(tag) or _version_from_title(name),
                    body=html_to_text(release.get("body")),
                    tags=("release", channel),
                )
            )
            if len(updates) >= MAX_VERSIONS:
                break
        return updates

    def _brave_from_atom(
        self, target: Target, client: Client, channel: str, prefix: str
    ) -> list[Update]:
        fetched = client.get("https://github.com/brave/brave-browser/releases.atom")
        if fetched is None:
            return []
        updates = parse_feed(
            fetched.content,
            source=self.name,
            target=target.key,
            limit=None,  # filtered below, don't truncate before
            tags=("release", channel),
        )
        return [
            Update(
                source=u.source,
                target=u.target,
                # Keyed on the tag so the two paths dedupe
                uid=f"brave:{_tag_from_url(u.url) or u.uid}",
                title=u.title,
                url=u.url,
                published=u.published,
                version=_version_from_title(u.title),
                body=u.body,
                tags=u.tags,
            )
            for u in updates
            if u.title.strip().lower().startswith(prefix)
        ][:MAX_VERSIONS]

    # Firefox

    def _firefox(self, target: Target, client: Client) -> list[Update]:
        fetched = client.get(
            "https://product-details.mozilla.org/1.0/firefox_versions.json", conditional=True
        )
        if fetched is None:
            return []

        doc = fetched.json()
        channel = target.params["channel"]
        version_key, date_key = FIREFOX_CHANNELS[channel]
        version = doc.get(version_key)
        if not version:
            return []

        # Mozilla publishes only current versions
        # one item per channel; the uid carries the version so it reports once.
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"firefox:{channel}:{version}",
                title=f"Firefox {version}",
                url="https://www.mozilla.org/firefox/releases/",
                published=_parse_date(doc.get(date_key)) if date_key else None,
                version=version,
                body=f"{channel} channel",
                tags=("release", channel),
            )
        ]

    # Edge

    def _edge(self, target: Target, client: Client) -> list[Update]:
        fetched = client.get("https://edgeupdates.microsoft.com/api/products", conditional=True)
        if fetched is None:
            return []

        product_name = EDGE_CHANNELS[target.params["channel"]]
        platform = EDGE_PLATFORMS[target.params["platform"]]

        product = next(
            (p for p in fetched.json() if p.get("Product") == product_name), None
        )
        if product is None:
            return []

        seen: dict[str, datetime | None] = {}
        for release in product.get("Releases", []):
            if release.get("Platform") != platform:
                continue
            version = release.get("ProductVersion")
            published = _parse_iso(release.get("PublishedTime"))
            if version and version not in seen:
                seen[version] = published

        ordered = sorted(
            seen.items(), key=lambda kv: kv[1] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"edge:{product_name}:{platform}:{version}",
                title=f"Edge {version}",
                url=EDGE_RELNOTES.get(target.params["channel"], EDGE_RELNOTE_FALLBACK),
                published=published,
                version=version,
                body=f"{product_name} channel on {platform}",
                tags=("release", target.params["channel"]),
            )
            for version, published in ordered[:MAX_VERSIONS]
        ]


def _pretty(browser: str, platform: str, channel: str) -> str:
    names = {"chrome": "Chrome", "brave": "Brave", "firefox": "Firefox", "edge": "Edge"}
    suffix = "" if channel in ("stable", "release") else f" {channel}"
    if browser == "brave":
        return f"Brave{suffix}"
    return f"{names[browser]}{suffix} ({platform})"


def _tag_from_url(url: str) -> str | None:
    match = re.search(r"/releases/tag/([^/?#]+)", url or "")
    return match.group(1) if match else None


def _version_from_title(title: str) -> str | None:
    match = VERSION_RE.search(title or "")
    return match.group(1) if match else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    # Some have more digits than fromisoformat accepts
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        rest = tail[len(digits):] if tail[len(digits):].startswith(("+", "-")) else ""
        text = f"{head}.{digits or '0'}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return _parse_iso(value)
