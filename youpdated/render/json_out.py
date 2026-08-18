from __future__ import annotations

import json
from datetime import datetime, timezone

from ..runner import RunResult


def render(result: RunResult) -> str:
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "baseline": result.baseline,
        "counts": {
            "targets": len(result.targets),
            "fetched": result.total_fetched,
            "new": len(result.updates),
            "errors": len(result.errors),
        },
        # Resolved display names live on targets, expose them
        "targets": [
            {"source": t.source, "key": t.key, "label": t.display} for t in result.targets
        ],
        "updates": [u.to_dict() for u in result.updates],
        "errors": [
            {"source": e.source, "target": e.target, "message": e.message}
            for e in result.errors
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
