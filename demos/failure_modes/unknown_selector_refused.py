"""Failure mode 5: calldata whose selector nothing can decode is refused, never guessed.

The signer looks the selector up in the offline index. When it is not there, the bytes
are handed to KeeperHub as raw calldata (the change this project merged upstream) for
KeeperHub to decode against the contract's verified ABI. A KeeperHub that has that
change refuses with 400 on ``data``; one that predates it cannot take raw calldata at
all, and the submitter refuses before anything is signed. In both cases: no signature
is guessed and nothing is broadcast.
"""

from almanak.framework.execution.interfaces import SigningError, SubmissionError
from common import CHAIN_NAME, VAULT_BASE, Stack, banner, record, run, tx


async def main() -> None:
    async with Stack() as stack:
        banner("sign calldata with selector 0xdeadbeef")
        weird = tx(VAULT_BASE, "0xdeadbeef" + "00" * 32, stack.address, nonce=0)
        try:
            signed = await stack.signer.sign(weird, CHAIN_NAME)
        except SigningError as exc:
            print(f"refused at signing: {exc}")
            record("unknown_selector_refused", selector="0xdeadbeef", broadcast=False, refused_by="signer")
            return
        print("selector unknown to the index; handing the raw bytes to KeeperHub to decode or refuse")
        try:
            results = await stack.submitter.submit([signed])
        except SubmissionError as exc:
            print(f"refused: {exc}")
        else:
            if results[0].submitted:
                raise SystemExit(f"KeeperHub accepted undecodable calldata: {results[0].tx_hash}")
            print(f"refused: {results[0].error}")
        where = (
            "keeperhub"
            if stack.client.capabilities.raw_calldata
            else "submitter (this KeeperHub takes no raw calldata)"
        )
        record("unknown_selector_refused", selector="0xdeadbeef", broadcast=False, refused_by=where)


run(main())
