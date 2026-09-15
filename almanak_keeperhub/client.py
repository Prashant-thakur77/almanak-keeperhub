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
    """One write call in the shape ``POST /api/execute/contract-call`` accepts.

    Two spellings of the same call: the typed one (function name, arguments, ABI fragment)
    and the raw one (``data``, the calldata Almanak compiled). KeeperHub accepts raw calldata
    since the change this project contributed upstream; when it does, the bytes go as they
    are and KeeperHub decodes them losslessly. An older API gets the typed spelling. A call
    with ``data`` but no function name is raw-only: KeeperHub decodes it against the
    contract's verified ABI, and refuses it if it cannot.
    """

    contract_address: str
    chain_id: int
    function_name: str
    function_args: list[Any]
    abi: list[dict[str, Any]]
    value_wei: int = 0
    data: str | None = None

    @property
    def raw_only(self) -> bool:
        return bool(self.data) and not self.function_name

    def body(self, *, raw: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"contractAddress": self.contract_address, "chainId": self.chain_id}
        if raw or self.raw_only:
            if not self.data:
                raise ValueError("raw body requested for a call without calldata")
            body["data"] = self.data
            if self.abi:
                body["abi"] = json.dumps(self.abi)
        else:
            body["functionName"] = self.function_name
            body["functionArgs"] = json.dumps(self.function_args)
            body["abi"] = json.dumps(self.abi)
        if self.value_wei:
            body["value"] = wei_to_ether_string(self.value_wei)
        return body

    def label(self) -> str:
        return self.function_name or (self.data or "0x")[:10]


@dataclass
class Capabilities:
    """What the KeeperHub API this client talks to accepts, learned from its answers.

    ``None`` is not yet known. Each flag latches to False the first time the API rejects
    the newer shape with the error an older schema gives, and to True the first time it
    accepts it, so a process asks at most once per feature.
    """

    raw_calldata: bool | None = None
    call_sequence: bool | None = None


def _rejects_unknown_shape(status: int, payload: dict[str, Any], missing_field: str) -> bool:
    """An older API ignores the new field and reports the typed field it wanted instead."""
    return status == 400 and payload.get("field") == missing_field and "required" in str(payload.get("error", ""))


def wei_to_ether_string(value_wei: int) -> str:
    """Exact decimal ether string without exponent, e.g. 10**15 -> ``0.001``.

    Integer arithmetic, not Decimal: Decimal's default 28-digit context rounds anything past
    10**28 wei, and "exact" has to mean exact whatever the amount.
    """
    whole, frac = divmod(int(value_wei), 10**18)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:018d}".rstrip("0")


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
class SequenceOutcome:
    """A dry run of several calls, each against the state the one before it produced."""

    success: bool
    results: list[SimulationOutcome]
    mechanism: str | None
    atomic: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def first_failure(self) -> int | None:
        for index, result in enumerate(self.results):
            if not result.success:
                return index
        return None


@dataclass(frozen=True)
class ConditionResult:
    met: bool
    observed_value: str | None
    target_value: str | None
    operator: str | None


