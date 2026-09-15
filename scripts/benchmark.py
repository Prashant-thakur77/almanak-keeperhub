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
from almanak_keeperhub.demo_targets import demo_targets
from almanak_keeperhub.signer import KeeperHubSigner, work_id_scope
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter

_TARGETS = demo_targets()  # ALMANAK_KEEPERHUB_CHAIN=base (default) or base_sepolia
BASE_CHAIN_ID = _TARGETS.chain_id
USDC_BASE = _TARGETS.usdc
VAULT_BASE = _TARGETS.vault
DOCS = Path(os.environ.get("ALMANAK_KEEPERHUB_BENCHMARK_DIR") or Path(__file__).resolve().parents[1] / "docs")


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


async def main(args: argparse.Namespace) -> dict:
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        sys.exit("KEEPERHUB_API_KEY is not set")
    base_url = os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")
    rpc_url = _TARGETS.rpc
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
        "chain": _TARGETS.chain,
        "chain_id": _TARGETS.chain_id,
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
        outcome = await simulator.simulate([impossible], _TARGETS.chain)
        refusal_latency.append(time.perf_counter() - started)
        refused += int(outcome.simulated and not outcome.success)
    results["refusals"] = {"attempted": args.refusals, "refused_before_broadcast": refused}

    # 2. Dry runs of a valid approve.
    ok, sim_latency, gas = 0, [], []
    approve = tx(USDC_BASE, calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 1]), address)
    for _ in range(args.simulations):
        started = time.perf_counter()
        outcome = await simulator.simulate([approve], _TARGETS.chain)
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
            signed = await signer.sign(approve, _TARGETS.chain)
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
            retry = await signer.sign(approve, _TARGETS.chain)
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

    # 4. Retries at scale: the same work id again, for the first N landed approvals. Every
    #    one must come back as the hash that already landed; a new hash is a double spend.
    retries = min(args.retries, len(hashes))
    replayed, doubled, retry_latency = 0, 0, []
    for i in range(retries):
        with work_id_scope(f"{run_id}-{i}"):
            again = await signer.sign(approve, _TARGETS.chain)
        started = time.perf_counter()
        outcomes = await submitter.submit([again])
        retry_latency.append(time.perf_counter() - started)
        if outcomes[0].submitted and outcomes[0].tx_hash == hashes[i]:
            replayed += 1
        elif outcomes[0].submitted:
            doubled += 1
    results["retries"] = {
        "attempted": retries,
        "replayed_same_hash": replayed,
        "double_broadcasts": doubled,
        "latency_p50_s": round(pct(retry_latency, 50), 2),
    }

    # 5. Crash cycles: a child process broadcasts and dies before settlement; a fresh
    #    submitter here settles the hash from the receipts log, then the same work is
    #    retried and must replay rather than resend.
    resumed, resent, crash_hashes = 0, 0, []
    for i in range(args.crashes):
        work_id = f"{run_id}-crash-{i}"
        tx_hash = _crash_after_broadcast(work_id)
        if tx_hash is None:
            continue
        crash_hashes.append(tx_hash)
        fresh = KeeperHubSubmitter(client, rpc_url=rpc_url)
        receipt = await fresh.get_receipt(tx_hash, timeout=180)
        resumed += int(receipt.success)
        with work_id_scope(work_id):
            again = await signer.sign(approve, _TARGETS.chain)
        outcomes = await fresh.submit([again])
        resent += int(outcomes[0].submitted and outcomes[0].tx_hash != tx_hash)
    results["crashes"] = {
        "attempted": args.crashes,
        "resumed_and_verified_by_a_fresh_process": resumed,
        "resent_after_resume": resent,
        "tx_hashes": crash_hashes,
    }
    results["simulation_latency"] = {
        "p50_s": round(pct(sim_latency + refusal_latency, 50), 2),
        "p95_s": round(pct(sim_latency + refusal_latency, 95), 2),
    }
    results["finished_at"] = datetime.now(UTC).isoformat()
    await client.aclose()
    return results


def write_results(results: dict) -> None:
    DOCS.mkdir(exist_ok=True)
    (DOCS / "benchmark.json").write_text(json.dumps(results, indent=2) + "\n")
    (DOCS / "benchmark.md").write_text(render(results))
    print(render(results))


def _crash_after_broadcast(work_id: str) -> str | None:
    """Broadcast one approve in a child that hard-exits right after submit; return its hash."""
    import subprocess

    child = subprocess.run(  # noqa: S603 - our own script, fixed argv
        [sys.executable, __file__, "--phase", "broadcast", "--work-id", work_id],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )
    line = next((ln for ln in child.stdout.splitlines() if ln.startswith("BROADCAST ")), None)
    if line is None:
        print(f"crash child did not broadcast: {child.stderr[-400:]}", file=sys.stderr)
        return None
    return line.split()[1]


async def phase_broadcast(work_id: str) -> None:
    api_key = os.environ.get("KEEPERHUB_API_KEY") or sys.exit("KEEPERHUB_API_KEY is not set")
    client = KeeperHubClient(
        api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")
    )
    address = await client.wallet_address()
    approve = tx(USDC_BASE, calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 1]), address)
    with work_id_scope(work_id):
        signed = await KeeperHubSigner(client, address).sign(approve, _TARGETS.chain)
    results = await KeeperHubSubmitter(client, rpc_url=_TARGETS.rpc).submit([signed])
    print(f"BROADCAST {results[0].tx_hash}", flush=True)
    os._exit(0)  # the crash: no settlement, no receipt phase, no clean shutdown


def render(r: dict) -> str:
    ex, si, re = r["executions"], r["simulations"], r["refusals"]
    lines = [
        f"# Benchmark ({r['run_id']})",
        "",
        f"KeeperHub: {r['base_url']}  chain: {r.get('chain', 'base')} ({r.get('chain_id', 8453)})  wallet: {r['wallet']}  started: {r['started_at']}",
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
    if r.get("retries", {}).get("attempted"):
        rt = r["retries"]
        lines.append(
            f"| Retries of landed work replayed by idempotency key | {rt['replayed_same_hash']}/{rt['attempted']}, "
            f"{rt['double_broadcasts']} double broadcasts (p50 {rt['latency_p50_s']}s) |"
        )
    if r.get("crashes", {}).get("attempted"):
        cr = r["crashes"]
        lines.append(
            f"| Process killed after broadcast, settled by a fresh process | "
            f"{cr['resumed_and_verified_by_a_fresh_process']}/{cr['attempted']}, {cr['resent_after_resume']} resent |"
        )
    lines += ["", "Transaction hashes:", ""] + [f"- {h}" for h in ex["tx_hashes"]]
    if r.get("crashes", {}).get("tx_hashes"):
        lines += ["", "Crash-cycle hashes:", ""] + [f"- {h}" for h in r["crashes"]["tx_hashes"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refusals", type=int, default=20)
    parser.add_argument("--simulations", type=int, default=10)
    parser.add_argument("--executions", type=int, default=5)
    parser.add_argument("--retries", type=int, default=5, help="retry this many of the landed approvals")
    parser.add_argument("--crashes", type=int, default=0, help="crash-after-broadcast cycles")
    parser.add_argument("--phase", choices=["broadcast"], help=argparse.SUPPRESS)
    parser.add_argument("--work-id", help=argparse.SUPPRESS)
    parsed = parser.parse_args()
    if parsed.phase == "broadcast":
        asyncio.run(phase_broadcast(parsed.work_id))
    write_results(asyncio.run(main(parsed)))
