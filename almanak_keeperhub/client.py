"""Async client for the KeeperHub Direct Execution API.

Endpoints used (docs/api/direct-execution.md, docs/api/user.md):

* ``GET  /api/user``                          -> organization wallet address
* ``POST /api/execute/contract-call``         -> ``simulate: true`` dry run, or broadcast with ``Idempotency-Key``
* ``GET  /api/execute/{executionId}/status``  -> verified receipts, ``X-Poll-Interval-Hint`` header
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from almanak_keeperhub.errors import (
    KeeperHubAPIError,
    KeeperHubAuthError,
    KeeperHubIdempotencyConflict,
    KeeperHubIdempotencyInProgress,
    KeeperHubRateLimited,
    KeeperHubUnavailable,
)

DEFAULT_BASE_URL = "https://app.keeperhub.com"
TERMINAL_STATUSES = frozenset({"completed", "failed"})


@dataclass(frozen=True)
class ContractCall:
    """One write call in the shape ``POST /api/execute/contract-call`` accepts."""

    contract_address: str
    chain_id: int
    function_name: str
    function_args: list[Any]
    abi: list[dict[str, Any]]
    value_wei: int = 0

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contractAddress": self.contract_address,
            "chainId": self.chain_id,
            "functionName": self.function_name,
            "functionArgs": json.dumps(self.function_args),
            "abi": json.dumps(self.abi),
        }
        if self.value_wei:
            body["value"] = wei_to_ether_string(self.value_wei)
        return body


def wei_to_ether_string(value_wei: int) -> str:
    """Exact decimal ether string without exponent, e.g. 10**15 -> ``0.001``."""
    text = format(Decimal(value_wei) / Decimal(10**18), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class SimulationOutcome:
    success: bool
    would_revert: bool
    gas_estimate: int | None = None
    sender: str | None = None
    failure_kind: str | None = None
    revert_reason: str | None = None
    code: str | None = None
    balance_wei: int | None = None
    required_wei: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionEnvelope:
    """The 202 body of a broadcast (or its 24-hour replay)."""

    execution_id: str
    status: str
    transaction_hash: str | None
    transaction_link: str | None
    error: str | None
    idempotent_replay: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Receipt:
    hash: str
    chain_id: int | None
    verified: bool
    receipt_status: str | None
    block_number: int | None
    gas_used: int | None


@dataclass(frozen=True)
class ExecutionStatus:
    execution_id: str
    status: str
    transaction_hash: str | None
    transaction_link: str | None
    sponsored: bool
    receipts: list[Receipt]
    error: str | None
    poll_hint_seconds: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        # The server computes the hint from its own terminal set; trust it first.
        if self.poll_hint_seconds is not None:
            return self.poll_hint_seconds == 0
        return self.status in TERMINAL_STATUSES


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class KeeperHubClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("KeeperHub API key is required (KEEPERHUB_API_KEY)")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._wallet_address: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def wallet_address(self) -> str:
        """Organization wallet: ``GET /api/user`` first, ``GET /api/user/wallet`` as fallback.

        Both accept an organization API key per docs/api/user.md; the first is what the
        headless-onboarding guide recommends, the second is the wallet resource itself.
        """
        if self._wallet_address is None:
            last_status = 0
            last_payload: dict[str, Any] = {}
            for path in ("/api/user", "/api/user/wallet"):
                response = await self._http.get(path)
                payload = _json_or_empty(response)
                _raise_for_status(response, payload)
                address = payload.get("walletAddress")
                if isinstance(address, str) and address:
                    self._wallet_address = address
                    return address
                last_status, last_payload = response.status_code, payload
            raise KeeperHubAPIError(
                "KeeperHub returned no walletAddress from /api/user or /api/user/wallet. Provision the "
                "organization wallet in the app (Settings > Organization > Wallets), or set "
                "KEEPERHUB_WALLET_ADDRESS explicitly.",
                status=last_status,
                payload=last_payload,
            )
        return self._wallet_address

    async def simulate_contract_call(self, call: ContractCall) -> SimulationOutcome:
        body = call.body()
        body["simulate"] = True
        response = await self._http.post("/api/execute/contract-call", json=body)
        payload = _json_or_empty(response)
        if response.status_code == 503:
            raise KeeperHubUnavailable(_message(payload, "simulator unavailable"), status=503, payload=payload)
        if response.status_code not in (200, 400):
            _raise_for_status(response, payload)
        return SimulationOutcome(
            success=bool(payload.get("success")) and response.status_code == 200,
            would_revert=bool(payload.get("wouldRevert")),
            gas_estimate=_int_or_none(payload.get("gasEstimate")),
            sender=payload.get("from"),
            failure_kind=payload.get("failureKind"),
            revert_reason=payload.get("revertReason") or payload.get("error"),
            code=payload.get("code") if isinstance(payload.get("code"), str) else None,
            balance_wei=_int_or_none(payload.get("balanceWei")),
            required_wei=_int_or_none(payload.get("requiredWei")),
            raw=payload,
        )

    async def execute_contract_call(self, call: ContractCall, *, idempotency_key: str) -> ExecutionEnvelope:
        response = await self._http.post(
            "/api/execute/contract-call",
            json=call.body(),
            headers={"Idempotency-Key": idempotency_key},
        )
        payload = _json_or_empty(response)
        _raise_for_status(response, payload)
        execution_id = payload.get("executionId")
        if not isinstance(execution_id, str):
            raise KeeperHubAPIError(
                "broadcast response carried no executionId", status=response.status_code, payload=payload
            )
        return ExecutionEnvelope(
            execution_id=execution_id,
            status=str(payload.get("status", "")),
            transaction_hash=payload.get("transactionHash"),
            transaction_link=payload.get("transactionLink"),
            error=payload.get("error"),
            idempotent_replay=payload.get("idempotentReplay") is True,
            raw=payload,
        )

    async def execution_status(self, execution_id: str) -> ExecutionStatus:
        response = await self._http.get(f"/api/execute/{execution_id}/status")
        payload = _json_or_empty(response)
        _raise_for_status(response, payload)
        hint_header = response.headers.get("X-Poll-Interval-Hint")
        hint = float(hint_header) if hint_header not in (None, "") else None
        receipts = [
            Receipt(
                hash=str(item.get("hash")),
                chain_id=_int_or_none(item.get("chainId")),
                verified=item.get("verified") is True,
                receipt_status=item.get("receiptStatus"),
                block_number=_int_or_none(item.get("blockNumber")),
                gas_used=_int_or_none(item.get("gasUsed")),
            )
            for item in payload.get("receipts") or []
            if isinstance(item, dict)
        ]
        return ExecutionStatus(
            execution_id=str(payload.get("executionId", execution_id)),
            status=str(payload.get("status", "")),
            transaction_hash=payload.get("transactionHash"),
            transaction_link=payload.get("transactionLink"),
            sponsored=payload.get("sponsored") is True,
            receipts=receipts,
            error=payload.get("error"),
            poll_hint_seconds=hint,
            raw=payload,
        )

    async def wait_for_terminal(
        self,
        execution_id: str,
        *,
        timeout_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.monotonic,
        default_interval: float = 3.0,
    ) -> ExecutionStatus:
        """Poll status honouring ``X-Poll-Interval-Hint`` until terminal."""
        deadline = now() + timeout_seconds
        while True:
            status = await self.execution_status(execution_id)
            if status.terminal:
                return status
            if now() >= deadline:
                raise TimeoutError(
                    f"KeeperHub execution {execution_id} still '{status.status}' after {timeout_seconds:.0f}s; "
                    "do not resend, keep the same idempotency key and poll again"
                )
            interval = status.poll_hint_seconds if status.poll_hint_seconds else default_interval
            await sleep(float(interval))


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _message(payload: dict[str, Any], fallback: str) -> str:
    for key in ("message", "error", "details"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _raise_for_status(response: httpx.Response, payload: dict[str, Any]) -> None:
    status = response.status_code
    if status < 400:
        return
    message = _message(payload, f"KeeperHub returned HTTP {status}")
    code = payload.get("code")
    if status == 401 or (
        status == 403 and (payload.get("error") == "insufficient_scope" or code == "insufficient_scope")
    ):
        raise KeeperHubAuthError(message, status=status, payload=payload)
    if status == 409 and code == "idempotency_conflict":
        raise KeeperHubIdempotencyConflict(message, status=status, payload=payload)
    if status == 409 and code == "idempotency_in_progress":
        raise KeeperHubIdempotencyInProgress(message, status=status, payload=payload)
    if status == 429:
        retry_after = _int_or_none(response.headers.get("Retry-After")) or 5
        raise KeeperHubRateLimited(message, status=status, payload=payload, retry_after_seconds=retry_after)
    if status == 503:
        raise KeeperHubUnavailable(message, status=status, payload=payload)
    raise KeeperHubAPIError(message, status=status, payload=payload)
