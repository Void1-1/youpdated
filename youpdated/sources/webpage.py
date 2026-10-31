"""Version found on a web page, for apps without a feed or registry
    sources:
      webpage:
        - https://www.videolan.org/vlc/
        - url: https://www.7-zip.org/
          name: 7-Zip
          regex: 'Download 7-Zip ([\\d.]+)'

Without ``regex:`` the page is read for its version, best guess first:

1. structured data: JSON-LD or microdata ``softwareVersion``
2. download links with a version in the file name (``App-2.4.1.dmg``)
3. text naming latest version: "Latest version: 2.4.1"
4. text labelling a version: "Version 2.4.1"
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, ClassVar, Iterable
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from ..versions import Release, format_release, release
from .base import ConfigEntryError, accepted, entry_fields, require, url_parts

SIGNAL_CACHE = "webpage_signal"

# Best first
SIGNALS = ("structured", "downloads", "latest", "label")
DESCRIBED = {
    "structured": "the page's structured data",
    "downloads": "the download links",
    "latest": 'the page\'s "latest version" text',
    "label": 'the page\'s "version" labels',
}
# Candidates shown in an error
SHOWN = 5
# A captured version longer than this is incorrect
MAX_VERSION = 100
# visible text shorter than this is likely built by js
SPARSE_TEXT = 500

_PRE_WORDS = r"alpha|beta|rc|preview|pre|dev|canary|nightly|snapshot"
# day-first or month-first date
_DATE = r"\d{1,2}\.\d{1,2}\.(?:19|20)\d{2}(?!\.?\d)"
_MAX_DIGITS = 30
# dotted version as a word of its own
VERSION_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?<!\d\.)(?!v?{_DATE})[vV]?"
    r"(?P<numbers>\d+(?:\.\d+)+)(?!\.?\d|\.[xX*](?![A-Za-z0-9]))"
    r"(?P<pre>"
    rf"[-_.~+]?(?:{_PRE_WORDS})(?:[-_.]?\d+)?(?![A-Za-z])"
    r"|\s+(?:alpha|beta|rc|preview)(?:[-_. ]?\d+(?![.\d]))?(?![A-Za-z])(?!\s*v?\d+\.\d)"
    r"|(?:a|b|rc)\d+(?![A-Za-z])"
    r")?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(rf"[vV]?(\d{{1,{_MAX_DIGITS}}})")

_SEP = r"\s*(?:[:=\-–—]\s*)?"
LATEST_RE = re.compile(
    r"\b(?:latest|current|newest|stable)\b(?:\s+(?:stable|public|official))?"
    r"(?:\s+(?:version|release|build))?(?:\s+is)?" + _SEP + r"(?=v?\d)",
    re.IGNORECASE,
)
# Not bare `v2.4`: licenses (`LGPL v2.1`), runtimes (`Electron v31.2.0`), and docs menus use it
LABEL_RE = re.compile(r"\b(?:version|release|ver\.)" + _SEP + r"(?=v?\d)", re.IGNORECASE)
# A version in a sentence like these is one the app needs, replaced, or is built on, not its own
_CLAUSE_SPLIT_RE = re.compile(r"\n|[.;!?|•·](?:\s|$)")
_BEFORE_RE = re.compile(
    r"requir|minimum|\bmin\b|at least|compatib|support|deprecat|end of life|\beol\b|licen[cs]"
    r"|\b(?:older|earlier|prior|previous(?:ly)?|legacy|needs?|[al]?gpl|mpl|apache)\b"
    r"|\b(?:built|tested|based|runs|works|powered)\s+(?:on|with|by)\b"
    # before the label: `upgrading from version 1.9`, `macOS version 14`
    r"|\b(?:from|since|than|until|before|after|on|using|running|macos|mac os(?: x)?|os x|osx"
    r"|windows|ios"
    r"|ipados|watchos|android|linux|ubuntu|debian|fedora|kernel|java|jdk|jre|python"
    r"|node(?:\.js)?|electron|\.net|api|protocol|schema|format|firmware)\s*$",
    re.IGNORECASE,
)
_AFTER_RE = re.compile(
    r"\s*(?:\+(?![A-Za-z0-9])"
    r"|(?:or|and)\s+(?:later|newer|higher|above|greater|up|earlier|older|below|lower|before)\b"
    r"|(?:is\s+)?(?:required|needed)\b"
    # a range to another version, not a date: `2.0 - 2.4`, but not `2.4.1 - 2026-10-29`
    rf"|(?:-|–|—|to|through)\s*(?!{_DATE})v?\d+\.\d)",
    re.IGNORECASE,
)

# Files a download link can point to; longest first
DOWNLOAD_SUFFIXES = tuple(
    sorted(
        (
            ".dmg", ".pkg", ".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle",
            ".zip", ".7z", ".rar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2",
            ".tar.zst", ".deb", ".rpm", ".appimage", ".apk", ".flatpakref", ".snap", ".jar",
            ".iso", ".img", ".xip", ".run",
        ),
        key=len,
        reverse=True,
    )
)
_FEED_TYPES = ("application/rss+xml", "application/atom+xml")
_SOFTWARE_TYPES = {
    "softwareapplication", "mobileapplication", "webapplication", "videogame",
    "softwaresourcecode",
}
_SKIP_TEXT = {"script", "style", "template", "noscript", "svg"}
# Never have text, so an itemprop on one is its `content` or nothing
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
}
# Elements HTML allow unclosed, and start tags that close
_CLOSED_BY = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "tr": {"tr"},
    "option": {"option"},
    "p": {"p", "div", "ul", "ol", "dl", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre"},
}
_BLOCKS = {
    "br", "p", "div", "li", "ul", "ol", "tr", "td", "th", "dt", "dd", "table", "section",
    "article", "header", "footer", "nav", "aside", "main", "h1", "h2", "h3", "h4", "h5", "h6",
    "option", "figcaption", "blockquote", "pre", "hr", "title",
}


@dataclass(frozen=True)
class Found:
    """a mention of a version"""

    version: str
    # Equal for different spellings of a version: `2.4` and `2.4.0`, `rc1` and `-rc.1`
    key: tuple[Release, str]
    where: str

    @property
    def prerelease(self) -> bool:
        return bool(self.key[1])

    @property
    def uid(self) -> str:
        numbers, marker = self.key
        return "version:" + format_release(numbers) + (f"-{marker}" if marker else "")


def found_in(text: str, where: str | None = None) -> Found | None:
    """version ``text`` names"""
    versions = {f.key: f for f in _versions(text, where or text)}
    return next(iter(versions.values())) if len(versions) == 1 else None


def _versions(text: str, where: str) -> Iterable[Found]:
    for match in VERSION_RE.finditer(text):
        found = _found(match, where)
        if found is not None:
            yield found


def _found(match: re.Match[str], where: str) -> Found | None:
    numbers = release(match.group("numbers"))
    if numbers is None:
        return None
    pre = match.group("pre") or ""
    marker = re.sub(r"[-_.~+\s]", "", pre).lower()
    return Found(match.group("numbers") + pre, (numbers, marker), where)


def _distinct(found: list[Found]) -> list[Found]:
    """``found`` with one entry per version, first spelling kept"""
    return list({f.key: f for f in reversed(found)}.values())[::-1]


def _listed(found: list[Found]) -> str:
    shown = ", ".join(f.version for f in found[:SHOWN])
    return shown + (", …" if len(found) > SHOWN else "")


@dataclass
class Page:
    """What version reading options a page offers"""

    title: str | None = None
    feeds: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    structured: list[str] = field(default_factory=list)
    itemprops: list[str] = field(default_factory=list)
    text: str = ""
    scripts: int = 0


class _PageReader(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.page = Page()
        self._text: list[str] = []
        self._skip = 0
        self._title: list[str] | None = None
        self._script: list[str] | None = None
        # (tag, depth, parts) of an itemprop element
        self._itemprop: tuple[str, int, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if self._itemprop is not None:
            name, depth, parts = self._itemprop
            if depth == 1 and tag in _CLOSED_BY.get(name, ()):
                self._end_itemprop()
            elif tag == name:
                self._itemprop = (name, depth + 1, parts)
        if tag == "title" and self.page.title is None and not self._skip:
            self._title = []
        elif tag == "script":
            if values.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
                self._script = []
            else:
                self.page.scripts += 1
        elif tag == "link" and "alternate" in values.get("rel", "").lower().split():
            kind = values.get("type", "").split(";")[0].strip().lower()
            described = f"{values.get('title', '')} {values.get('href', '')}".lower()
            if kind in _FEED_TYPES and values.get("href") and "comment" not in described:
                self._link(self.page.feeds, values["href"])
        elif tag == "a" and values.get("href"):
            self._link(self.page.links, values["href"])

        if "softwareversion" in values.get("itemprop", "").lower().split():
            if "content" in values:
                self.page.itemprops.append(values["content"])
            elif self._itemprop is None and tag not in _VOID:
                self._itemprop = (tag, 1, [])

        if tag in _SKIP_TEXT:
            self._skip += 1
        elif tag in _BLOCKS:
            self._text.append("\n")

    def _link(self, links: list[str], href: str) -> None:
        # an unreadable address (`http://[insert link]`)
        try:
            links.append(urljoin(self.base, href))
        except ValueError:
            pass

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title is not None:
            self.page.title = " ".join("".join(self._title).split()) or None
            self._title = None
        elif tag == "script" and self._script is not None:
            self.page.structured.append("".join(self._script))
            self._script = None
        if self._itemprop is not None:
            name, depth, parts = self._itemprop
            if tag == name and depth > 1:
                self._itemprop = (name, depth - 1, parts)
            elif tag == name or (depth == 1 and name in _CLOSED_BY and tag in _BLOCKS):
                self._end_itemprop()
        if tag in _SKIP_TEXT and self._skip:
            self._skip -= 1
        elif tag in _BLOCKS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        if self._title is not None:
            self._title.append(data)
        if self._itemprop is not None:
            self._itemprop[2].append(data)
        if not self._skip:
            self._text.append(data)

    def _end_itemprop(self) -> None:
        if self._itemprop is not None:
            self.page.itemprops.append("".join(self._itemprop[2]))
            self._itemprop = None

    def finish(self) -> Page:
        self._end_itemprop()
        text = re.sub(r"[^\S\n]+", " ", "".join(self._text))
        self.page.text = re.sub(r" ?\n[\s]*", "\n", text).strip()
        return self.page


def read_page(markup: str, base: str) -> Page:
    reader = _PageReader(base)
    try:
        reader.feed(markup)
        reader.close()
    except Exception:
        pass
    return reader.finish()

# signals

def _structured(page: Page) -> tuple[list[Found], list[str]]:
    """Versions in JSON-LD, microdata, software names"""
    found: list[Found] = []
    names: list[str] = []
    for block in page.structured:
        try:
            doc = json.loads(block, strict=False)
        except (ValueError, RecursionError):
            continue
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(reversed(node))
                continue
            if not isinstance(node, dict):
                continue
            kinds = node.get("@type")
            kinds = [kinds] if isinstance(kinds, str) else kinds if isinstance(kinds, list) else []
            software = any(str(k).lower() in _SOFTWARE_TYPES for k in kinds)
            for key in ("softwareVersion", "version") if software else ("softwareVersion",):
                value = node.get(key)
                # not a float: `2.10` is 2.1
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    version = _structured_version(str(value), key)
                    if version is not None:
                        found.append(version)
                        if isinstance(node.get("name"), str) and node["name"].strip():
                            names.append(" ".join(node["name"].split()))
                        break
            stack.extend(reversed([v for v in node.values() if isinstance(v, (dict, list))]))
    for value in page.itemprops:
        version = _structured_version(value, "softwareVersion")
        if version is not None:
            found.append(version)
    return found, names


def _structured_version(value: str, key: str) -> Found | None:
    """structured data states its a version"""
    value = " ".join(html.unescape(value).split())[:MAX_VERSION]
    found = found_in(value, f"{key} {value}")
    if found is None:
        single = _SINGLE_RE.fullmatch(value)
        if single:
            number = int(single.group(1))
            found = Found(single.group(1), ((number,), ""), f"{key} {value}")
    return found


def _downloads(page: Page) -> list[Found]:
    found = []
    for link in page.links:
        for name in _file_names(link):
            stem = _download_stem(name)
            if stem is None:
                continue
            version = found_in(stem, name)
            if version is not None:
                found.append(version)
    return found


def _file_names(link: str) -> Iterable[str]:
    """last path segment of ``link`` and (`?file=App-1.2.dmg`)"""
    try:
        parts = urlsplit(link)
    except ValueError:
        return
    yield unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    for _, value in parse_qsl(parts.query):
        yield value.rstrip("/").rsplit("/", 1)[-1]


def _download_stem(name: str) -> str | None:
    lowered = name.lower()
    for suffix in DOWNLOAD_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def _labelled(text: str, label: re.Pattern[str]) -> list[Found]:
    found = []
    for match in label.finditer(text):
        version = VERSION_RE.match(text, match.end())
        if version is None or _excluded(text, match.start(), version.end()):
            continue
        snippet = " ".join(text[match.start() : version.end()].split())
        result = _found(version, snippet)
        if result is not None:
            found.append(result)
    return found


def _excluded(text: str, start: int, end: int) -> bool:
    clause = _CLAUSE_SPLIT_RE.split(text[max(0, start - 60) : start])[-1]
    return bool(_BEFORE_RE.search(clause) or _AFTER_RE.match(text, end))


def signals(page: Page, structured: list[Found]) -> dict[str, list[Found]]:
    """Versions found, in page order"""
    return {
        "structured": structured,
        "downloads": _downloads(page),
        "latest": _labelled(page.text, LATEST_RE),
        "label": _labelled(page.text, LABEL_RE),
    }


def decide(
    page: Page, found: dict[str, list[Found]], pinned: str | None, url: str
) -> tuple[str, Found]:
    """signal used and named version"""
    if pinned is not None:
        if not found[pinned]:
            raise FetchError(_moved(found, pinned, url))
        chosen = pinned
    else:
        chosen = next((name for name in SIGNALS if found[name]), "")
        if not chosen:
            raise FetchError(_nothing(page))

    distinct = _distinct(found[chosen])
    if len(distinct) > 1:
        raise FetchError(
            f"found more than one version in {DESCRIBED[chosen]} ({_listed(distinct)}); "
            "set `regex:` to define the correct version location"
        )
    (winner,) = distinct
    for other in SIGNALS:
        if other == chosen:
            continue
        theirs = _distinct(found[other])
        if len(theirs) == 1 and not _agrees(theirs[0], winner):
            raise FetchError(
                f"found {winner.version} in {DESCRIBED[chosen]} but {theirs[0].version} in "
                f"{DESCRIBED[other]}; set `regex:` to say which is current"
            )
    return chosen, winner


def _agrees(other: Found, winner: Found) -> bool:
    """If ``other`` is ``winner``"""
    if other.key == winner.key:
        return True
    (numbers, marker), (series, other_marker) = winner.key, other.key
    return not other_marker and numbers[: len(series)] == series


def _nothing(page: Page) -> str:
    hints = []
    if page.feeds:
        hints.append(
            f"the page links a feed, which can be watched: youpdated add feed {page.feeds[0]}"
        )
    if page.scripts and len(page.text) < SPARSE_TEXT:
        hints.append("it may be built by JavaScript, which isn't run")
    hints.append("use `regex:` to set the version location")
    return "no version found on the page; " + "; ".join(hints)


def _moved(found: dict[str, list[Found]], pinned: str, url: str) -> str:
    message = f"earlier checks found the version in {DESCRIBED[pinned]}, there is no longer a version there"
    elsewhere = next(
        (
            (name, distinct[0])
            for name in SIGNALS
            if len(distinct := _distinct(found[name])) == 1
        ),
        None,
    )
    if elsewhere is not None:
        message += f", though there's {elsewhere[1].version} in {DESCRIBED[elsewhere[0]]}"
    return (
        message
        + ". The page may have changed: set `regex:` to set the version location, or start over "
        f"with `youpdated rm --wipe webpage {url}` and readding it"
    )


def _not_a_page(content: bytes, headers: dict[str, str]) -> str | None:
    head = content[:1024].lstrip().lower()
    if b"<html" in head or b"<!doctype html" in head:
        return None
    if b"<rss" in head or b"<feed" in head or b"<rdf:rdf" in head:
        return "feed"
    kind = headers.get("content-type", "").split(";")[0].strip().lower()
    if kind and "html" not in kind and not kind.startswith("text/"):
        return kind
    return None


def _captured(match: re.Match[str]) -> str:
    if "version" in match.re.groupindex:
        value = match.group("version")
    elif match.re.groups:
        value = match.group(1)
    else:
        value = match.group(0)
    return " ".join(html.unescape(value or "").split())


@register
class WebpageSource:
    name: ClassVar[str] = "webpage"
    summary: ClassVar[str] = "Version names by a web page with no feed or registry"
    caches: ClassVar[tuple[str, ...]] = (SIGNAL_CACHE,)
    fallback: ClassVar[bool] = True

    def claim(self, value: str) -> str | None:
        """Any http(s) URL"""
        return accepted(self, value) if url_parts(value) else None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "url", self.name)
            url = str(require(fields, "url", self.name)).strip()
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ConfigEntryError(
                    f"sources.{self.name}: `{url}` is not an http(s) page URL"
                )
            pattern = fields.get("regex")
            if pattern is not None:
                if not isinstance(pattern, str) or not pattern.strip():
                    raise ConfigEntryError(
                        f"sources.{self.name}: `regex` for {url} must be a pattern, got {pattern!r}"
                    )
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigEntryError(
                        f"sources.{self.name}: `regex` for {url} is not a valid pattern: {exc}"
                    ) from exc
            targets.append(
                Target(
                    source=self.name,
                    key=url,
                    label=fields.get("name"),
                    params={"regex": pattern},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        fetched = client.get(target.key, conditional=True)
        if fetched is None:
            return []

        instead = _not_a_page(fetched.content, fetched.headers)
        if instead == "feed":
            raise FetchError(
                f"the address is a feed, not a web page: youpdated add feed {target.key}"
            )
        if instead is not None:
            raise FetchError(f"the address returned {instead}, not a web page")

        markup = fetched.text
        page = read_page(markup, fetched.url)
        structured, names = _structured(page)
        if not target.label:
            names = set(names)
            target.label = (
                names.pop() if len(names) == 1 else page.title or urlsplit(target.key).netloc
            )

        pattern = target.params.get("regex")
        if pattern:
            match = re.search(pattern, markup)
            if match is None:
                raise FetchError("`regex` matched nothing on the page")
            version = _captured(match)
            if not version or len(version) > MAX_VERSION:
                raise FetchError(f"`regex` matched {version[:40]!r}, which isn't a version")
            named = found_in(version)
            prerelease = named.prerelease if named else False
            uid = named.uid if named else f"version:{version}"
            body = None
        else:
            state = client.state
            pinned = state.cache_get(SIGNAL_CACHE, target.key) if state else None
            found = signals(page, structured)
            signal, winner = decide(page, found, pinned if pinned in SIGNALS else None, target.key)
            if state is not None and pinned != signal:
                state.cache_set(SIGNAL_CACHE, target.key, signal)
            version, prerelease, uid = winner.version, winner.prerelease, winner.uid
            body = f"Read from {DESCRIBED[signal]}: {winner.where}"

        return [
            Update(
                source=self.name,
                target=target.key,
                uid=uid,
                title=version,
                url=target.key,
                version=version,
                body=body,
                tags=("release",) + (("prerelease",) if prerelease else ()),
            )
        ]