@dataclass(frozen=True)
class GuardedOutcome:
    """The answer of ``POST /api/execute/check-and-execute``: KeeperHub read the guard, compared, and acted or not."""

    executed: bool
    condition: ConditionResult
    execution_id: str | None = None
    status: str | None = None
    transaction_hash: str | None = None
    idempotent_replay: bool = False
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
        self.capabilities = Capabilities()

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

    def _use_raw(self, call: ContractCall) -> bool:
        return bool(call.data) and (call.raw_only or self.capabilities.raw_calldata is not False)

    async def _post_call(
        self, call: ContractCall, *, simulate: bool, headers: dict[str, str] | None = None
    ) -> tuple[httpx.Response, dict[str, Any], bool]:
        """POST the call, raw when the API takes raw calldata, typed otherwise; says which was used."""
        raw = self._use_raw(call)
        body = call.body(raw=raw)
        if simulate:
            body["simulate"] = True
        response = await self._http.post("/api/execute/contract-call", json=body, headers=headers)
        payload = _json_or_empty(response)
        if raw and self.capabilities.raw_calldata is None:
            if _rejects_unknown_shape(response.status_code, payload, "functionName"):
                self.capabilities.raw_calldata = False
                if call.raw_only:
                    raise KeeperHubAPIError(
                        "this KeeperHub does not accept raw calldata and the selector is not in the local index",
                        status=response.status_code,
                        payload=payload,
                    )
                return await self._post_call(call, simulate=simulate, headers=headers)
            if response.status_code in (200, 202):
                self.capabilities.raw_calldata = True
        return response, payload, raw

    async def simulate_contract_call(self, call: ContractCall) -> SimulationOutcome:
        response, payload, _ = await self._post_call(call, simulate=True)
        if response.status_code == 503:
            raise KeeperHubUnavailable(_message(payload, "simulator unavailable"), status=503, payload=payload)
        if response.status_code not in (200, 400):
            _raise_for_status(response, payload)
        return _simulation_outcome(payload, ok=response.status_code == 200)

    async def simulate_call_sequence(self, calls: list[ContractCall]) -> SequenceOutcome | None:
        """Dry-run ``calls`` in order, each against the state the previous one produced.

        ``None`` when this KeeperHub predates sequence simulation, so the caller can fall back
        to one dry run per call. Raises on 503 like the single-call dry run.
        """
        if not calls or self.capabilities.call_sequence is False or any(c.raw_only for c in calls):
            return None  # sequence entries are typed upstream; a raw-only call has no typed spelling
        chain_id = calls[0].chain_id
        entries = []
        for call in calls:
            entry = call.body()
            entry.pop("chainId", None)
            entries.append(entry)
        response = await self._http.post(
            "/api/execute/contract-call", json={"chainId": chain_id, "simulate": True, "calls": entries}
        )
        payload = _json_or_empty(response)
        if _rejects_unknown_shape(response.status_code, payload, "contractAddress"):
            self.capabilities.call_sequence = False
            return None
        if response.status_code == 503 and not isinstance(payload.get("results"), list):
            raise KeeperHubUnavailable(_message(payload, "simulator unavailable"), status=503, payload=payload)
        if response.status_code not in (200, 400, 503):
            _raise_for_status(response, payload)
        results = payload.get("results")
        if not isinstance(results, list):
            raise KeeperHubAPIError(
                _message(payload, "sequence dry run returned no results"), status=response.status_code, payload=payload
            )
        self.capabilities.call_sequence = True
        return SequenceOutcome(
            success=bool(payload.get("success")) and response.status_code == 200,
            results=[_simulation_outcome(r, ok=bool(r.get("success"))) for r in results if isinstance(r, dict)],
            mechanism=payload.get("mechanism"),
            atomic=bool(payload.get("atomic")),
            raw=payload,
        )

    async def check_and_execute(
        self,
        *,
        check: ContractCall,
        operator: str,
        value: str,
        action: ContractCall,
        idempotency_key: str | None = None,
        simulate: bool = False,
    ) -> GuardedOutcome:
        """Have KeeperHub read ``check``, compare it with ``value``, and only then run ``action``.

        The read happens on KeeperHub's side right before the write, so a decision made on a
        stale snapshot (a position already closed by the keeper or another process) comes back
        ``executed: false`` with the observed value instead of a broadcast that reverts.
        """
        body: dict[str, Any] = {
            **check.body(),
            "condition": {"operator": operator, "value": value},
            "action": {k: v for k, v in action.body().items() if k != "chainId"},
        }
        if simulate:
            body["simulate"] = True
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = await self._http.post("/api/execute/check-and-execute", json=body, headers=headers)
        payload = _json_or_empty(response)
        if response.status_code >= 400 and not (response.status_code == 400 and payload.get("conditionResult")):
            _raise_for_status(response, payload)
        cond = payload.get("conditionResult") if isinstance(payload.get("conditionResult"), dict) else {}
        return GuardedOutcome(
            executed=bool(payload.get("executed")),
            condition=ConditionResult(
                met=bool(cond.get("met")),
                observed_value=str(cond["observedValue"]) if cond.get("observedValue") is not None else None,
                target_value=str(cond["targetValue"]) if cond.get("targetValue") is not None else None,
                operator=cond.get("operator"),
            ),
            execution_id=payload.get("executionId") if isinstance(payload.get("executionId"), str) else None,
            status=payload.get("status"),
            transaction_hash=payload.get("transactionHash"),
            idempotent_replay=payload.get("idempotentReplay") is True,
            raw=payload,
        )

    async def execute_contract_call(self, call: ContractCall, *, idempotency_key: str) -> ExecutionEnvelope:
        response, payload, _ = await self._post_call(call, simulate=False, headers={"Idempotency-Key": idempotency_key})
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
        """Poll status honouring ``X-Poll-Interval-Hint`` until terminal.

        A rate limit, a 5xx or a transport error while polling never decides the outcome
        of a transaction that may still land: the poll is retried until the deadline.
        """
        deadline = now() + timeout_seconds
        last_problem: str | None = None
        while True:
            try:
                status = await self.execution_status(execution_id)
            except KeeperHubRateLimited as exc:
                last_problem = str(exc)
                interval: float = float(exc.retry_after_seconds)
            except (KeeperHubUnavailable, httpx.TransportError) as exc:
                last_problem = str(exc)
                interval = default_interval
            except KeeperHubAPIError as exc:
                if exc.status < 500:
                    raise
                last_problem = str(exc)
                interval = default_interval
            else:
                if status.terminal:
                    return status
                interval = status.poll_hint_seconds if status.poll_hint_seconds else default_interval
                last_problem = None
            if now() >= deadline:
                raise TimeoutError(
                    f"KeeperHub execution {execution_id} not terminal after {timeout_seconds:.0f}s"
                    + (f" (last poll problem: {last_problem})" if last_problem else "")
                    + "; do not resend, keep the same idempotency key and poll again"
                )
            await sleep(float(interval))


def _simulation_outcome(payload: dict[str, Any], *, ok: bool) -> SimulationOutcome:
    return SimulationOutcome(
        success=bool(payload.get("success")) and ok,
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
