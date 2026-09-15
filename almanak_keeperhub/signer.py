"""KeeperHubSigner: an Almanak ``Signer`` whose key never leaves KeeperHub's Turnkey enclave.

``sign()`` does no cryptography. It decodes the compiled calldata into the
call KeeperHub will execute, derives the idempotency key that identifies this
piece of work, and returns a ``SignedTransaction`` the orchestrator can track.
The organization wallet signs at broadcast time, inside KeeperHub.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass

from almanak.framework.execution.interfaces import (
    SignedTransaction,
    Signer,
    SigningError,
    UnsignedTransaction,
)

from almanak_keeperhub.calldata import SelectorIndex, decode_calldata
from almanak_keeperhub.client import ContractCall, KeeperHubClient
from almanak_keeperhub.errors import UndecodableCalldata

KEY_VERSION = "almanak-keeperhub/v2"
SALT_ENV = "ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT"

# The piece of work the current execution belongs to (Almanak's intent id, set by the
# gateway wrapper around orchestrator.execute). Empty outside an orchestrated execution.
_work_id: ContextVar[str] = ContextVar("almanak_keeperhub_work_id", default="")


@contextlib.contextmanager
def work_id_scope(work_id: str) -> Iterator[None]:
    token = _work_id.set(work_id)
    try:
        yield
    finally:
        _work_id.reset(token)


def current_work_id() -> str:
    return _work_id.get()


@dataclass
class KeeperHubSignedTransaction(SignedTransaction):
    """A ``SignedTransaction`` plus what KeeperHub needs to broadcast it."""

    call: ContractCall | None = None
    idempotency_key: str = ""


def idempotency_key_for(tx: UnsignedTransaction, sender: str, work_id: str = "") -> str:
    """Identify the work, not the attempt (docs/api/direct-execution.md, "Choosing a stable key").

    Almanak assigns a fresh nonce on every attempt, so the nonce is deliberately not part of
    the key: a retry of the same intent reproduces the key and KeeperHub replays the first
    execution instead of moving funds twice. The intent id separates two intents that happen
    to compile to identical calldata within KeeperHub's 24-hour replay window.
    """
    parts = [
        KEY_VERSION,
        str(tx.chain_id),
        sender.lower(),
        str(tx.to).lower(),
        (tx.data or "0x").lower(),
        str(int(tx.value or 0)),
        work_id,
        os.environ.get(SALT_ENV, ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def call_for(tx: UnsignedTransaction, *, index: SelectorIndex | None, raw_calldata: bool | None) -> ContractCall:
    """The KeeperHub call for a compiled transaction, in both spellings when possible.

    The typed spelling comes from the local selector index. When the selector is not there,
    the call is raw-only: KeeperHub decodes the bytes against the contract's verified ABI
    and refuses them if it cannot, which is the same refusal the index used to make, moved
    to the party that signs. That path needs a KeeperHub that accepts raw calldata; when
    this process already knows it does not, the refusal happens here.
    """
    data = tx.data or "0x"
    value_wei = int(tx.value or 0)
    try:
        decoded = decode_calldata(data, to=str(tx.to), value_wei=value_wei, index=index)
    except UndecodableCalldata as exc:
        if raw_calldata is False or len(data) < 10:
            raise SigningError(str(exc)) from exc
        return ContractCall(
            contract_address=str(tx.to),
            chain_id=int(tx.chain_id),
            function_name="",
            function_args=[],
            abi=[],
            value_wei=value_wei,
            data=data,
        )
    return ContractCall(
        contract_address=str(tx.to),
        chain_id=int(tx.chain_id),
        function_name=decoded.function_name,
        function_args=decoded.function_args,
        abi=decoded.abi,
        value_wei=value_wei,
        data=data,
    )


class KeeperHubSigner(Signer):
    def __init__(self, client: KeeperHubClient, address: str, index: SelectorIndex | None = None) -> None:
        self._client = client
        self._address = address
        self._index = index

    @property
    def address(self) -> str:
        return self._address

    async def sign(self, tx: UnsignedTransaction, chain: str) -> KeeperHubSignedTransaction:
        if not tx.to:
            raise SigningError("KeeperHub contract-call cannot deploy contracts (tx.to is empty)")
        if tx.from_address and tx.from_address.lower() != self._address.lower():
            raise SigningError(
                f"tx.from_address {tx.from_address} is not the KeeperHub organization wallet {self._address}"
            )
        call = call_for(tx, index=self._index, raw_calldata=self._client.capabilities.raw_calldata)
        key = idempotency_key_for(tx, self._address, current_work_id())
        return KeeperHubSignedTransaction(
            raw_tx="0x",
            tx_hash="0x" + key,
            unsigned_tx=tx,
            call=call,
            idempotency_key=key,
        )
