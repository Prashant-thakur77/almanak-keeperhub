"""Shared helpers for the failure-mode demos.

Every demo builds a real Almanak ``UnsignedTransaction`` and pushes it through
the same KeeperHubSigner / KeeperHubSimulator / KeeperHubSubmitter the gateway
uses, so what the judges see is the integration, not a special code path.

Environment: KEEPERHUB_API_KEY (mcp:write), optional KEEPERHUB_BASE_URL,
RPC_URL_BASE (any Base RPC, used only to read receipt logs).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from almanak.framework.execution.interfaces import TransactionType, UnsignedTransaction
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.demo_targets import demo_targets
from almanak_keeperhub.signer import KeeperHubSigner
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter

_TARGETS = demo_targets()  # ALMANAK_KEEPERHUB_CHAIN=base (default) or base_sepolia
BASE_CHAIN_ID = _TARGETS.chain_id
USDC_BASE = _TARGETS.usdc
VAULT_BASE = _TARGETS.vault  # Moonwell Flagship USDC vault on Base, or your TestVault on Base Sepolia
CHAIN_NAME = _TARGETS.chain
# The demo verdicts are proof, so a fork rehearsal must point this elsewhere (ALMANAK_KEEPERHUB_DEMO_RECEIPTS).
RECEIPTS = Path(
    os.environ.get("ALMANAK_KEEPERHUB_DEMO_RECEIPTS") or Path(__file__).resolve().parents[2] / "docs" / "receipts.json"
)


def calldata(signature: str, types: list[str], args: list[Any]) -> str:
    return "0x" + (function_signature_to_4byte_selector(signature) + encode(types, args)).hex()


def tx(
    to: str, data: str, sender: str, nonce: int, value: int = 0, gas_limit: int = 200_000, **meta: Any
) -> UnsignedTransaction:
    return UnsignedTransaction(
        to=to,
        value=value,
        data=data,
        chain_id=BASE_CHAIN_ID,
        gas_limit=gas_limit,
        nonce=nonce,
        tx_type=TransactionType.EIP_1559,
        from_address=sender,
        max_fee_per_gas=0,
        max_priority_fee_per_gas=0,
        metadata={"description": meta.get("description", ""), "intent_type": meta.get("intent_type", "demo")},
    )


class Stack:
    """Client + the three Almanak interfaces, built exactly like the gateway does."""

    def __init__(self) -> None:
        api_key = os.environ.get("KEEPERHUB_API_KEY")
        if not api_key:
            sys.exit("KEEPERHUB_API_KEY is not set")
        self.client = KeeperHubClient(
            api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")
        )
        self.rpc_url = _TARGETS.rpc  # matched to the chain, never a mainnet RPC for Sepolia hashes
        self.address = ""
        self.signer: KeeperHubSigner
        self.simulator: KeeperHubSimulator
        self.submitter: KeeperHubSubmitter

    async def __aenter__(self) -> Stack:
        self.address = await self.client.wallet_address()
        self.signer = KeeperHubSigner(self.client, self.address)
        self.simulator = KeeperHubSimulator(self.client, self.address)
        self.submitter = KeeperHubSubmitter(self.client, rpc_url=self.rpc_url)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.client.aclose()

    async def nonce(self) -> int:
        from web3 import AsyncHTTPProvider, AsyncWeb3

        web3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
        return await web3.eth.get_transaction_count(web3.to_checksum_address(self.address), "pending")


def record(kind: str, **fields: Any) -> None:
    """Append proof to docs/receipts.json so every claim in the README has a source."""
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(RECEIPTS.read_text()) if RECEIPTS.exists() else []
    entries.append({"kind": kind, **fields})
    RECEIPTS.write_text(json.dumps(entries, indent=2))


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def run(coro: Any) -> None:
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        sys.exit(130)
