"""Freeze the console into a static site.

The live console reads three files and asks KeeperHub for evidence on demand.
The export writes the same page next to frozen copies of everything it would
have fetched, including KeeperHub's verdict and the decoded events for every
execution, so the page works from GitHub Pages with nothing behind it and a
reader can open Inspect on any row and see the same evidence a running
console shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from almanak_keeperhub.console.server import PAGE, build_state, keeper_state, verify_reference

logger = logging.getLogger(__name__)

SNAPSHOT_TAG = "<script>window.__CONSOLE_SNAPSHOT__ = true;</script>\n<script>"


async def collect_site(
    *,
    receipts: Path,
    demo_receipts: Path,
    benchmark: Path,
    org_wallet: str,
    base_url: str,
    chain: str,
    verify: bool = True,
) -> dict[str, Any]:
    """Everything the page fetches, gathered once: the state, the keeper, and KeeperHub's
    evidence for each execution."""
    state = build_state(receipts, demo_receipts, benchmark, org_wallet=org_wallet, base_url=base_url, chain=chain)
    state["snapshot_at"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    # The page shows these as provenance; on a public site the local paths mean nothing.
    state["sources"] = {
        "receipts": receipts.name,
        "demo_receipts": demo_receipts.name,
        "benchmark": benchmark.name,
    }
    keeper = await keeper_state(receipts)

    evidence: dict[str, Any] = {}
    if verify:
        for execution in state["executions"]:
            ref = execution.get("execution_id") or execution.get("tx_hash")
            if not ref:
                continue
            try:
                evidence[str(ref)] = await verify_reference(str(ref), receipts, chain)
            except Exception as exc:  # noqa: BLE001 - one bad row must not sink the export
                evidence[str(ref)] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"state": state, "keeper": keeper, "evidence": evidence}


def write_site(out_dir: Path, collected: dict[str, Any]) -> dict[str, Any]:
    data = out_dir / "console-data"
    (data / "verify").mkdir(parents=True, exist_ok=True)
    (data / "state.json").write_text(json.dumps(collected["state"], indent=1))
    (data / "keeper.json").write_text(json.dumps(collected["keeper"], indent=1))
    for ref, evidence in collected["evidence"].items():
        (data / "verify" / f"{ref}.json").write_text(json.dumps(evidence, indent=1))
    (out_dir / "index.html").write_text(PAGE.read_text().replace("<script>", SNAPSHOT_TAG, 1))
    (out_dir / ".nojekyll").write_text("")
    return {
        "out_dir": str(out_dir),
        "executions": len(collected["state"]["executions"]),
        "evidence_files": len(collected["evidence"]),
        "snapshot_at": collected["state"]["snapshot_at"],
    }


async def export_site(out_dir: Path, **kwargs: Any) -> dict[str, Any]:
    collected = await collect_site(**kwargs)
    return await asyncio.to_thread(write_site, out_dir, collected)


def export_site_sync(out_dir: Path, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(export_site(out_dir, **kwargs))
