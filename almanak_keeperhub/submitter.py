"""KeeperHubSubmitter: an Almanak ``Submitter`` that broadcasts through KeeperHub.

Contract (interfaces.Submitter docstring): return ``SubmissionResult`` per tx,
retry connection-level problems, raise typed errors for the rest. KeeperHub
owns nonce assignment, gas, retries and receipt verification; this class maps
its envelope back into Almanak's types and keeps the execution id per hash so
the receipt phase can resume.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from almanak.framework.execution.interfaces import (
    SignedTransaction,
    SubmissionError,
    SubmissionResult,
    Submitter,
    TransactionReceipt,
)

from almanak_keeperhub.client import ExecutionEnvelope, ExecutionStatus, KeeperHubClient
from almanak_keeperhub.errors import (
    KeeperHubAPIError,
    KeeperHubAuthError,
    KeeperHubIdempotencyConflict,
    KeeperHubIdempotencyInProgress,
    KeeperHubRateLimited,
)
from almanak_keeperhub.signer import KeeperHubSignedTransaction

logger = logging.getLogger(__name__)

ReceiptFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]
Sleep = Callable[[float], Awaitable[None]]


class KeeperHubSubmitter(Submitter):
    def __init__(
        self,
        client: KeeperHubClient,
        receipt_fetcher: ReceiptFetcher | None = None,
        rpc_url: str | None = None,
        sleep: Sleep = asyncio.sleep,
        confirmation_timeout: float = 180.0,
        max_in_progress_retries: int = 20,
    ) -> None:
        if receipt_fetcher is None:
            if not rpc_url:
                raise ValueError(
                    "KeeperHubSubmitter needs an rpc_url (for receipt logs) or a receipt_fetcher"
                )
            receipt_fetcher = _web3_receipt_fetcher(rpc_url)
        self._client = client
        self._fetch_receipt = receipt_fetcher
        self._sleep = sleep
        self._confirmation_timeout = confirmation_timeout
        self._max_in_progress_retries = max_in_progress_retries
        self._executions: dict[str, ExecutionStatus | ExecutionEnvelope] = {}

    # -- Submitter interface ----------------------------------------------------

    async def submit(self, txs: list[SignedTransaction]) -> list[SubmissionResult]:
        results: list[SubmissionResult] = []
        for index, signed in enumerate(txs):
            if not isinstance(signed, KeeperHubSignedTransaction) or signed.call is None:
                raise SubmissionError(
                    "KeeperHubSubmitter only accepts transactions prepared by KeeperHubSigner"
                )
            envelope = await self._broadcast(signed)
            if envelope.transaction_hash is None:
                # Refused before broadcast: cap, guard, validation. Nothing reached the chain,
                # and the transactions after this one depend on it, so stop here.
                reason = (
                    envelope.error or f"KeeperHub execution {envelope.execution_id} ended '{envelope.status}'"
                )
                logger.error("KeeperHub refused tx %d/%d: %s", index + 1, len(txs), reason)
                results.append(SubmissionResult(tx_hash="", submitted=False, error=reason))
                return results
            signed.tx_hash = envelope.transaction_hash  # the orchestrator indexes by this
            self._executions[envelope.transaction_hash.lower()] = envelope
            logger.info(
                "KeeperHub execution %s broadcast tx %s (%s)%s",
                envelope.execution_id,
                envelope.transaction_hash,
                envelope.status,
                " [idempotent replay]" if envelope.idempotent_replay else "",
            )
            results.append(SubmissionResult(tx_hash=envelope.transaction_hash, submitted=True))
            if index < len(txs) - 1 and envelope.status not in ("completed", "failed"):
                # Later transactions depend on this one landing; wait before sending the next.
                await self._settle(envelope.transaction_hash, timeout=self._confirmation_timeout)
        return results

    async def get_receipt(self, tx_hash: str, timeout: float = 120.0) -> TransactionReceipt:
        status = await self._settle(tx_hash, timeout=timeout)
        raw = await self._fetch_receipt(tx_hash)
        if raw is None:
            raise SubmissionError(
                f"KeeperHub execution {status.execution_id} is '{status.status}' but the chain has no receipt "
                f"for {tx_hash} yet; keep the same idempotency key and poll again",
                tx_hash=tx_hash,
            )
        return _to_almanak_receipt(raw, tx_hash)

    # -- extras used by the gateway/CLI ---------------------------------------------

    def execution_for(self, tx_hash: str) -> ExecutionStatus | ExecutionEnvelope:
        try:
            return self._executions[tx_hash.lower()]
        except KeyError as exc:
            raise SubmissionError(f"no KeeperHub execution known for {tx_hash}", tx_hash=tx_hash) from exc

    # -- internals -------------------------------------------------------------------

    async def _broadcast(self, signed: KeeperHubSignedTransaction) -> ExecutionEnvelope:
        assert signed.call is not None
        attempts = 0
        while True:
            try:
                return await self._client.execute_contract_call(
                    signed.call, idempotency_key=signed.idempotency_key
                )
            except KeeperHubIdempotencyInProgress:
                attempts += 1
                if attempts > self._max_in_progress_retries:
                    raise SubmissionError(
                        "KeeperHub still reports the first attempt in progress; not rotating the key",
                        recoverable=True,
                    ) from None
                await self._sleep(2.0)
            except KeeperHubRateLimited as exc:
                attempts += 1
                if attempts > self._max_in_progress_retries:
                    raise SubmissionError(f"rate limited by KeeperHub: {exc}", recoverable=True) from exc
                await self._sleep(float(exc.retry_after_seconds))
            except KeeperHubIdempotencyConflict as exc:
                raise SubmissionError(
                    f"idempotency key reused with a different body (original execution "
                    f"{exc.original_execution_id}); refusing to broadcast",
                    recoverable=False,
                ) from exc
            except KeeperHubAuthError as exc:
                raise SubmissionError(
                    f"KeeperHub credential cannot broadcast: {exc}", recoverable=False
                ) from exc
            except KeeperHubAPIError as exc:
                recoverable = exc.status >= 500
                raise SubmissionError(
                    f"KeeperHub refused the broadcast (HTTP {exc.status}): {exc}", recoverable=recoverable
                ) from exc

    async def _settle(self, tx_hash: str, timeout: float) -> ExecutionStatus:
        known = self.execution_for(tx_hash)
        if isinstance(known, ExecutionStatus) and known.terminal:
            return known
        status = await self._client.wait_for_terminal(
            known.execution_id, timeout_seconds=timeout, sleep=self._sleep
        )
        self._executions[tx_hash.lower()] = status
        return status


def _to_almanak_receipt(raw: dict[str, Any], tx_hash: str) -> TransactionReceipt:
    return TransactionReceipt(
        tx_hash=tx_hash,
        block_number=int(raw["blockNumber"]),
        block_hash=_hex(raw.get("blockHash")),
        gas_used=int(raw["gasUsed"]),
        effective_gas_price=int(raw.get("effectiveGasPrice") or 0),
        status=int(raw.get("status", 0)),
        logs=[_plain_log(log) for log in raw.get("logs") or []],
        contract_address=raw.get("contractAddress"),
        from_address=raw.get("from"),
        to_address=raw.get("to"),
    )


def _hex(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes | bytearray):
        return "0x" + bytes(value).hex()
    return str(value)


def _plain_log(log: Any) -> dict[str, Any]:
    entry = dict(log)
    entry["topics"] = [_hex(t) for t in entry.get("topics", [])]
    for key in ("data", "transactionHash", "blockHash"):
        if key in entry:
            entry[key] = _hex(entry[key])
    return entry


def _web3_receipt_fetcher(rpc_url: str) -> ReceiptFetcher:
    from web3 import AsyncHTTPProvider, AsyncWeb3
    from web3.exceptions import TransactionNotFound

    web3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))

    async def fetch(tx_hash: str) -> dict[str, Any] | None:
        for attempt in range(6):
            try:
                receipt = await web3.eth.get_transaction_receipt(tx_hash)  # type: ignore[arg-type]
                return dict(receipt)
            except TransactionNotFound:
                await asyncio.sleep(2.0 * (attempt + 1))
        return None

    return fetch
