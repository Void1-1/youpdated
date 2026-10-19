"""Check a canary run's ``youpdated check --all --json`` output.

Sources return ``[]``, a parser broken by an upstream change can come back clean and empty. 
With ``--all`` every target in the canary config always has something to report, so empty is a failure.

Reads the JSON from a path, or stdin. Exits 1 if anything is wrong.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter


def problems(report: dict) -> list[str]:
    found = [f"{e['source']} {e['target']}: {e['message']}" for e in report["errors"]]

    counts = Counter((u["source"], u["target"]) for u in report["updates"])
    errored = {(e["source"], e["target"]) for e in report["errors"]}
    for target in report["targets"]:
        entry = (target["source"], target["key"])
        if not counts[entry] and (target["source"], target["label"]) not in errored:
            found.append(f"{target['source']} {target['label']}: fetched, but parsed no items")

    for update in report["updates"]:
        missing = [field for field in ("title", "url") if not update.get(field)]
        if missing:
            found.append(
                f"{update['source']} {update['target']}: item {update['uid']!r} "
                f"has no {' or '.join(missing)}"
            )

    if not report["targets"]:
        found.append("no targets ran: is the source name right?")
    return found


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as handle:
            report = json.load(handle)
    else:
        report = json.load(sys.stdin)

    counts = Counter((u["source"], u["target"]) for u in report["updates"])
    for target in report["targets"]:
        print(f"{counts[(target['source'], target['key'])]:>4}  {target['source']:<9} {target['label']}")

    found = problems(report)
    # An annotation shows on the run summary without opening the log
    prefix = "::error::" if os.environ.get("GITHUB_ACTIONS") else "FAIL "
    for line in found:
        print(prefix + line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
