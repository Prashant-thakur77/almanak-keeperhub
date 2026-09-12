"""KeeperHubSubmitter: an Almanak ``Submitter`` that broadcasts through KeeperHub.

Contract (interfaces.Submitter docstring): return ``SubmissionResult`` per tx,
retry connection-level problems, raise typed errors for the rest. KeeperHub
owns nonce assignment, gas, retries and receipt verification; this class maps
its envelope back into Almanak's types and keeps the execution id per hash so
the receipt phase can resume.

Rules that keep the orchestrator honest:

* a bundle is sequential; once a transaction is refused, reverts, or cannot be
  confirmed, every later transaction is returned as ``submitted=False`` so the
  orchestrator takes its failure branch instead of reporting a partial bundle
  as success;
* a transport error, a 5xx or a rate limit never rotates the idempotency key:
  the same request is sent again, and KeeperHub answers with a replay or
  ``idempotency_in_progress`` if the first attempt got through;
* KeeperHub's verified receipt outranks the chain receipt when they disagree
  (a Safe inner-call failure has a successful outer transaction).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
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
    KeeperHubUnavailable,
)
from almanak_keeperhub.notify import notifier_from_env
from almanak_keeperhub.receipts import ReceiptLog
from almanak_keeperhub.signer import KeeperHubSignedTransaction

logger = logging.getLogger(__name__)

ReceiptFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]
Sleep = Callable[[float], Awaitable[None]]
FAILED_RECEIPT_STATUSES = frozenset({"reverted", "safe_inner_failure"})
TERMINAL_OK_STATUSES = frozenset({"completed", "success"})


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
        self._max_retries = max_in_progress_retries
        self._executions: dict[str, ExecutionStatus | ExecutionEnvelope] = {}
        self._receipts = ReceiptLog()
        self._notify = notifier_from_env()

    # -- Submitter interface ----------------------------------------------------

    async def submit(self, txs: list[SignedTransaction]) -> list[SubmissionResult]:
        results: list[SubmissionResult] = []
        for index, signed in enumerate(txs):
            if not isinstance(signed, KeeperHubSignedTransaction) or signed.call is None:
                raise SubmissionError("KeeperHubSubmitter only accepts transactions prepared by KeeperHubSigner")
            envelope = await self._broadcast(signed)
            if envelope.transaction_hash is None:
                # Refused before broadcast: cap, guard, validation. Nothing reached the chain.
                reason = envelope.error or f"KeeperHub execution {envelope.execution_id} ended '{envelope.status}'"
                logger.error("KeeperHub refused tx %d/%d: %s", index + 1, len(txs), reason)
                await self._notify.send(
                    "refused before broadcast",
                    f"{signed.call.function_name} -> {signed.call.contract_address}: {reason}",
                    None,
                )
                results.append(SubmissionResult(tx_hash="", submitted=False, error=reason))
                return self._abandon_rest(results, txs, index + 1, reason)
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
            await self._notify.send(
                "broadcast" + (" (replay)" if envelope.idempotent_replay else ""),
                f"{signed.call.function_name} -> {signed.call.contract_address} on chain {signed.call.chain_id}, execution {envelope.execution_id}",
                envelope.transaction_link,
            )
            results.append(SubmissionResult(tx_hash=envelope.transaction_hash, submitted=True))
            if envelope.status == "failed":
                # Reached the chain and failed. The receipt phase reports it with the hash.
                reason = envelope.error or f"KeeperHub execution {envelope.execution_id} failed"
                logger.error("KeeperHub execution %s failed on chain; stopping the bundle", envelope.execution_id)
                return self._abandon_rest(results, txs, index + 1, reason)
            if index < len(txs) - 1 and envelope.status != "completed":
                # Later transactions depend on this one landing; wait before sending the next.
                try:
                    settled = await self._settle(envelope.transaction_hash, timeout=self._confirmation_timeout)
                except SubmissionError as exc:
                    return self._abandon_rest(results, txs, index + 1, str(exc))
                if settled.status == "failed":
                    reason = settled.error or f"KeeperHub execution {settled.execution_id} failed while settling"
                    logger.error("%s; stopping the bundle", reason)
                    return self._abandon_rest(results, txs, index + 1, reason)
        return results

    async def get_receipt(self, tx_hash: str, timeout: float = 120.0) -> TransactionReceipt:
        status = await self._settle(tx_hash, timeout=timeout)
        raw = await self._fetch_receipt(tx_hash)
        if raw is None:
            verified_receipt = next(
                (r for r in status.receipts if r.hash.lower() == tx_hash.lower() and r.verified and r.block_number),
                None,
            )
            if verified_receipt is None:
                verified = [r.verified for r in status.receipts]
                raise SubmissionError(
                    f"KeeperHub execution {status.execution_id} is '{status.status}' (verified={verified}) but this "
                    f"process could not read the receipt for {tx_hash} from its RPC; the transaction is not resent, "
                    "keep the same idempotency key and retry the receipt read",
                    tx_hash=tx_hash,
                )
            # KeeperHub re-fetched and verified the receipt on its own node; a public RPC lagging behind
            # it is not a reason to fail the work. Logs are unavailable without the RPC.
            logger.warning(
                "receipt for %s unavailable from the local RPC; using KeeperHub's verified receipt (block %s, gas %s) without logs",
                tx_hash,
                verified_receipt.block_number,
                verified_receipt.gas_used,
            )
            return TransactionReceipt(
                tx_hash=tx_hash,
                block_number=int(verified_receipt.block_number or 0),
                block_hash="",
                gas_used=int(verified_receipt.gas_used or 0),
                effective_gas_price=0,
                status=0 if verified_receipt.receipt_status in FAILED_RECEIPT_STATUSES else 1,
                logs=[],
            )
        receipt = _to_almanak_receipt(raw, tx_hash)
        # KeeperHub re-fetches and classifies every receipt before settling. Its verdict outranks the
        # chain's status bit: a Safe inner-call failure has a successful outer transaction.
        if receipt.status == 1 and any(
            r.hash.lower() == tx_hash.lower() and r.receipt_status in FAILED_RECEIPT_STATUSES for r in status.receipts
        ):
            logger.error("KeeperHub classified %s as failed (%s); reporting revert", tx_hash, status.receipts)
            receipt.status = 0
        return receipt

    # -- extras used by the gateway/CLI ---------------------------------------------

    def execution_for(self, tx_hash: str) -> ExecutionStatus | ExecutionEnvelope:
        """The execution behind a hash: from memory, or from the receipts log after a restart.

        Almanak persists the hash in its session store and asks for the receipt again when a
        process resumes; the receipts log is what lets a fresh submitter answer without resending.
        """
        known = self._executions.get(tx_hash.lower())
        if known is not None:
            return known
        recorded = self._receipts.find_by_hash(tx_hash)
        if recorded is None:
            raise SubmissionError(
                f"no KeeperHub execution known for {tx_hash} (not in memory, not in {self._receipts.path})",
                tx_hash=tx_hash,
            )
        logger.info(
            "resuming KeeperHub execution %s for %s from %s", recorded["execution_id"], tx_hash, self._receipts.path
        )
        envelope = ExecutionEnvelope(
            execution_id=str(recorded["execution_id"]),
            status=str(recorded.get("status") or "unconfirmed"),
            transaction_hash=tx_hash,
            transaction_link=recorded.get("transaction_link"),
            error=None,
            idempotent_replay=bool(recorded.get("idempotent_replay", False)),
            raw=dict(recorded),
        )
        self._executions[tx_hash.lower()] = envelope
        return envelope

    # -- internals -------------------------------------------------------------------

    @staticmethod
    def _abandon_rest(
        results: list[SubmissionResult], txs: list[SignedTransaction], start: int, reason: str
    ) -> list[SubmissionResult]:
        """Every transaction after a failure is reported as not sent, so the orchestrator sees a failed bundle."""
        for position in range(start, len(txs)):
            results.append(
                SubmissionResult(
                    tx_hash="",
                    submitted=False,
                    error=(
                        f"not sent: transaction {position + 1}/{len(txs)} depends on an earlier one "
                        f"that did not complete ({reason})"
                    ),
                )
            )
        return results

    async def _broadcast(self, signed: KeeperHubSignedTransaction) -> ExecutionEnvelope:
        assert signed.call is not None
        attempts = 0
        while True:
            try:
                envelope = await self._client.execute_contract_call(signed.call, idempotency_key=signed.idempotency_key)
            except KeeperHubIdempotencyInProgress:
                attempts = await self._retry_or_give_up(attempts, 2.0, "first attempt still in progress")
            except KeeperHubRateLimited as exc:
                attempts = await self._retry_or_give_up(attempts, float(exc.retry_after_seconds), str(exc))
            except (KeeperHubUnavailable, httpx.TransportError) as exc:
                # The request may or may not have reached KeeperHub. Re-sending under the SAME key is
                # safe by construction: a landed attempt answers with a replay or in_progress.
                attempts = await self._retry_or_give_up(attempts, 3.0 * (attempts + 1), str(exc))
            except KeeperHubIdempotencyConflict as exc:
                raise SubmissionError(
                    f"idempotency key reused with a different body (original execution "
                    f"{exc.original_execution_id}); refusing to broadcast",
                    recoverable=False,
                ) from exc
            except KeeperHubAuthError as exc:
                raise SubmissionError(f"KeeperHub credential cannot broadcast: {exc}", recoverable=False) from exc
            except KeeperHubAPIError as exc:
                if exc.status >= 500:
                    attempts = await self._retry_or_give_up(attempts, 3.0 * (attempts + 1), str(exc))
                    continue
                raise SubmissionError(
                    f"KeeperHub refused the broadcast (HTTP {exc.status}): {exc}", recoverable=False
                ) from exc
            else:
                return await self._resolve_hash(envelope)

    async def _retry_or_give_up(self, attempts: int, delay: float, reason: str) -> int:
        attempts += 1
        if attempts > self._max_retries:
            raise SubmissionError(
                f"KeeperHub broadcast not confirmed after {attempts - 1} retries ({reason}); "
                "the idempotency key was never rotated, retry the same tick later",
                recoverable=True,
            )
        logger.warning("KeeperHub broadcast retry %d/%d in %.0fs: %s", attempts, self._max_retries, delay, reason)
        await self._sleep(delay)
        return attempts

    async def _resolve_hash(self, envelope: ExecutionEnvelope) -> ExecutionEnvelope:
        """A non-terminal envelope without a hash is broadcast-in-flight, not a refusal: poll first."""
        if envelope.transaction_hash is not None or envelope.status == "failed":
            return envelope
        try:
            status = await self._client.wait_for_terminal(
                envelope.execution_id, timeout_seconds=self._confirmation_timeout, sleep=self._sleep, now=self._now
            )
        except TimeoutError as exc:
            raise SubmissionError(
                f"KeeperHub execution {envelope.execution_id} is '{envelope.status}' without a hash after "
                f"{self._confirmation_timeout:.0f}s; do not resend, keep the same idempotency key and poll again",
                recoverable=True,
            ) from exc
        if status.transaction_hash:
            self._executions[status.transaction_hash.lower()] = status
        return ExecutionEnvelope(
            execution_id=status.execution_id,
            status=status.status,
            transaction_hash=status.transaction_hash,
            transaction_link=status.transaction_link,
            error=status.error,
            idempotent_replay=envelope.idempotent_replay,
            raw=status.raw,
        )

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
        await self._notify.send(
            "settled" if status.status in TERMINAL_OK_STATUSES else "failed",
            f"execution {status.execution_id} {status.status}, verified={[r.verified for r in status.receipts]}",
            status.transaction_link,
        )
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
        # Public RPCs lag behind the node KeeperHub broadcast on and rate-limit bursts; be patient
        # (about two minutes by default) before falling back to KeeperHub's verified receipt.
        attempts = int(os.environ.get("ALMANAK_KEEPERHUB_RECEIPT_ATTEMPTS", "12"))
        for attempt in range(attempts):
            delay = min(15.0, 2.0 * (attempt + 1))
            try:
                receipt = await web3.eth.get_transaction_receipt(tx_hash)  # type: ignore[arg-type]
                return dict(receipt)
            except TransactionNotFound:
                await asyncio.sleep(delay)
            except Exception as exc:  # noqa: BLE001 - dead or flaky RPC: retry, then let get_receipt explain
                logger.warning(
                    "receipt fetch for %s failed on %s (attempt %d/%d): %s",
                    tx_hash,
                    rpc_url,
                    attempt + 1,
                    attempts,
                    exc,
                )
                await asyncio.sleep(delay)
        return None

    return fetch
