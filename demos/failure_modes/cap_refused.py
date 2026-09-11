"""Failure mode 3: a 150 USDC transfer is refused by KeeperHub's per-transaction stablecoin cap.

docs/api/direct-execution.md: "A single transaction that moves a recognised
stablecoin ... is limited to 100 USD ... nothing is signed or broadcast; the
request completes as a failed execution (202 with status: failed)". The
submitter turns that into an Almanak SubmissionResult(submitted=False) and
stops the bundle. Nothing leaves the wallet.
"""

from common import USDC_BASE, banner, calldata, record, run, tx, Stack


async def main() -> None:
    async with Stack() as stack:
        banner("transfer 150 USDC to self (over the 100 USD per-transaction stablecoin cap)")
        transfer = tx(
            USDC_BASE,
            calldata("transfer(address,uint256)", ["address", "uint256"], [stack.address, 150 * 10**6]),
            stack.address,
            nonce=await stack.nonce(),
            gas_limit=80_000,
            description="transfer 150 USDC to self",
        )
        signed = await stack.signer.sign(transfer, "base")
        results = await stack.submitter.submit([signed])
        print(f"submitted={results[0].submitted} error={results[0].error}")
        assert results[0].submitted is False, "KeeperHub should have refused this before signing"
        record("cap_refused", amount_usdc=150, error=results[0].error, broadcast=False)


run(main())
