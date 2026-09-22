"""Execution console: a local page over the proof files, live while the CLI runs.

Reads ``keeperhub-receipts.json`` (every execution), ``docs/receipts.json``
(failure-mode demos) and ``docs/benchmark.json``; polls them; and on request
asks KeeperHub and the chain for a transaction's verdict and evidence.
Standard library only, no build step: one HTML file, one JSON endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("index.html")
EXPLORERS = {
    8453: "https://basescan.org/tx/",
    1: "https://etherscan.io/tx/",
    42161: "https://arbiscan.io/tx/",
    10: "https://optimistic.etherscan.io/tx/",
    137: "https://polygonscan.com/tx/",
    84532: "https://sepolia.basescan.org/tx/",
    11155111: "https://sepolia.etherscan.io/tx/",
}
FAILURE_TITLES = {
    "revert_caught_by_dry_run": "Revert caught by dry run",
    "dry_run_revert": "Revert caught by dry run",
    "duplicate_blocked": "Duplicate blocked by idempotency",
    "cap_refused": "Stablecoin cap refused before signing",
    "duplicate_blocked_by_idempotency": "Duplicate blocked by idempotency",
    "rpc_outage": "Local RPC outage, broadcast still landed",
    "unknown_selector_refused": "Unknown selector refused offline",
    "crash_and_resume": "Crash after broadcast, resumed by a new process",
}
REFUSAL_KINDS = {"revert_caught_by_dry_run", "dry_run_revert", "cap_refused", "unknown_selector_refused"}
TERMINAL_OK = {"completed", "success"}
TERMINAL_BAD = {"failed", "error", "system_error", "cancelled"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _explorer(entry: dict[str, Any]) -> str | None:
    link = entry.get("transaction_link")
    if link:
        return str(link)
    prefix = EXPLORERS.get(int(entry.get("chain_id") or 0))
    tx_hash = entry.get("tx_hash")
    return f"{prefix}{tx_hash}" if prefix and tx_hash else None


def build_state(
    receipts: Path, demo_receipts: Path, benchmark: Path, *, org_wallet: str, base_url: str, chain: str = "base"
) -> dict[str, Any]:
    rows = [e for e in _read_json(receipts, []) if isinstance(e, dict)]
    simulations = sorted(
        (r for r in rows if r.get("type") == "simulation"), key=lambda e: str(e.get("recorded_at", "")), reverse=True
    )
    entries = [r for r in rows if r.get("type") != "simulation"]
    executions = []
    for entry in sorted(entries, key=lambda e: str(e.get("recorded_at", "")), reverse=True):
        status = str(entry.get("status") or "unknown")
        executions.append(
            {
                **entry,
                "status": status,
                "explorer": _explorer(entry),
                "outcome": "ok" if status in TERMINAL_OK else "bad" if status in TERMINAL_BAD else "pending",
            }
        )
    demos = [d for d in _read_json(demo_receipts, []) if isinstance(d, dict)]
    failure_modes = [
        {**d, "title": FAILURE_TITLES.get(str(d.get("kind")), str(d.get("kind", "")).replace("_", " ").capitalize())}
        for d in demos
    ]
    summary = {
        "executions": len(executions),
        "verified": sum(1 for e in executions if e.get("verified") is True),
        "replays": sum(1 for e in executions if e.get("idempotent_replay")),
        "sponsored": sum(1 for e in executions if e.get("sponsored")),
        "in_flight": sum(1 for e in executions if e["outcome"] == "pending"),
        "failed": sum(1 for e in executions if e["outcome"] == "bad"),
        "refused_before_broadcast": sum(1 for d in demos if str(d.get("kind")) in REFUSAL_KINDS),
        "dry_runs": len(simulations),
        "dry_run_refusals": sum(1 for s in simulations if not s.get("success")),
    }
    bench = _read_json(benchmark, None)
    features = _read_json(benchmark.parent / "api-features.json", None)
    conformance = _read_json(benchmark.parent / "conformance.json", None)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "org_wallet": org_wallet,
        "base_url": base_url,
        "chain": chain,
        "summary": summary,
        "executions": executions,
        "simulations": simulations[:50],
        "failure_modes": failure_modes,
        "benchmark": bench if isinstance(bench, dict) else None,
        "api_features": features if isinstance(features, dict) else None,
        "conformance": conformance if isinstance(conformance, dict) else None,
        "sources": {"receipts": str(receipts), "demo_receipts": str(demo_receipts), "benchmark": str(benchmark)},
    }


async def verify_reference(reference: str, receipts: Path, chain_name: str) -> dict[str, Any]:
    """KeeperHub's verdict plus on-chain evidence, as the `verify` command prints it."""
    from almanak_keeperhub.client import DEFAULT_BASE_URL, KeeperHubClient
    from almanak_keeperhub.receipts import ReceiptLog
    from almanak_keeperhub.verify import actor_evidence, valid_reference

    if not valid_reference(reference):
        return {"error": "not a transaction hash or execution id"}
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        return {"error": "KEEPERHUB_API_KEY is not set in the console's environment"}
    client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
    try:
        org_wallet = await client.wallet_address()
        execution_id, tx_hash = reference, None
        if reference.startswith("0x") and len(reference) == 66:
            entry = ReceiptLog(receipts).find_by_hash(reference)
            if entry is None:
                return {"error": f"{reference} is not in {receipts}"}
            execution_id, tx_hash = str(entry["execution_id"]), reference
        status = await client.execution_status(execution_id)
        tx_hash = tx_hash or status.transaction_hash
        result: dict[str, Any] = {
            "execution_id": status.execution_id,
            "status": status.status,
            "sponsored": status.sponsored,
            "transaction_link": status.transaction_link,
            "receipts": [
                {"hash": r.hash, "verified": r.verified, "receipt_status": r.receipt_status} for r in status.receipts
            ],
            "org_wallet": org_wallet,
            "tx_hash": tx_hash,
            "events": [],
        }
        rpc_url = (
            os.environ.get(f"ALMANAK_{chain_name.upper()}_RPC_URL")
            or os.environ.get(f"{chain_name.upper()}_RPC_URL")
            or os.environ.get("RPC_URL_BASE")
        )
        if tx_hash and rpc_url:
            try:
                from web3 import AsyncHTTPProvider, AsyncWeb3

                provider = AsyncHTTPProvider(rpc_url)
                try:
                    receipt = await AsyncWeb3(provider).eth.get_transaction_receipt(tx_hash)  # type: ignore[arg-type]
                finally:
                    await provider.disconnect()
                sender = str(receipt["from"]).lower()
                result["onchain"] = {
                    "sender": sender,
                    "sender_is_org_wallet": sender == org_wallet.lower(),
                    "status": "success" if receipt["status"] == 1 else "reverted",
                    "block": int(receipt["blockNumber"]),
                    "gas_used": int(receipt["gasUsed"]),
                }
                result["events"] = actor_evidence([dict(log) for log in receipt["logs"]], org_wallet)
            except Exception as exc:  # noqa: BLE001 - KeeperHub's verdict stands even if the RPC lags
                result["onchain_error"] = f"{type(exc).__name__}: the RPC could not return this receipt yet"
        return result
    finally:
        await client.aclose()


