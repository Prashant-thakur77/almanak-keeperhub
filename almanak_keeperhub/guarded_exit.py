"""A vault exit that KeeperHub re-checks at execution time.

Almanak decides to exit on a snapshot of the position. Between that decision and the
broadcast the position can change: the keeper compounded, another process redeemed, a
retry is replaying an exit that already happened. ``POST /api/execute/check-and-execute``
lets KeeperHub read the vault balance right before the write and run the redeem only if
the balance still covers it, so a stale decision comes back ``executed: false`` with the
observed value instead of a broadcast that reverts or, worse, lands on a different position.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from almanak_keeperhub.client import ContractCall, GuardedOutcome, KeeperHubClient
from almanak_keeperhub.receipts import ReceiptLog

logger = logging.getLogger(__name__)

BALANCE_OF_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]
REDEEM_ABI = [
    {
        "type": "function",
        "name": "redeem",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "shares", "type": "uint256"},
            {"name": "receiver", "type": "address"},
            {"name": "owner", "type": "address"},
        ],
        "outputs": [{"name": "assets", "type": "uint256"}],
    }
]


@dataclass(frozen=True)
class GuardedExit:
    vault: str
    chain_id: int
    wallet: str
    shares: int
    work_id: str = ""  # the decision this exit carries out; a retry of it reuses the id, a new decision does not

    @property
    def check(self) -> ContractCall:
        return ContractCall(self.vault, self.chain_id, "balanceOf", [self.wallet], BALANCE_OF_ABI)

    @property
    def action(self) -> ContractCall:
        selector = function_signature_to_4byte_selector("redeem(uint256,address,address)")
        data = (
            "0x" + (selector + encode(["uint256", "address", "address"], [self.shares, self.wallet, self.wallet])).hex()
        )
        return ContractCall(
            self.vault, self.chain_id, "redeem", [str(self.shares), self.wallet, self.wallet], REDEEM_ABI, data=data
        )

    def idempotency_key(self) -> str:
        """Identifies one exit decision. The guard, not this key, is what prevents a second redeem:
        two exits of the same size within KeeperHub's replay window are different decisions
        (the position was rebuilt in between), so the decision id is part of the key."""
        import hashlib

        parts = [
            "guarded-exit/v2",
            str(self.chain_id),
            self.wallet.lower(),
            self.vault.lower(),
            str(self.shares),
            self.work_id,
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


async def run_guarded_exit(
    client: KeeperHubClient,
    exit_: GuardedExit,
    *,
    simulate: bool = False,
    receipts: ReceiptLog | None = None,
    settle_timeout: float = 240.0,
) -> GuardedOutcome:
    """Ask KeeperHub to redeem ``shares`` only if the wallet still holds at least that many.

    One idempotency key per decision (``exit_.work_id``), so a retry of the same decision replays
    rather than redeems twice; the guard covers everything else. Records the execution in the
    receipts log like any other broadcast.
    """
    outcome = await client.check_and_execute(
        check=exit_.check,
        operator="gte",
        value=str(exit_.shares),
        action=exit_.action,
        idempotency_key=None if simulate else exit_.idempotency_key(),
        simulate=simulate,
    )
    log = receipts or ReceiptLog()
    if simulate:
        log.record_simulation(
            chain_id=exit_.chain_id,
            to=exit_.vault,
            function="redeem",
            success=outcome.executed,
            would_revert=bool(outcome.raw.get("wouldRevert")),
            guarded=True,
            condition=outcome.condition.__dict__,
            error=None if outcome.executed else str(outcome.raw.get("revertReason") or "condition not met"),
        )
        return outcome
    if not outcome.executed:
        log.record_simulation(
            chain_id=exit_.chain_id,
            to=exit_.vault,
            function="redeem",
            success=False,
            would_revert=False,
            guarded=True,
            condition=outcome.condition.__dict__,
            error=(
                f"guard not met: vault balance {outcome.condition.observed_value} "
                f"{outcome.condition.operator} {outcome.condition.target_value} is false; nothing broadcast"
            ),
        )
        return outcome
    assert outcome.execution_id
    log.record(
        outcome.execution_id,
        chain_id=exit_.chain_id,
        to=exit_.vault,
        function="redeem",
        tx_hash=outcome.transaction_hash,
        status=outcome.status,
        idempotency_key=exit_.idempotency_key(),
        idempotent_replay=outcome.idempotent_replay,
        guarded=True,
        condition=outcome.condition.__dict__,
    )
    status = await client.wait_for_terminal(outcome.execution_id, timeout_seconds=settle_timeout)
    log.update(
        outcome.execution_id,
        status=status.status,
        tx_hash=status.transaction_hash or outcome.transaction_hash,
        transaction_link=status.transaction_link,
        verified=all(r.verified for r in status.receipts) if status.receipts else None,
        receipt_status=[r.receipt_status for r in status.receipts],
        sponsored=status.sponsored,
    )
    return GuardedOutcome(
        executed=True,
        condition=outcome.condition,
        execution_id=outcome.execution_id,
        status=status.status,
        transaction_hash=status.transaction_hash or outcome.transaction_hash,
        idempotent_replay=outcome.idempotent_replay,
        raw={**outcome.raw, "settled": status.raw},
    )


async def current_shares(rpc_url: str, vault: str, wallet: str) -> int:
    from web3 import AsyncHTTPProvider, AsyncWeb3

    provider = AsyncHTTPProvider(rpc_url)
    try:
        web3 = AsyncWeb3(provider)
        contract = web3.eth.contract(address=web3.to_checksum_address(vault), abi=BALANCE_OF_ABI)
        return int(await contract.functions.balanceOf(web3.to_checksum_address(wallet)).call())
    finally:
        await provider.disconnect()


def describe(outcome: GuardedOutcome, exit_: GuardedExit, started: float) -> dict[str, Any]:
    c = outcome.condition
    return {
        "executed": outcome.executed,
        "guard": f"balanceOf({exit_.wallet}) {c.operator} {c.target_value}",
        "observed": c.observed_value,
        "execution_id": outcome.execution_id,
        "tx_hash": outcome.transaction_hash,
        "status": outcome.status,
        "idempotent_replay": outcome.idempotent_replay,
        "seconds": round(time.perf_counter() - started, 2),
    }
