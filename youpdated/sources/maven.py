"""Maven artifact versions from a repository's `maven-metadata.xml`

Maven Central by default; `repository:` points at any other Maven 2 layout.
No per-version dates, but versions are listed in deploy order.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Iterable
from xml.etree import ElementTree as ET

from ..http import Client, FetchError
from ..models import Target, Update
from ..registry import register
from .base import ConfigEntryError, accepted, entry_fields, highest, recent_versions, url_parts

CENTRAL = "https://repo1.maven.org/maven2"
PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# Maven's qualifiers for an unfinished version: alpha, beta, milestone, release candidate
_PRERELEASE_RE = re.compile(
    r"(?i)(?:^|[-.\d])(alpha|beta|milestone|rc|cr|preview|ea)[-.]?\d*$|[-.][mab]\d+$"
)
_SNAPSHOT = "-SNAPSHOT"


def _is_prerelease(version: str) -> bool:
    return bool(_PRERELEASE_RE.search(version))


@register
class MavenSource:
    name: ClassVar[str] = "maven"
    summary: ClassVar[str] = "New versions of a Maven artifact"

    def claim(self, value: str) -> str | None:
        """artifact page on central.sonatype.com or mvnrepository.com"""
        url = url_parts(value)
        if url and url[0] in ("central.sonatype.com", "mvnrepository.com"):
            segments = url[1].strip("/").split("/")
            if len(segments) >= 3 and segments[0] == "artifact":
                return accepted(self, f"{segments[1]}:{segments[2]}")
        return None

    def targets(self, entries: list[Any]) -> list[Target]:
        targets = []
        for entry in entries:
            fields = entry_fields(entry, "package", self.name)
            group, artifact = _coordinates(fields, self.name)
            repository = str(fields.get("repository") or CENTRAL).strip().rstrip("/")
            if not repository.startswith(("https://", "http://")):
                raise ConfigEntryError(
                    f"sources.{self.name}: repository `{repository}` is not an http(s) URL"
                )
            targets.append(
                Target(
                    source=self.name,
                    key=f"{group}:{artifact}",
                    label=fields.get("name"),
                    params={"group": group, "artifact": artifact, "repository": repository},
                )
            )
        return targets

    def fetch(self, target: Target, client: Client) -> Iterable[Update]:
        group, artifact = target.params["group"], target.params["artifact"]
        repository = target.params["repository"]
        base = f"{repository}/{group.replace('.', '/')}/{artifact}"
        fetched = client.get(f"{base}/maven-metadata.xml", conditional=True, soft_statuses=(404,))
        if fetched is None:
            return []
        if fetched.status == 404:
            raise FetchError(f"no artifact `{target.key}` in {repository}")
        try:
            root = ET.fromstring(fetched.content)
        except ET.ParseError as exc:
            raise FetchError(f"{base}/maven-metadata.xml is not Maven metadata: {exc}") from None

        deployed = [
            (v.text or "").strip() for v in root.iterfind("./versioning/versions/version")
        ]
        newest = [v for v in reversed(deployed) if v and not v.endswith(_SNAPSHOT)]
        versions = list(dict.fromkeys(newest))
        # `<release>` is only the last non-snapshot deployed
        release = (root.findtext("./versioning/release") or "").strip()
        if release not in versions or _is_prerelease(release):
            release = highest(v for v in versions if not _is_prerelease(v)) or ""
        latest = release or None
        listed = recent_versions(versions, _is_prerelease, latest)

        updates = []
        for version in versions:
            if version not in listed:
                continue
            tags = ("release",)
            if _is_prerelease(version):
                tags += ("prerelease",)
            if version == latest:
                tags += ("latest",)
            url = (
                f"https://central.sonatype.com/artifact/{group}/{artifact}/{version}"
                if repository == CENTRAL else f"{base}/{version}/"
            )
            updates.append(
                Update(
                    source=self.name,
                    target=target.key,
                    uid=f"version:{version}",
                    title=f"{artifact} {version}",
                    url=url,
                    version=version,
                    tags=tags,
                )
            )
        return updates


def _coordinates(fields: dict[str, Any], source: str) -> tuple[str, str]:
    """`group:artifact`, from `package` or from `group` and `artifact`"""
    if fields.get("package") is not None:
        value = str(fields["package"]).strip()
        group, sep, artifact = value.partition(":")
        if not sep:
            raise ConfigEntryError(
                f"sources.{source}: `{value}` is not a Maven artifact, "
                "expected `group:artifact` format: com.google.guava:guava"
            )
        artifact = artifact.split(":", 1)[0]
    else:
        group = str(fields.get("group") or "").strip()
        artifact = str(fields.get("artifact") or "").strip()
        if not group or not artifact:
            raise ConfigEntryError(
                f"sources.{source}: entry needs `group:artifact`, or both `group` and `artifact`"
            )
    for part in (group, artifact):
        if not PART_RE.match(part):
            raise ConfigEntryError(f"sources.{source}: `{part}` is not a Maven group or artifact id")
    return group, artifact
