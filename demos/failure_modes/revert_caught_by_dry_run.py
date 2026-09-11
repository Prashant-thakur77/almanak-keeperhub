"""Failure mode 1: a deposit above the wallet's USDC balance is refused by the dry run.

Expected: KeeperHub simulate answers wouldRevert=true, Almanak's SimulationResult
is success=False, and no transaction is broadcast (zero calls to the execute path).
"""

from common import USDC_BASE, VAULT_BASE, banner, calldata, record, run, tx, Stack


async def main() -> None:
    async with Stack() as stack:
        banner(f"dry run: deposit 1,000,000 USDC from {stack.address} (far above balance)")
        deposit = tx(
            VAULT_BASE,
            calldata("deposit(uint256,address)", ["uint256", "address"], [1_000_000 * 10**6, stack.address]),
            stack.address,
            nonce=await stack.nonce(),
            gas_limit=450_000,
            description="deposit 1,000,000 USDC into Moonwell Flagship USDC",
        )
        result = await stack.simulator.simulate([deposit], "base")
        print(f"simulated={result.simulated} success={result.success}")
        print(f"revert_reason={result.revert_reason}")
        assert result.simulated and not result.success, "simulation should have failed"
        record(
            "dry_run_revert",
            to=VAULT_BASE,
            token=USDC_BASE,
            revert_reason=result.revert_reason,
            broadcast=False,
        )
        print("no transaction was broadcast: the strategy tick stops at SIMULATION.")


run(main())
