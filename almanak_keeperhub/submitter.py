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
import time
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
from almanak_keeperhub.receipts import ReceiptLog
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
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if receipt_fetcher is None:
            if not rpc_url:
                raise ValueError("KeeperHubSubmitter needs an rpc_url (for receipt logs) or a receipt_fetcher")
            receipt_fetcher = _web3_receipt_fetcher(rpc_url)
        self._client = client
        self._fetch_receipt = receipt_fetcher
        self._sleep = sleep
        self._now = now
        self._confirmation_timeout = confirmation_timeout
        self._max_in_progress_retries = max_in_progress_retries
        self._executions: dict[str, ExecutionStatus | ExecutionEnvelope] = {}
        self._receipts = ReceiptLog()

    # -- Submitter interface ----------------------------------------------------

    async def submit(self, txs: list[SignedTransaction]) -> list[SubmissionResult]:
        results: list[SubmissionResult] = []
        for index, signed in enumerate(txs):
            if not isinstance(signed, KeeperHubSignedTransaction) or signed.call is None:
                raise SubmissionError("KeeperHubSubmitter only accepts transactions prepared by KeeperHubSigner")
            envelope = await self._broadcast(signed)
            if envelope.transaction_hash is None:
                # Refused before broadcast: cap, guard, validation. Nothing reached the chain,
                # and the transactions after this one depend on it, so stop here.
                reason = envelope.error or f"KeeperHub execution {envelope.execution_id} ended '{envelope.status}'"
                logger.error("KeeperHub refused tx %d/%d: %s", index + 1, len(txs), reason)
                results.append(SubmissionResult(tx_hash="", submitted=False, error=reason))
                return results
            signed.tx_hash = envelope.transaction_hash  # the orchestrator indexes by this
            self._executions[envelope.transaction_hash.lower()] = envelope
            self._receipts.record(
                envelope.execution_id,
                chain_id=signed.call.chain_id,
                to=signed.call.contract_address,
                function=signed.call.function_name,
                tx_hash=envelope.transaction_hash,
                transaction_link=envelope.transaction_link,
                status=envelope.status,
                idempotency_key=signed.idempotency_key,
                idempotent_replay=envelope.idempotent_replay,
            )
            logger.info(
                "KeeperHub execution %s broadcast tx %s (%s)%s",
                envelope.execution_id,
                envelope.transaction_hash,
                envelope.status,
                " [idempotent replay]" if envelope.idempotent_replay else "",
            )
            results.append(SubmissionResult(tx_hash=envelope.transaction_hash, submitted=True))
            if envelope.status == "failed":
                # Reached the chain and reverted. The receipt phase reports it with the hash;
                # the transactions after this one depend on it, so nothing more is sent.
                logger.error("KeeperHub execution %s reverted on chain; stopping the bundle", envelope.execution_id)
                return results
            if index < len(txs) - 1 and envelope.status != "completed":
                # Later transactions depend on this one landing; wait before sending the next.
                settled = await self._settle(envelope.transaction_hash, timeout=self._confirmation_timeout)
                if settled.status == "failed":
                    logger.error(
                        "KeeperHub execution %s failed while settling; stopping the bundle", settled.execution_id
                    )
                    return results
        return results

    async def get_receipt(self, tx_hash: str, timeout: float = 120.0) -> TransactionReceipt:
        status = await self._settle(tx_hash, timeout=timeout)
        raw = await self._fetch_receipt(tx_hash)
        if raw is None:
            verified = [r.verified for r in status.receipts]
            raise SubmissionError(
                f"KeeperHub execution {status.execution_id} is '{status.status}' (verified={verified}) but this "
                f"process could not read the receipt for {tx_hash} from its RPC; the transaction is not resent, "
                "keep the same idempotency key and retry the receipt read",
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
                return await self._client.execute_contract_call(signed.call, idempotency_key=signed.idempotency_key)
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
                raise SubmissionError(f"KeeperHub credential cannot broadcast: {exc}", recoverable=False) from exc
            except KeeperHubAPIError as exc:
                recoverable = exc.status >= 500
                raise SubmissionError(
                    f"KeeperHub refused the broadcast (HTTP {exc.status}): {exc}", recoverable=recoverable
                ) from exc

    async def _settle(self, tx_hash: str, timeout: float) -> ExecutionStatus:
        known = self.execution_for(tx_hash)
        if isinstance(known, ExecutionStatus) and known.terminal:
            return known
        try:
            status = await self._client.wait_for_terminal(
                known.execution_id, timeout_seconds=timeout, sleep=self._sleep, now=self._now
            )
        except TimeoutError as exc:
            # Almanak's contract for a broadcast that may still land: recoverable, keep the hash,
            # never resend under a new key.
            raise SubmissionError(
                f"KeeperHub execution {known.execution_id} still unconfirmed after {timeout:.0f}s; "
                "do not resend, keep the same idempotency key and poll the receipt again",
                tx_hash=tx_hash,
                recoverable=True,
            ) from exc
        self._executions[tx_hash.lower()] = status
        self._receipts.update(
            status.execution_id,
            status=status.status,
            verified=all(r.verified for r in status.receipts) if status.receipts else None,
            receipt_status=[r.receipt_status for r in status.receipts],
            transaction_link=status.transaction_link,
            sponsored=status.sponsored,
        )
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
            except Exception as exc:  # noqa: BLE001 - dead or flaky RPC: retry, then let get_receipt explain
                logger.warning(
                    "receipt fetch for %s failed on %s (attempt %d/6): %s", tx_hash, rpc_url, attempt + 1, exc
                )
                await asyncio.sleep(2.0 * (attempt + 1))
        return None

    return fetch
