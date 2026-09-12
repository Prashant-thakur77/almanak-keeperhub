"""Failure mode 5: calldata this package cannot decode is refused before anything is sent.

KeeperHub has no raw-calldata write on EVM, so the signer must name the function
and typed arguments. An unknown selector is a SigningError at SIGNING phase,
never a guess. Offline: no KeeperHub call is made.
"""

from almanak.framework.execution.interfaces import SigningError
from common import CHAIN_NAME, VAULT_BASE, Stack, banner, record, run, tx


async def main() -> None:
    async with Stack() as stack:
        banner("sign calldata with selector 0xdeadbeef")
        weird = tx(VAULT_BASE, "0xdeadbeef" + "00" * 32, stack.address, nonce=0)
        try:
            await stack.signer.sign(weird, CHAIN_NAME)
        except SigningError as exc:
            print(f"refused: {exc}")
            record("unknown_selector_refused", selector="0xdeadbeef", broadcast=False)
            return
        raise SystemExit("expected SigningError")


run(main())
