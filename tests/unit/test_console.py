"""The execution console reads the proof files and serves them, never raw JSON dumps."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
import respx

from almanak_keeperhub.console.server import ConsoleServer, build_state

BASE = "https://app.keeperhub.com"
ORG = "0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9"


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def proof_files(tmp_path: Path) -> dict[str, Path]:
    receipts = _write(
        tmp_path / "strategy" / "keeperhub-receipts.json",
        [
            {
                "execution_id": "e1",
                "recorded_at": "2026-09-12T10:00:00+00:00",
                "chain_id": 8453,
                "to": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "function": "approve",
                "tx_hash": "0xaaa",
                "status": "completed",
                "verified": True,
                "sponsored": True,
                "idempotent_replay": False,
                "transaction_link": "https://basescan.org/tx/0xaaa",
            },
            {
                "execution_id": "e2",
                "recorded_at": "2026-09-12T10:00:30+00:00",
                "chain_id": 8453,
                "to": "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca",
                "function": "deposit",
                "tx_hash": "0xbbb",
                "status": "completed",
                "verified": True,
                "idempotent_replay": True,
            },
            {
                "type": "simulation",
                "recorded_at": "2026-09-12T09:59:00+00:00",
                "chain_id": 8453,
                "to": "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca",
                "function": "deposit",
                "success": False,
                "would_revert": True,
                "error": "balance",
            },
            {
                "type": "simulation",
                "recorded_at": "2026-09-12T09:59:30+00:00",
                "chain_id": 8453,
                "to": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "function": "approve",
                "success": True,
                "gas_estimate": 55000,
            },
            {
                "execution_id": "e3",
                "recorded_at": "2026-09-12T10:01:00+00:00",
                "chain_id": 8453,
                "to": "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca",
                "function": "deposit",
                "tx_hash": "0xccc",
                "status": "unconfirmed",
            },
        ],
    )
    demos = _write(
        tmp_path / "docs" / "receipts.json",
        [
            {"kind": "revert_caught_by_dry_run", "revert_reason": "insufficient balance", "broadcasts": 0},
            {"kind": "cap_refused", "error": "exceeds the 100.0 USD per-transaction limit"},
            {"kind": "crash_and_resume", "execution_id": "e9", "tx_hash": "0x999", "resumed": True},
        ],
    )
    benchmark = _write(
        tmp_path / "docs" / "benchmark.json",
        {
            "run_id": "bench-1",
            "refusals": {"attempted": 20, "refused_before_broadcast": 20},
            "simulations": {"attempted": 10, "succeeded": 10, "gas_estimate_median": 55783},
            "executions": {
                "attempted": 5,
                "landed_and_verified": 5,
                "tx_hashes": ["0x1"],
                "latency_p50_s": 12.1,
                "latency_p95_s": 20.4,
            },
            "simulation_latency": {"p50_s": 0.8, "p95_s": 1.9},
            "retry": {"same_work_replayed_not_resent": True, "latency_s": 0.4},
        },
    )
    return {"receipts": receipts, "demos": demos, "benchmark": benchmark}


def test_state_aggregates_counts_and_orders_newest_first(proof_files: dict[str, Path]) -> None:
    state = build_state(
        proof_files["receipts"],
        proof_files["demos"],
        proof_files["benchmark"],
        org_wallet="0xOrg",
        base_url="https://app.keeperhub.com",
    )

    assert state["summary"] == {
        "executions": 3,
        "verified": 2,
        "replays": 1,
        "sponsored": 1,
        "in_flight": 1,
        "failed": 0,
        "refused_before_broadcast": 2,
        "dry_runs": 2,
        "dry_run_refusals": 1,
    }
    assert [s["function"] for s in state["simulations"]] == ["approve", "deposit"]  # newest first
    assert state["simulations"][1]["would_revert"] is True
    assert [e["execution_id"] for e in state["executions"]] == ["e3", "e2", "e1"]
    assert state["executions"][0]["explorer"] == "https://basescan.org/tx/0xccc"  # built when the file has no link
    assert state["failure_modes"][0]["kind"] == "revert_caught_by_dry_run"
    assert state["failure_modes"][0]["title"] == "Revert caught by dry run"
    assert state["benchmark"]["executions"]["landed_and_verified"] == 5
    assert state["org_wallet"] == "0xOrg"
    assert state["sources"]["receipts"] == str(proof_files["receipts"])


def test_state_survives_missing_files(tmp_path: Path) -> None:
    state = build_state(
        tmp_path / "none.json", tmp_path / "none2.json", tmp_path / "none3.json", org_wallet="", base_url="x"
    )
    assert state["executions"] == []
    assert state["failure_modes"] == []
    assert state["benchmark"] is None
    assert state["summary"]["executions"] == 0


def test_server_serves_page_and_state(proof_files: dict[str, Path]) -> None:
    server = ConsoleServer(
        receipts=proof_files["receipts"],
        demo_receipts=proof_files["demos"],
        benchmark=proof_files["benchmark"],
        org_wallet="0xOrg",
        base_url="https://app.keeperhub.com",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        page = httpx.get(f"{base}/")
        assert page.status_code == 200
        assert "execution console" in page.text.lower()
        assert "text/html" in page.headers["content-type"]
        state = httpx.get(f"{base}/api/state").json()
        assert state["summary"]["executions"] == 3
        missing = httpx.get(f"{base}/nope")
        assert missing.status_code == 404
    finally:
        server.shutdown()


async def test_export_writes_a_self_contained_snapshot(strategy_dir: Path, tmp_path: Path) -> None:
    """The static site carries the page, the state, the keeper and one evidence file per execution."""
    from almanak_keeperhub.console.export import export_site

    out = tmp_path / "site"
    with respx.mock(base_url=BASE) as mock:
        mock.get("/api/workflows/7clo/executions").mock(return_value=httpx.Response(200, json={"executions": []}))
        mock.get("/api/user").mock(return_value=httpx.Response(200, json={"walletAddress": ORG}))
        mock.get(path__regex=r"/api/execute/.*/status").mock(
            return_value=httpx.Response(
                200, json={"status": "completed", "sponsored": True, "receipts": [{"verified": True}], "result": {}}
            )
        )
        summary = await export_site(
            out,
            receipts=strategy_dir / "keeperhub-receipts.json",
            demo_receipts=strategy_dir / "missing-demos.json",
            benchmark=strategy_dir / "missing-benchmark.json",
            org_wallet=ORG,
            base_url=BASE,
            chain="base_sepolia",
        )

    assert summary["executions"] == 2
    assert summary["evidence_files"] == 2
    page = (out / "index.html").read_text()
    assert "window.__CONSOLE_SNAPSHOT__ = true" in page
    state = json.loads((out / "console-data" / "state.json").read_text())
    assert state["snapshot_at"].endswith("UTC")
    assert state["sources"]["receipts"] == "keeperhub-receipts.json"  # no local paths leak onto the site
    assert sorted(p.name for p in (out / "console-data" / "verify").iterdir()) == ["au5z.json", "az13.json"]
    assert (out / ".nojekyll").exists()
