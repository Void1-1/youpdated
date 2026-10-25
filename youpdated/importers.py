"""add watched sources from common dependency files"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .config import ConfigError
from .registry import all_sources
from .sources.base import ConfigEntryError, Source, url_parts

# package.json sections that list packages
NPM_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
# npm package names, old ones with capitals included
_NPM_NAME_RE = re.compile(
    r"^(?:@[a-z0-9~-][a-z0-9._~-]*/)?[a-z0-9~-][a-z0-9._~-]*$", re.IGNORECASE
)
# node-semver's range grammar, for yarn's `npm:^4`
_XR = r"(?:[xX*]|\d+)"
_PARTIAL = rf"v?{_XR}(?:\.{_XR}(?:\.{_XR}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)?)?"
_SIMPLE = rf"(?:(?:[<>]=?|=|~|\^)\s*)?{_PARTIAL}"
_RANGE = rf"(?:{_PARTIAL}\s+-\s+{_PARTIAL}|{_SIMPLE}(?:\s+{_SIMPLE})*)?"
_SEMVER_RANGE_RE = re.compile(rf"^\s*{_RANGE}(?:\s*\|\|\s*{_RANGE})*\s*$")
# what npm and pip install from a file instead of a registry
_NPM_ARCHIVES = (".tgz", ".tar", ".tar.gz")
_ARCHIVES = (".whl", ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
_PYPI_HOSTS = ("pypi.org", "pypi.python.org")
_COMMENT_RE = re.compile(r"(^|\s+)#.*$")
# `user:token@` in a URL
_CREDENTIALS_RE = re.compile(r"(?<=://)[^/\s@]+@")
# `brew "wget"`, `cask 'firefox', args: {...}`, `mas("Xcode", id: 497799835)`
_BREWFILE_RE = re.compile(r"""^\s*([a-z_]+)\s*\(?\s*(["'])(.+?)\2""")
# other `brew bundle` entries, which no source watches
_BUNDLE_KINDS = ("mas", "vscode", "go", "cargo", "uv", "flatpak", "whalebrew")


@dataclass
class Found:
    """entries read for sources"""

    entries: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)
    _sources: dict[str, Source] = field(default_factory=all_sources, init=False, repr=False)

    def add(self, source: str, entry: str) -> None:
        """``entry`` under ``source`` if the source reads it"""
        try:
            targets = self._sources[source].targets([entry])
        except ConfigEntryError as exc:
            self.skip(entry, str(exc).removeprefix(f"sources.{source}: "))
            return
        keys = {(source, t.key) for t in targets}
        if keys <= self._seen:
            return
        self._seen |= keys
        self.entries.setdefault(source, []).append(entry)

    def skip(self, what: str, why: str) -> None:
        """once each, without credentials"""
        skipped = (_redact(what), _redact(why))
        if skipped not in self.skipped:
            self.skipped.append(skipped)

    def warn(self, warning: str) -> None:
        warning = _redact(warning)
        if warning not in self.warnings:
            self.warnings.append(warning)


def read_file(path: Path, found: Found, fallback: str | None = None) -> None:
    """entries from ``path``, read as named or as ``fallback``"""
    # not is_file(): a pipe is fine, `<(pip freeze)`
    if not path.exists():
        raise ConfigError(f"no file at {path}")
    READERS[file_kind(path, fallback)](path, found)


def file_kind(path: Path, fallback: str | None = None) -> str:
    name = path.name.lower()
    if name == "package.json":
        return "package.json"
    if name in ("brewfile", ".brewfile") or name.endswith(".brewfile"):
        return "Brewfile"
    # requirements.txt, requirements-dev.txt, requirements.in, requirements/base.txt
    if path.suffix.lower() in (".txt", ".in") and (
        "requirements" in name or path.parent.name.lower() == "requirements"
    ):
        return "requirements.txt"
    if fallback is not None:
        return fallback
    raise ConfigError(
        f"can't tell what kind of file {path} is; name with --format "
        f"({' | '.join(READERS)})"
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"cannot read {path}: it is not UTF-8 text") from exc


def _redact(text: str) -> str:
    return _CREDENTIALS_RE.sub("", text)

# package.json

def read_package_json(path: Path, found: Found) -> None:
    try:
        doc = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigError(f"{path} is not a package.json: its top level is not an object")
    for section in NPM_SECTIONS:
        listed = doc.get(section)
        if not isinstance(listed, dict):
            continue
        for name, spec in listed.items():
            spec = spec.strip() if isinstance(spec, str) else ""
            package = _npm_package(name, spec)
            if package is None:
                found.skip(name, f"installed from `{spec}`, not npm registry")
            elif not _NPM_NAME_RE.match(package):
                found.skip(name, f"`{package}` is not an npm package name")
            else:
                found.add("npm", package)


def _npm_package(name: str, spec: str) -> str | None:
    """registry package a dependency installs, or None"""
    protocol, colon, rest = spec.partition(":")
    if colon and protocol == "npm":
        # `npm:real@^1`, `npm:@scope/real`, or yarn's `npm:^1` for this name
        if _SEMVER_RANGE_RE.match(rest):
            return name
        at = rest.find("@", 1)
        return rest[:at] if at > 0 else rest
    if colon and protocol == "catalog":
        # pnpm and bun keep the range in the workspace; still the registry
        return name
    # other protocols, paths, tarballs, and `owner/repo`
    if colon or "/" in spec or "\\" in spec or spec.startswith("."):
        return None
    if spec.lower().endswith(_NPM_ARCHIVES):
        return None
    return name

# requirements.txt

@dataclass
class _Pip:
    """what one `pip install -r` is told, includes and all"""

    names: list[str] = field(default_factory=list)
    index: str | None = None
    extra: list[str] = field(default_factory=list)
    no_index: bool = False
    read: set[Path] = field(default_factory=set)


def read_requirements(path: Path, found: Found) -> None:
    """requirements pip would install from PyPI, following ``-r``"""
    pip = _Pip()
    _read_pip(path, found, pip)
    # index options cover every requirement, wherever they are written
    if pip.no_index:
        elsewhere = "pip is told not to use an index"
    elif pip.index:
        elsewhere = f"installed from {pip.index}, not PyPI"
    else:
        elsewhere = None
    for name in pip.names:
        if elsewhere:
            found.skip(name, elsewhere)
        else:
            found.add("pypi", name)
    for url in pip.extra:
        found.warn(
            f"{path} also installs from {url}; a package from there is watched on PyPI, "
            "which may not be the same package"
        )


def _read_pip(path: Path, found: Found, pip: _Pip) -> None:
    # here, not at the top: every command would pay to load it
    from packaging.requirements import InvalidRequirement, Requirement

    resolved = path.resolve()
    if resolved in pip.read:
        return
    pip.read.add(resolved)

    for line in _logical_lines(_read(path)):
        if line.startswith("-"):
            flag, value = _option(line)
            if flag in ("-r", "--requirement"):
                include = _included(value, path)
                if include is None:
                    found.skip(value, "a requirements file at a URL is not fetched")
                else:
                    _read_pip(include, found, pip)
            elif flag in ("-e", "--editable"):
                found.skip(value, "an editable install, not from PyPI")
            elif flag in ("-i", "--index-url"):
                url = url_parts(value)
                pip.index = None if url and url[0] in _PYPI_HOSTS else value
            elif flag == "--extra-index-url":
                pip.extra.append(value)
            elif flag == "--no-index":
                pip.no_index = True
            continue

        words = []
        for word in line.split():
            if word.startswith("-"):
                break
            words.append(word)
        text = " ".join(words)
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            found.skip(text, "not a PyPI requirement")
            continue
        if requirement.url:
            found.skip(requirement.name, f"installed from {requirement.url}, not PyPI")
        elif requirement.name.lower().endswith(_ARCHIVES):
            found.skip(text, "a local file, not PyPI")
        else:
            pip.names.append(requirement.name)


def _included(value: str, path: Path) -> Path | None:
    """file ``-r value`` in ``path`` names, or None for a URL"""
    parts = urlsplit(value)
    if parts.scheme == "file":
        return Path(url2pathname(parts.path))
    if "://" in value:
        return None
    return path.parent / Path(value).expanduser()


def _logical_lines(text: str) -> list[str]:
    """lines joined at trailing ``\\``, without comments or blanks"""
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        # as in pip, a comment never continues
        comment = raw.lstrip().startswith("#")
        if raw.endswith("\\") and not comment:
            pending += raw[:-1]
            continue
        joined.append(pending + (" " + raw if comment else raw))
        pending = ""
    joined.append(pending)
    lines = (_COMMENT_RE.sub("", line).strip() for line in joined)
    return [line for line in lines if line]


def _option(line: str) -> tuple[str, str]:
    """``-r file``, ``-rfile``, ``--requirement=file`` as ``(flag, value)``"""
    flag, *rest = line.split(None, 1)
    value = rest[0] if rest else ""
    if flag.startswith("--") and "=" in flag:
        flag, _, value = flag.partition("=")
    elif not flag.startswith("--") and len(flag) > 2:
        flag, value = flag[:2], flag[2:]
    return flag, value.strip()

# Brewfile

def read_brewfile(path: Path, found: Found) -> None:
    """``brew`` and ``cask`` lines, like ``brew bundle dump``"""
    for line in _read(path).splitlines():
        match = _BREWFILE_RE.match(line)
        if match is None:
            continue
        kind, name = match.group(1), match.group(3).strip()
        if kind == "brew":
            found.add("homebrew", name.removeprefix("homebrew/core/"))
        elif kind == "cask":
            found.add("homebrew", f"cask:{name.removeprefix('homebrew/cask/')}")
        elif kind in _BUNDLE_KINDS:
            found.skip(f"{kind} {name}", f"no source watches `{kind}` entries")


READERS: dict[str, Callable[[Path, Found], None]] = {
    "package.json": read_package_json,
    "requirements.txt": read_requirements,
    "Brewfile": read_brewfile,
}
