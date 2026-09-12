"""Redeem the org wallet's whole vault position through KeeperHub (the free path's exit).

    ALMANAK_KEEPERHUB_CHAIN=base_sepolia python scripts/redeem_all.py

On Base Sepolia the demo strategy cannot decide to exit on its own (no Morpho Blue rate to
read), so this uses the same KeeperHub signer and submitter the strategy uses to redeem all
shares: dry run, idempotent broadcast, verified receipt. Also handy to get test USDC back.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from almanak.framework.execution.interfaces import TransactionType, UnsignedTransaction
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import AsyncHTTPProvider, AsyncWeb3

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.demo_targets import demo_targets
from almanak_keeperhub.signer import KeeperHubSigner, work_id_scope
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter

BALANCE_OF = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "a", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]


async def main() -> int:
    targets = demo_targets()
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        sys.exit("KEEPERHUB_API_KEY is not set")
    rpc_url = (
        os.environ.get(f"ALMANAK_{targets.chain.upper()}_RPC_URL")
        or os.environ.get("RPC_URL_BASE")
        or "https://sepolia.base.org"
    )
    client = KeeperHubClient(
        api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com")
    )
    try:
        wallet = await client.wallet_address()
        web3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        vault = web3.eth.contract(address=web3.to_checksum_address(targets.vault), abi=BALANCE_OF)
        shares = int(await vault.functions.balanceOf(web3.to_checksum_address(wallet)).call())
        print(f"vault {targets.vault} on {targets.chain}: {shares} shares owned by {wallet}")
        if shares == 0:
            print("nothing to redeem")
            return 0
        data = (
            "0x"
            + (
                function_signature_to_4byte_selector("redeem(uint256,address,address)")
                + encode(["uint256", "address", "address"], [shares, wallet, wallet])
            ).hex()
        )
        tx = UnsignedTransaction(
            to=targets.vault,
            value=0,
            data=data,
            chain_id=targets.chain_id,
            gas_limit=300_000,
            nonce=0,
            tx_type=TransactionType.EIP_1559,
            from_address=wallet,
            max_fee_per_gas=0,
            max_priority_fee_per_gas=0,
            metadata={"description": "redeem all shares", "intent_type": "VAULT_REDEEM"},
        )
        simulator = KeeperHubSimulator(client, wallet)
        outcome = await simulator.simulate([tx], targets.chain)
        print(f"dry run: simulated={outcome.simulated} success={outcome.success} gas={outcome.gas_estimates}")
        if not outcome.success:
            print(f"refused: {outcome.revert_reason}")
            return 1
        signer = KeeperHubSigner(client, wallet)
        with work_id_scope(f"redeem-all-{int(time.time())}"):
            signed = await signer.sign(tx, targets.chain)
        submitter = KeeperHubSubmitter(client, rpc_url=rpc_url)
        results = await submitter.submit([signed])
        if not results[0].submitted:
            print(f"refused: {results[0].error}")
            return 1
        receipt = await submitter.get_receipt(results[0].tx_hash, timeout=240)
        execution = submitter.execution_for(results[0].tx_hash)
        print(
            f"redeemed: execution {execution.execution_id} tx {results[0].tx_hash} status={receipt.status} block={receipt.block_number}"
        )
        print(f"{targets.explorer}{results[0].tx_hash}")
        return 0 if receipt.success else 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
