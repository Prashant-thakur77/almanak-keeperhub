"""Reproduce every finding in docs/keeperhub-feedback.md in one command, expected vs actual.

    python scripts/verify_api_notes.py            # against KEEPERHUB_BASE_URL (default app.keeperhub.com)

Read-only except finding 1, which sends a simulate (no broadcast). Needs KEEPERHUB_API_KEY.
A finding that no longer reproduces is reported as FIXED so the notes never go stale.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

BASE = os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com").rstrip("/")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
VAULT_BASE = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"


async def finding_1_no_raw_calldata(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """contract-call has no `data` input: calldata must be decoded client-side."""
    body = {
        "contractAddress": USDC_BASE,
        "chainId": 8453,
        "data": "0x095ea7b3" + VAULT_BASE[2:].lower().rjust(64, "0") + "1".rjust(64, "0"),
        "simulate": True,
    }
    response = await client.post("/api/execute/contract-call", json=body)
    text = response.text[:160].replace("\n", " ")
    expected = "before KeeperHub#2449 deploys: 400 functionName required; after: 200, the calldata dry-runs"
    actual = f"HTTP {response.status_code}: {text}"
    if response.status_code >= 400 and "functionName" in response.text:
        verdict = "REPRODUCED (fix merged upstream, not deployed yet)"
    elif response.status_code == 200:
        verdict = "FIXED UPSTREAM BY THIS PROJECT, LIVE ON PRODUCTION"
    else:
        verdict = "CHANGED?"
    return expected, actual, verdict


async def finding_2_no_sequence_dry_run(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """A bundle's second call cannot be dry-run against the first's effects (fixed upstream in KeeperHub#2452)."""
    body = {
        "chainId": 8453,
        "simulate": True,
        "calls": [
            {"contractAddress": USDC_BASE, "functionName": "approve", "functionArgs": json.dumps([VAULT_BASE, "1"])},
            {"contractAddress": USDC_BASE, "functionName": "approve", "functionArgs": json.dumps([VAULT_BASE, "2"])},
        ],
    }
    response = await client.post("/api/execute/contract-call", json=body)
    text = response.text[:160].replace("\n", " ")
    expected = "before KeeperHub#2452 deploys: 400 contractAddress required; after: results[] with one entry per call"
    actual = f"HTTP {response.status_code}: {text}"
    if response.status_code == 400 and "contractAddress" in response.text:
        verdict = "REPRODUCED (fix merged upstream, not deployed yet)"
    elif "results" in response.text:
        verdict = "FIXED UPSTREAM BY THIS PROJECT, LIVE ON PRODUCTION"
    else:
        verdict = "CHANGED?"
    return expected, actual, verdict


async def finding_5_docs_redirect(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """The MCP guide URL printed in the DoraHacks brief redirects."""
    response = await httpx.AsyncClient(follow_redirects=False, timeout=20).get(
        "https://docs.keeperhub.com/ai-tools/mcp-server"
    )
    expected = "308 -> /agent/mcp-server"
    actual = f"HTTP {response.status_code} -> {response.headers.get('location', '')}"
    verdict = "REPRODUCED" if response.status_code in (301, 302, 307, 308) else "FIXED?"
    return expected, actual, verdict


async def finding_6_chain_field_precedence(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """contract-call prefers `network` over `chainId` when both are sent (docs say so); check it holds."""
    body = {
        "contractAddress": USDC_BASE,
        "chainId": 8453,
        "network": "11155111",
        "functionName": "decimals",
        "functionArgs": "[]",
        "abi": json.dumps(
            [
                {
                    "type": "function",
                    "name": "decimals",
                    "stateMutability": "view",
                    "inputs": [],
                    "outputs": [{"type": "uint8"}],
                }
            ]
        ),
    }
    response = await client.post("/api/execute/contract-call", json=body)
    text = response.text[:120].replace("\n", " ")
    expected = "read runs on Sepolia (network wins): error or 6 from a different contract, not Base USDC's 6"
    actual = f"HTTP {response.status_code}: {text}"
    # Base USDC is not deployed at that address on Sepolia; a Sepolia read fails or returns junk.
    verdict = "REPRODUCED" if response.status_code >= 400 or '"result":"6"' not in response.text else "UNCLEAR"
    return expected, actual, verdict


async def finding_spend_cap_endpoint(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """GET /api/analytics/spend-cap is documented for API keys; confirm it answers."""
    response = await client.get("/api/analytics/spend-cap")
    expected = "200 with effectiveDailyCapWei"
    actual = f"HTTP {response.status_code}: {response.text[:120]}"
    verdict = "OK" if response.status_code == 200 and "effectiveDailyCapWei" in response.text else "DIFFERS"
    return expected, actual, verdict


FINDINGS = [
    ("1", "no raw-calldata write on contract-call", finding_1_no_raw_calldata),
    ("2", "no dry run of a call sequence against carried state", finding_2_no_sequence_dry_run),
    ("5", "MCP guide URL in the brief redirects", finding_5_docs_redirect),
    ("6", "network vs chainId precedence on contract-call", finding_6_chain_field_precedence),
    ("+", "spend-cap endpoint reachable with an API key", finding_spend_cap_endpoint),
]


async def main() -> int:
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        sys.exit("KEEPERHUB_API_KEY is not set")
    async with httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {api_key}"}, timeout=60) as client:
        print(f"KeeperHub: {BASE}\n")
        print("| # | Finding | Expected | Actual | Verdict |\n|---|---|---|---|---|")
        for number, title, probe in FINDINGS:
            try:
                expected, actual, verdict = await probe(client)
            except Exception as exc:  # noqa: BLE001 - report, never crash the table
                expected, actual, verdict = "", f"error: {exc}", "ERROR"
            print(f"| {number} | {title} | {expected} | {actual.replace('|', '/')} | {verdict} |")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
