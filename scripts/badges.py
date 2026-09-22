"""Shields.io endpoint badges from the console state, written by the proof runner.

    python scripts/badges.py docs/console/console-data/state.json docs/badges

Each file is the endpoint schema (https://shields.io/badges/endpoint-badge); the README
points shields at them on the published site, so the numbers in the badges are the numbers
on the console, refreshed by the same runner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

GREEN = "1a7f4b"
AMBER = "b7791f"
GREY = "5b6673"


def badges(state: dict) -> dict[str, dict]:
    summary = state.get("summary") or {}
    conformance = state.get("conformance") or {}
    executions = int(summary.get("executions", 0))
    verified = int(summary.get("verified", 0))
    passed = int(conformance.get("passed", 0))
    failed = int(conformance.get("failed", 0))
    snapshot = str(state.get("snapshot_at") or "")
    return {
        "executions": {"label": "executions on production", "message": str(executions), "color": GREEN},
        "verified": {
            "label": "verified by KeeperHub",
            "message": f"{verified} of {executions}",
            "color": GREEN if verified == executions else AMBER,
        },
        "conformance": {
            "label": "live conformance",
            "message": f"{passed} of {passed + failed} passed",
            "color": GREEN if failed == 0 and passed else AMBER,
        },
        "refreshed": {"label": "proof refreshed", "message": snapshot or "unknown", "color": GREY},
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    state = json.loads(Path(argv[1]).read_text())
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, badge in badges(state).items():
        (out / f"{name}.json").write_text(json.dumps({"schemaVersion": 1, **badge}) + "\n")
    print(f"{len(badges(state))} badges written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
