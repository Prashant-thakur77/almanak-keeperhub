"""Failure mode 6: the process dies right after broadcast; a fresh process finishes the job.

Almanak checkpoints the transaction hash in its session store the moment a
submitter returns it. If the process crashes before the receipt phase, the
restarted runner asks the submitter for that hash's receipt. A fresh
KeeperHubSubmitter has nothing in memory, so it resumes from
``keeperhub-receipts.json`` (hash -> KeeperHub execution id), polls the
execution to its verified end, and never resends.

Phase 1 runs in a child process that hard-exits after ``submit()``; phase 2 is
this process with a brand-new stack.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from common import CHAIN_NAME, USDC_BASE, VAULT_BASE, Stack, banner, calldata, record, run, tx

APPROVE = calldata("approve(address,uint256)", ["address", "uint256"], [VAULT_BASE, 3])


async def phase_broadcast() -> None:
    async with Stack() as stack:
        signed = await stack.signer.sign(tx(USDC_BASE, APPROVE, stack.address, nonce=0), CHAIN_NAME)
        results = await stack.submitter.submit([signed])
        print(f"BROADCAST {results[0].tx_hash}", flush=True)
        os._exit(0)  # the crash: no settlement, no receipt phase, no clean shutdown


async def main() -> None:
    os.environ.setdefault("ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT", f"demo-{int(time.time())}")
    banner("phase 1: broadcast an approve in a child process, then die before settlement")
    child = subprocess.run(  # noqa: ASYNC221 - the blocking child IS the demo: a process that dies mid-flight
        [sys.executable, __file__, "--phase", "broadcast"], capture_output=True, text=True, env=os.environ, check=False
    )
    line = next((ln for ln in child.stdout.splitlines() if ln.startswith("BROADCAST ")), None)
    if line is None:
        sys.exit(f"child did not broadcast:\n{child.stdout}\n{child.stderr}")
    tx_hash = line.split()[1]
    print(f"child broadcast {tx_hash} and exited with {child.returncode} before any receipt was read")

    banner("phase 2: a fresh process is asked for that hash's receipt (what Almanak's runner does on restart)")
    async with Stack() as stack:
        receipt = await stack.submitter.get_receipt(tx_hash, timeout=180)
        execution = stack.submitter.execution_for(tx_hash)
        print(f"resumed execution {execution.execution_id} from {stack.submitter._receipts.path}")
        print(f"receipt status={receipt.status} block={receipt.block_number} gas_used={receipt.gas_used}")
        assert receipt.success, "resumed execution did not succeed"
        record("crash_and_resume", execution_id=execution.execution_id, tx_hash=tx_hash, resumed=True)
    print("\nno second broadcast was needed: the hash was settled by a process that never sent it.")


if __name__ == "__main__":
    if "--phase" in sys.argv:
        import asyncio

        asyncio.run(phase_broadcast())
    else:
        run(main())