async def keeper_state(receipts: Path) -> dict[str, Any]:
    """The remembered keeper workflow next to the receipts file, plus its live executions."""
    from almanak_keeperhub.keeper import executions, state_path

    path = state_path(receipts.parent)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"deployed": False, "path": str(path)}
    result: dict[str, Any] = {"deployed": True, "path": str(path), **state, "executions": [], "error": None}
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        result["error"] = "KEEPERHUB_API_KEY is not set in the console's environment"
        return result
    from almanak_keeperhub.client import DEFAULT_BASE_URL, KeeperHubClient

    client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
    try:
        result["executions"] = await executions(client, str(state.get("workflow_id", "")))
    except Exception as exc:  # noqa: BLE001 - shown on the page
        result["error"] = str(exc)
    finally:
        await client.aclose()
    result["exit_guard"] = exit_guard_state(receipts)
    return result


def exit_guard_state(receipts: Path) -> dict[str, Any]:
    """The remembered guarded-exit workflow next to the receipts file, with its manual runs.

    Read from the state file alone: every run is recorded there by `exit-guard run`, with
    the shares decided, the shares KeeperHub observed, and whether the Condition released
    the redeem.
    """
    from almanak_keeperhub.exit_workflow import state_path

    path = state_path(receipts.parent)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"deployed": False, "path": str(path)}
    return {"deployed": True, "path": str(path), **state}


class _Handler(BaseHTTPRequestHandler):
    server: ConsoleServer

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        logger.debug("console: " + fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self._respond(HTTPStatus.OK, body, "text/html; charset=utf-8")
        elif url.path == "/api/state":
            self._json(HTTPStatus.OK, self.server.state())
        elif url.path == "/api/keeper":
            try:
                payload = asyncio.run(keeper_state(self.server.receipts))
            except Exception as exc:  # noqa: BLE001
                payload = {"deployed": False, "error": str(exc)}
            self._json(HTTPStatus.OK, payload)
        elif url.path == "/api/verify":
            reference = parse_qs(url.query).get("ref", [""])[0]
            if not reference:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "ref is required"})
                return
            try:
                payload = asyncio.run(verify_reference(reference, self.server.receipts, self.server.chain_name))
            except Exception as exc:  # noqa: BLE001 - the page shows the error text
                payload = {"error": str(exc)}
            self._json(HTTPStatus.OK, payload)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        self._respond(status, json.dumps(payload).encode(), "application/json")

    def _respond(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *,
        receipts: Path,
        demo_receipts: Path,
        benchmark: Path,
        org_wallet: str,
        base_url: str,
        port: int = 8642,
        host: str = "127.0.0.1",
        chain_name: str = "base",
    ) -> None:
        self.receipts = receipts
        self.demo_receipts = demo_receipts
        self.benchmark = benchmark
        self.org_wallet = org_wallet
        self.base_url = base_url
        self.chain_name = chain_name
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def state(self) -> dict[str, Any]:
        return build_state(
            self.receipts,
            self.demo_receipts,
            self.benchmark,
            org_wallet=self.org_wallet,
            base_url=self.base_url,
            chain=self.chain_name,
        )
