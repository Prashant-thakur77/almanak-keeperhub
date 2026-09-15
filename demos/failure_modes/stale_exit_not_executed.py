"""Failure mode 7: an exit decided on a stale snapshot is not executed.

The strategy decided to redeem N shares; by the time the write would go out the
position is gone (the keeper compounded, another process exited, a retry replays a
landed exit). Sent as KeeperHub's check-and-execute, the redeem is guarded by
KeeperHub reading ``balanceOf(wallet)`` itself right before the write: the guard
fails, the answer is ``executed: false`` with the observed balance, and nothing is
broadcast. The same request with a met guard is what ``almanak-keeperhub exit`` does.
"""

from common import _TARGETS as targets
from common import Stack, banner, record, run

from almanak_keeperhub.guarded_exit import GuardedExit, current_shares, run_guarded_exit


async def main() -> None:
    async with Stack() as stack:
        held = await current_shares(targets.rpc, targets.vault, stack.address)
        stale = max(held + 1, 1)  # one share more than the position holds: a decision the chain has outrun
        banner(f"exit {stale} shares while the wallet holds {held}: KeeperHub checks before it redeems")
        exit_ = GuardedExit(vault=targets.vault, chain_id=targets.chain_id, wallet=stack.address, shares=stale)
        outcome = await run_guarded_exit(stack.client, exit_, receipts=stack.submitter._receipts)
        if outcome.executed:
            raise SystemExit(f"KeeperHub redeemed on a failed guard: {outcome.execution_id}")
        c = outcome.condition
        print(f"not executed: balanceOf = {c.observed_value}, guard {c.operator} {c.target_value} is false")
        record(
            "stale_exit_not_executed",
            shares_requested=stale,
            shares_observed_by_keeperhub=c.observed_value,
            broadcast=False,
        )
    print("\nthe decision was stale; KeeperHub noticed at execution time, and no transaction was sent.")


run(main())
