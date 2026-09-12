"""Failure mode 4: the strategy's own RPC is dead, the broadcast still lands and settles.

Almanak's public-mempool submitter needs a working RPC to send. With KeeperHub
the broadcast goes through KeeperHub's RPC pool; only the local receipt-log
fetch depends on our RPC. Point RPC_URL_BASE at a dead endpoint: the transaction
lands (KeeperHub verifies it), and get_receipt reports exactly which side failed.
"""

import os
import time

from common import CHAIN_NAME, USDC_BASE, VAULT_BASE, Stack, banner, calldata, record, run, tx


async def main() -> None:
    # Each demo run is new work; within the run, attempts share the key.
    os.environ.setdefault("ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT", f"demo-{int(time.time())}")
    os.environ["RPC_URL_BASE"] = "http://127.0.0.1:9"  # nothing listens here
    async with Stack() as stack:
        banner("broadcast approve(vault, 0.01 USDC) with a dead local RPC")
        approve = tx(
            USDC_BASE,
            calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 10_001]),
            stack.address,
            nonce=int(os.environ.get("DEMO_NONCE", "0")),  # nonce only salts the key; KeeperHub assigns the real one
            gas_limit=80_000,
        )
        signed = await stack.signer.sign(approve, CHAIN_NAME)
        results = await stack.submitter.submit([signed])
        execution = stack.submitter.execution_for(results[0].tx_hash)
        print(
            f"KeeperHub executionId={execution.execution_id} tx={results[0].tx_hash} (broadcast did not need our RPC)"
        )
        os.environ["ALMANAK_KEEPERHUB_RECEIPT_ATTEMPTS"] = "2"  # do not wait two minutes on a dead RPC in a demo
        receipt = await stack.submitter.get_receipt(results[0].tx_hash, timeout=120)
        print(
            f"local RPC never answered; settled from KeeperHub's verified receipt: block={receipt.block_number} "
            f"gas_used={receipt.gas_used} logs={len(receipt.logs)} (logs need an RPC)"
        )
        status = await stack.client.wait_for_terminal(execution.execution_id, timeout_seconds=120)
        print(f"KeeperHub verified receipt: status={status.status} verified={[r.verified for r in status.receipts]}")
        record(
            "rpc_outage",
            execution_id=execution.execution_id,
            tx_hash=results[0].tx_hash,
            keeperhub_status=status.status,
            settled_from="keeperhub verified receipt",
        )


run(main())
