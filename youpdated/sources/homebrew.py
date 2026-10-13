"""Homebrew formula and cask versions from the formulae.brew.sh JSON API.

Homebrew has a global RSS feed, but it covers every package, so each formula or cask is fetched on its own. 

The API exposes only the current version and no publish date, so each check gives one item (max)
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, entry_fields

KINDS = ("formula", "cask")
# Formulae generally use `@` for versioned names (python@3.12) and `+` in a few (libstdc++)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]*$")


def _kind_and_name(fields: dict[str, Any], source: str) -> tuple[str, str]:
    given = [kind for kind in KINDS if fields.get(kind) is not None]
    if len(given) != 1:
        raise ConfigEntryError(
            f"sources.{source}: entry needs one of `formula` or `cask`, got {given or 'neither'}"
        )
    kind = given[0]
    name = str(fields[kind]).strip()
    # A bare value may name its kind, as `cask:firefox`
    prefix, sep, rest = name.partition(":")
    if sep and kind == "formula" and prefix in KINDS:
        kind, name = prefix, rest.strip()
    if "/" in name:
        raise ConfigEntryError(
            f"sources.{source}: `{name}` is in a third-party tap; "
            "only homebrew/core and homebrew/cask are on formulae.brew.sh"
        )
    if not NAME_RE.match(name):
        raise ConfigEntryError(f"sources.{source}: `{name}` is not a {kind} name")
    return kind, name


@register
class HomebrewSource:
    name: ClassVar[str] = "homebrew"
    summary: ClassVar[str] = "New versions of a Homebrew formula or cask"

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "formula", self.name)
            kind, name = _kind_and_name(fields, self.name)
            targets.append(
                Target(
                    source=self.name,
                    # The kind is part of the key b/c a formula and a cask may share a name
                    key=f"{kind}:{name}",
                    label=fields.get("name"),
                    params={"kind": kind, "name": name},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        kind, name = target.params["kind"], target.params["name"]
        url = f"https://formulae.brew.sh/api/{kind}/{name}.json"
        fetched = client.get(url, conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no {kind} named `{name}` on formulae.brew.sh")

        doc = fetched.json()
        version = _formula_version(doc) if kind == "formula" else doc.get("version")
        if not version:
            # A HEAD-only formula has no stable version
            return []
        return [
            Update(
                source=self.name,
                target=target.key,
                uid=f"version:{version}",
                title=f"{name} {version}",
                url=f"https://formulae.brew.sh/{kind}/{name}",
                version=version,
                body=doc.get("desc"),
                tags=("release",),
            )
        ]


def _formula_version(doc: dict[str, Any]) -> str | None:
    """``stable`` plus the revision, as shown by brew: a revision bump is a new build of the same version."""
    stable = (doc.get("versions") or {}).get("stable")
    if not stable:
        return None
    revision = doc.get("revision") or 0
    return f"{stable}_{revision}" if revision else str(stable)
