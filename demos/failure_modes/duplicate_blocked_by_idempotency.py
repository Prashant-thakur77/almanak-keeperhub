"""Failure mode 2: the zombie-position incident, prevented.

almanak/framework/execution/nonce_recovery.py describes a mainnet run where a
transaction landed, the framework believed it failed, retried, and minted a
duplicate. Here the same compiled transaction is submitted twice on purpose
(simulating a crash-and-rerun). KeeperHub replays the first execution instead
of broadcasting again: same executionId, same hash, idempotentReplay=true.

Moves 0.01 USDC approve allowance (approve(vault, 10000)), harmless and cheap.
"""

import os
import time

from common import CHAIN_NAME, USDC_BASE, VAULT_BASE, Stack, banner, calldata, record, run, tx


async def main() -> None:
    # Each demo run is new work; within the run, attempts share the key.
    os.environ.setdefault("ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT", f"demo-{int(time.time())}")
    async with Stack() as stack:
        nonce = await stack.nonce()
        approve = tx(
            USDC_BASE,
            calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 10_000]),
            stack.address,
            nonce=nonce,
            gas_limit=80_000,
            description="approve 0.01 USDC to the vault",
        )
        banner("attempt 1: broadcast approve through KeeperHub")
        first = await stack.signer.sign(approve, CHAIN_NAME)
        results_1 = await stack.submitter.submit([first])
        exec_1 = stack.submitter.execution_for(results_1[0].tx_hash)
        print(
            f"executionId={exec_1.execution_id} tx={results_1[0].tx_hash} replay={getattr(exec_1, 'idempotent_replay', False)}"
        )

        banner("attempt 2: 'framework retried the same intent' -> identical work, identical key")
        approve.nonce = nonce + 1  # Almanak assigns a fresh nonce per attempt; the key must not move with it
        second = await stack.signer.sign(approve, CHAIN_NAME)
        assert second.idempotency_key == first.idempotency_key
        results_2 = await stack.submitter.submit([second])
        exec_2 = stack.submitter.execution_for(results_2[0].tx_hash)
        print(
            f"executionId={exec_2.execution_id} tx={results_2[0].tx_hash} replay={getattr(exec_2, 'idempotent_replay', False)}"
        )

        assert results_1[0].tx_hash == results_2[0].tx_hash, "second attempt must not produce a new transaction"
        receipt = await stack.submitter.get_receipt(results_1[0].tx_hash, timeout=180)
        print(f"one transaction on chain: block={receipt.block_number} status={receipt.status}")
        record(
            "duplicate_blocked",
            idempotency_key=first.idempotency_key,
            execution_id=exec_1.execution_id,
            tx_hash=results_1[0].tx_hash,
            second_attempt_replayed=getattr(exec_2, "idempotent_replay", None),
        )


run(main())
