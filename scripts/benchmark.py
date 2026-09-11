"""Measured behaviour of the KeeperHub backend: refusals, dry runs, broadcasts, replays, latency.

    python scripts/benchmark.py --refusals 20 --simulations 10 --executions 5

Every execution is a real approve of 1 raw USDC unit (0.000001 USDC) to the demo vault,
so the only cost is gas. Writes docs/benchmark.md and docs/benchmark.json.
Uses the same signer, simulator and submitter the gateway uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from almanak.framework.execution.interfaces import TransactionType, UnsignedTransaction
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.signer import KeeperHubSigner, work_id_scope
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter

BASE_CHAIN_ID = 8453
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
VAULT_BASE = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
DOCS = Path(__file__).resolve().parents[1] / "docs"


def calldata(signature: str, types: list[str], args: list) -> str:
    return "0x" + (function_signature_to_4byte_selector(signature) + encode(types, args)).hex()


def tx(to: str, data: str, sender: str, gas_limit: int = 120_000) -> UnsignedTransaction:
    return UnsignedTransaction(
        to=to,
        value=0,
        data=data,
        chain_id=BASE_CHAIN_ID,
        gas_limit=gas_limit,
        nonce=0,
        tx_type=TransactionType.EIP_1559,
        from_address=sender,
        max_fee_per_gas=0,
        max_priority_fee_per_gas=0,
        metadata={},
    )


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return ordered[index]


async def main(args: argparse.Namespace) -> int:
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        sys.exit("KEEPERHUB_API_KEY is not set")
    base_url = os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")
    rpc_url = os.environ.get("RPC_URL_BASE", "https://mainnet.base.org")
    client = KeeperHubClient(api_key=api_key, base_url=base_url)
    address = await client.wallet_address()
    signer = KeeperHubSigner(client, address)
    simulator = KeeperHubSimulator(client, address)
    submitter = KeeperHubSubmitter(client, rpc_url=rpc_url)
    run_id = f"bench-{int(time.time())}"
    results: dict = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "wallet": address,
    }

    # 1. Refusals: a deposit far above balance must never be broadcast.
    refused, refusal_latency = 0, []
    for _ in range(args.refusals):
        impossible = tx(
            VAULT_BASE,
            calldata("deposit(uint256,address)", ["uint256", "address"], [10**12 * 10**6, address]),
            address,
            gas_limit=450_000,
        )
        started = time.perf_counter()
        outcome = await simulator.simulate([impossible], "base")
        refusal_latency.append(time.perf_counter() - started)
        refused += int(outcome.simulated and not outcome.success)
    results["refusals"] = {"attempted": args.refusals, "refused_before_broadcast": refused}

    # 2. Dry runs of a valid approve.
    ok, sim_latency, gas = 0, [], []
    approve = tx(USDC_BASE, calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 1]), address)
    for _ in range(args.simulations):
        started = time.perf_counter()
        outcome = await simulator.simulate([approve], "base")
        sim_latency.append(time.perf_counter() - started)
        ok += int(outcome.success)
        if outcome.gas_estimates:
            gas.append(outcome.gas_estimates[0])
    results["simulations"] = {
        "attempted": args.simulations,
        "succeeded": ok,
        "gas_estimate_median": int(statistics.median(gas)) if gas else None,
    }

    # 3. Real broadcasts: each one is its own piece of work; then one deliberate retry.
    landed, exec_latency, hashes = 0, [], []
    for i in range(args.executions):
        with work_id_scope(f"{run_id}-{i}"):
            signed = await signer.sign(approve, "base")
        started = time.perf_counter()
        outcomes = await submitter.submit([signed])
        if outcomes[0].submitted:
            receipt = await submitter.get_receipt(outcomes[0].tx_hash, timeout=180)
            exec_latency.append(time.perf_counter() - started)
            landed += int(receipt.success)
            hashes.append(outcomes[0].tx_hash)
    replayed = None
    if args.executions:
        with work_id_scope(f"{run_id}-0"):
            retry = await signer.sign(approve, "base")
        started = time.perf_counter()
        outcomes = await submitter.submit([retry])
        replay_latency = time.perf_counter() - started
        replayed = outcomes[0].tx_hash == hashes[0] if hashes else None
        results["retry"] = {"same_work_replayed_not_resent": replayed, "latency_s": round(replay_latency, 2)}
    results["executions"] = {
        "attempted": args.executions,
        "landed_and_verified": landed,
        "tx_hashes": hashes,
        "latency_p50_s": round(pct(exec_latency, 50), 2),
        "latency_p95_s": round(pct(exec_latency, 95), 2),
    }
    results["simulation_latency"] = {
        "p50_s": round(pct(sim_latency + refusal_latency, 50), 2),
        "p95_s": round(pct(sim_latency + refusal_latency, 95), 2),
    }
    results["finished_at"] = datetime.now(UTC).isoformat()
    await client.aclose()

    DOCS.mkdir(exist_ok=True)
    (DOCS / "benchmark.json").write_text(json.dumps(results, indent=2) + "\n")
    (DOCS / "benchmark.md").write_text(render(results))
    print(render(results))
    return 0


def render(r: dict) -> str:
    ex, si, re = r["executions"], r["simulations"], r["refusals"]
    lines = [
        f"# Benchmark ({r['run_id']})",
        "",
        f"KeeperHub: {r['base_url']}  wallet: {r['wallet']}  started: {r['started_at']}",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Impossible deposits refused before broadcast | {re['refused_before_broadcast']}/{re['attempted']} |",
        f"| Valid approve dry runs succeeded | {si['succeeded']}/{si['attempted']} (median gas estimate {si['gas_estimate_median']}) |",
        f"| Real approvals landed and verified | {ex['landed_and_verified']}/{ex['attempted']} |",
        f"| Broadcast + verified receipt latency | p50 {ex['latency_p50_s']}s, p95 {ex['latency_p95_s']}s |",
        f"| Simulate latency | p50 {r['simulation_latency']['p50_s']}s, p95 {r['simulation_latency']['p95_s']}s |",
    ]
    if "retry" in r:
        lines.append(
            f"| Retry of already-landed work replayed, not resent | {r['retry']['same_work_replayed_not_resent']} ({r['retry']['latency_s']}s) |"
        )
    lines += ["", "Transaction hashes:", ""] + [f"- {h}" for h in ex["tx_hashes"]] + [""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refusals", type=int, default=20)
    parser.add_argument("--simulations", type=int, default=10)
    parser.add_argument("--executions", type=int, default=5)
    sys.exit(asyncio.run(main(parser.parse_args())))
