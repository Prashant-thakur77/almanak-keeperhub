"""Live conformance against the KeeperHub deployment in KEEPERHUB_BASE_URL.

Opt in with ALMANAK_KEEPERHUB_LIVE=1 and a KEEPERHUB_API_KEY (mcp:write); everything else
skips. The suite dry-runs freely and broadcasts a handful of zero-cost approvals on Base
Sepolia. With ALMANAK_KEEPERHUB_CONFORMANCE_OUT set, the outcome of every test is written
there as JSON, which the proof workflow publishes to the console.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from almanak_keeperhub.client import ContractCall, KeeperHubClient

CHAIN_ID = 84532
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
VAULT = "0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04"
APPROVE_ABI = [
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    }
]
DEPOSIT_ABI = [
    {
        "type": "function",
        "name": "deposit",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "assets", "type": "uint256"}, {"name": "receiver", "type": "address"}],
        "outputs": [{"name": "shares", "type": "uint256"}],
    }
]

_results: list[dict] = []


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("ALMANAK_KEEPERHUB_LIVE") != "1" or not os.environ.get("KEEPERHUB_API_KEY"):
        skip = pytest.mark.skip(reason="set ALMANAK_KEEPERHUB_LIVE=1 and KEEPERHUB_API_KEY to run against the live API")
        for item in items:
            if "tests/live" in str(item.fspath):
                item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    marker = item.get_closest_marker("doc")
    outcome.get_result().conformance_doc = marker.args[0] if marker else ""


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when != "call" or "tests/live" not in report.nodeid:
        return
    _results.append(
        {
            "test": report.nodeid.split("::", 1)[1],
            "doc": getattr(report, "conformance_doc", ""),
            "outcome": report.outcome,
            "duration_s": round(report.duration, 2),
        }
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    out = os.environ.get("ALMANAK_KEEPERHUB_CONFORMANCE_OUT")
    if not out or not _results:
        return
    summary = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com"),
        "chain_id": CHAIN_ID,
        "passed": sum(r["outcome"] == "passed" for r in _results),
        "failed": sum(r["outcome"] == "failed" for r in _results),
        "tests": _results,
    }
    Path(out).write_text(json.dumps(summary, indent=1) + "\n")


@pytest.fixture
async def client() -> AsyncIterator[KeeperHubClient]:
    c = KeeperHubClient(
        api_key=os.environ["KEEPERHUB_API_KEY"],
        base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com"),
    )
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
async def wallet(client: KeeperHubClient) -> str:
    return await client.wallet_address()


def approve(amount: int = 1) -> ContractCall:
    data = "0x095ea7b3" + VAULT[2:].lower().rjust(64, "0") + format(amount, "x").rjust(64, "0")
    return ContractCall(USDC, CHAIN_ID, "approve", [VAULT, str(amount)], APPROVE_ABI, data=data)


def impossible_deposit(wallet: str) -> ContractCall:
    assets = 10**12 * 10**6
    data = "0x6e553f65" + format(assets, "x").rjust(64, "0") + wallet[2:].lower().rjust(64, "0")
    return ContractCall(VAULT, CHAIN_ID, "deposit", [str(assets), wallet], DEPOSIT_ABI, data=data)


def fresh_key(label: str) -> str:
    return f"conformance-{label}-{int(time.time() * 1000)}"
