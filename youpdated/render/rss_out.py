"""Aggregated Atom feed"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from ..runner import RunResult

ATOM = "http://www.w3.org/2005/Atom"
FEED_ID = "urn:youpdated:feed"


def _stamp(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def render(result: RunResult, title: str = "Youpdated") -> str:
    ET.register_namespace("", ATOM)
    feed = ET.Element(f"{{{ATOM}}}feed")

    ET.SubElement(feed, f"{{{ATOM}}}id").text = FEED_ID
    ET.SubElement(feed, f"{{{ATOM}}}title").text = title
    ET.SubElement(feed, f"{{{ATOM}}}updated").text = _stamp(None)
    ET.SubElement(feed, f"{{{ATOM}}}generator").text = "youpdated"
    ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": "self", "href": FEED_ID})

    for update in result.updates:
        entry = ET.SubElement(feed, f"{{{ATOM}}}entry")
        digest = hashlib.sha256(
            "|".join(update.dedupe_key).encode("utf-8")
        ).hexdigest()[:24]
        ET.SubElement(entry, f"{{{ATOM}}}id").text = f"urn:youpdated:{digest}"

        label = f"[{update.source}] {update.target}"
        ET.SubElement(entry, f"{{{ATOM}}}title").text = f"{label} — {update.title}"
        ET.SubElement(entry, f"{{{ATOM}}}updated").text = _stamp(update.published)
        if update.published:
            ET.SubElement(entry, f"{{{ATOM}}}published").text = _stamp(update.published)
        if update.url:
            ET.SubElement(entry, f"{{{ATOM}}}link", {"rel": "alternate", "href": update.url})

        author = ET.SubElement(entry, f"{{{ATOM}}}author")
        ET.SubElement(author, f"{{{ATOM}}}name").text = label

        for tag in update.tags:
            ET.SubElement(entry, f"{{{ATOM}}}category", {"term": tag})

        summary = update.body or update.title
        if update.version:
            summary = f"{update.version} — {summary}"
        ET.SubElement(entry, f"{{{ATOM}}}summary", {"type": "text"}).text = summary

    ET.indent(feed, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        feed, encoding="unicode"
    )
