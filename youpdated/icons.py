"""Targets' icons for the desktop app"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from .http import Client
from .models import Target

if TYPE_CHECKING:
    # sources import this module for page helpers
    from .sources.base import Source

# Kept in the history, which an encrypted setup reads and writes whole on every change
MAX_BYTES = 256 * 1024
# Icons don't change often
REFRESH = timedelta(days=30)
# A target whose icon couldn't be fetched is tried again after this
RETRY = timedelta(days=7)
# Fetched per check (prevents excess delays)
PER_CHECK = 12

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)
_TAG_RE = re.compile(r"<(meta|link)\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


class IconError(Exception):
    """What was fetched cannot be an icon"""


@dataclass(frozen=True)
class Icon:
    mime: str
    data: bytes


def sniff(data: bytes) -> str | None:
    """The image type ``data`` starts as, by bytes"""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for magic, mime in _MAGIC if data.startswith(magic)), None)


def find(source: Source, target: Target, client: Client) -> Icon | None:
    """``target``'s icon, or None"""
    hook = getattr(source, "icon", None)
    named = hook(target, client) if hook is not None else None
    urls = [named] if isinstance(named, str) else [url for url in named or () if url]
    failure: Exception | None = None
    for url in urls:
        try:
            return _fetch(url, client)
        except Exception as exc:  # FetchError, or not an image
            failure = exc
    if failure is not None:
        raise failure
    return None


def _fetch(url: str, client: Client) -> Icon | None:
    fetched = client.get(url)
    if fetched is None:
        return None
    if len(fetched.content) > MAX_BYTES:
        raise IconError(f"{url}: {len(fetched.content)} bytes, over {MAX_BYTES}")
    mime = sniff(fetched.content)
    if mime is None:
        raise IconError(f"{url}: not a PNG, JPEG, GIF, WebP, or ICO image")
    return Icon(mime, fetched.content)


# for hooks that read a page

def _tags(page: str, name: str) -> list[dict[str, str]]:
    found = []
    for tag, body in _TAG_RE.findall(page):
        if tag.lower() == name:
            found.append({
                key.lower(): html.unescape(value.strip("\"'"))
                for key, value in _ATTR_RE.findall(body)
            })
    return found


def _web(base: str, href: str | None) -> str | None:
    if not href:
        return None
    url = urljoin(base, href.strip())
    return url if urlsplit(url).scheme in ("http", "https") else None


def page_image(page: str, base: str) -> str | None:
    """A page's ``og:image``, the picture shown when it's shared"""
    for attrs in _tags(page, "meta"):
        if (attrs.get("property") or attrs.get("name", "")).lower() in ("og:image", "twitter:image"):
            url = _web(base, attrs.get("content"))
            if url:
                return url
    return None


def site_icon(page: str, base: str) -> str | None:
    """A page's icon: touch icon, which is larger, then any icon, then /favicon.ico"""
    links = _tags(page, "link")
    for wanted in ("apple-touch-icon", "icon"):
        for attrs in links:
            rel = attrs.get("rel", "").lower().split()
            if wanted in rel and not attrs.get("type", "").lower().startswith("image/svg"):
                url = _web(base, attrs.get("href"))
                if url and not urlsplit(url).path.lower().endswith(".svg"):
                    return url
    return _web(base, "/favicon.ico")
