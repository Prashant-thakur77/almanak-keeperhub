"""KeeperHubSimulator: Almanak's dry run, executed by KeeperHub without touching the chain.

A multi-transaction bundle is dry-run as one sequence, each call against the state
the one before it produced, on a KeeperHub that offers sequence simulation (the change
this project contributed upstream). Before that, only the first transaction of a bundle
could be simulated against live state, because later ones depend on state the earlier
ones create (approve before deposit); on such a KeeperHub the later ones keep the
compiler's gas limit and are reported as warnings, never as success.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

import httpx
from almanak.framework.execution.interfaces import SigningError, SimulationResult, Simulator, UnsignedTransaction

from almanak_keeperhub.calldata import SelectorIndex
from almanak_keeperhub.client import ContractCall, KeeperHubClient, SequenceOutcome
from almanak_keeperhub.errors import (
    KeeperHubAPIError,
    KeeperHubRateLimited,
    KeeperHubUnavailable,
)
from almanak_keeperhub.notify import notifier_from_env
from almanak_keeperhub.receipts import ReceiptLog
from almanak_keeperhub.signer import call_for

logger = logging.getLogger(__name__)
SIMULATOR_NAME = "keeperhub"
FALLBACK_GAS = 300_000


class KeeperHubSimulator(Simulator):
    def __init__(
        self,
        client: KeeperHubClient,
        address: str,
        index: SelectorIndex | None = None,
        sleep: Any = None,
    ) -> None:
        self._sleep = sleep or asyncio.sleep
        self._client = client
        self._address = address
        self._index = index

    async def simulate(
        self,
        txs: list[UnsignedTransaction],
        chain: str,
        state_overrides: dict | None = None,
    ) -> SimulationResult:
        if state_overrides:
            logger.warning("KeeperHubSimulator ignores state_overrides; the org wallet's live state is simulated")
        if not txs:
            return SimulationResult(success=True, simulated=False, simulator_name=SIMULATOR_NAME)

        try:
            calls = [call_for(tx, index=self._index, raw_calldata=self._client.capabilities.raw_calldata) for tx in txs]
        except SigningError as exc:
            return _failure(str(exc), simulated=False)

        if len(calls) > 1:
            try:
                sequence = await self._with_retries(functools.partial(self._client.simulate_call_sequence, calls))
            except KeeperHubAPIError as exc:
                return _failure(f"KeeperHub simulator unavailable: {exc}", simulated=False)
            if sequence is not None:
                return await self._from_sequence(txs, calls, sequence)

        gas_estimates: list[int] = []
        warnings: list[str] = []
        for index, (tx, call) in enumerate(zip(txs, calls, strict=True)):
            if index > 0:
                fallback = tx.gas_limit if tx.gas_limit and tx.gas_limit > 0 else FALLBACK_GAS
                gas_estimates.append(fallback)
                warnings.append(
                    f"tx[{index}] depends on state written by tx[{index - 1}]; this KeeperHub cannot chain calls, "
                    f"using compiler gas limit {fallback}"
                )
                continue
            try:
                outcome = await self._with_retries(functools.partial(self._client.simulate_contract_call, call))
            except KeeperHubAPIError as exc:
                return _failure(f"KeeperHub simulator unavailable: {exc}", simulated=False)
            if not outcome.success:
                return await self._refuse(tx, call, outcome.revert_reason, outcome.code, outcome.would_revert)
            gas_estimates.append(outcome.gas_estimate or tx.gas_limit or FALLBACK_GAS)
            self._record_ok(tx, call, outcome.gas_estimate, outcome.sender)

        return SimulationResult(
            success=True,
            simulated=True,
            gas_estimates=gas_estimates,
            warnings=warnings,
            simulator_name=SIMULATOR_NAME,
        )

    async def _with_retries(self, request):  # noqa: ANN001, ANN202 - a zero-arg coroutine factory
        for attempt in range(3):
            try:
                return await request()
            except (KeeperHubRateLimited, KeeperHubUnavailable, httpx.TransportError) as exc:
                if attempt == 2:
                    raise KeeperHubAPIError(str(exc), status=getattr(exc, "status", 0)) from exc
                delay = float(getattr(exc, "retry_after_seconds", 0) or 0) or 2.0 * (attempt + 1)
                logger.warning("KeeperHub simulate retry %d/3 in %.0fs: %s", attempt + 1, delay, exc)
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _from_sequence(
        self, txs: list[UnsignedTransaction], calls: list[ContractCall], sequence: SequenceOutcome
    ) -> SimulationResult:
        gas_estimates: list[int] = []
        for index, (tx, call, result) in enumerate(zip(txs, calls, sequence.results, strict=False)):
            if not result.success:
                reason = result.revert_reason or "simulation failed"
                if result.failure_kind == "unavailable":
                    reason = f"tx[{index}] could not be simulated against the state tx[{index - 1}] produces: {reason}"
                else:
                    reason = f"tx[{index}] {call.label()} would fail after tx[0..{index - 1}]: {reason}"
                return await self._refuse(tx, call, reason, result.code, result.would_revert)
            gas_estimates.append(result.gas_estimate or tx.gas_limit or FALLBACK_GAS)
            self._record_ok(tx, call, result.gas_estimate, result.sender, mechanism=sequence.mechanism)
        if len(gas_estimates) < len(txs):
            return _failure("KeeperHub answered for fewer calls than were sent", simulated=False)
        return SimulationResult(
            success=True, simulated=True, gas_estimates=gas_estimates, warnings=[], simulator_name=SIMULATOR_NAME
        )

    async def _refuse(
        self, tx: UnsignedTransaction, call: ContractCall, reason: str | None, code: str | None, would_revert: bool
    ) -> SimulationResult:
        reason = reason or "simulation failed"
        if code:
            reason = f"{reason} [code={code}]"
        ReceiptLog().record_simulation(
            chain_id=int(tx.chain_id),
            to=str(tx.to),
            function=call.label(),
            success=False,
            would_revert=bool(would_revert),
            error=reason,
        )
        await notifier_from_env().send(
            "refused by dry run", f"{call.label()} -> {tx.to} on chain {tx.chain_id}: {reason}", None
        )
        return _failure(reason, simulated=True)

    def _record_ok(
        self,
        tx: UnsignedTransaction,
        call: ContractCall,
        gas_estimate: int | None,
        sender: str | None,
        mechanism: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "chain_id": int(tx.chain_id),
            "to": str(tx.to),
            "function": call.label(),
            "success": True,
            "would_revert": False,
            "gas_estimate": gas_estimate,
        }
        if mechanism:
            fields["mechanism"] = mechanism
        ReceiptLog().record_simulation(**fields)
        logger.info(
            "KeeperHub simulate ok: %s.%s gas=%s from=%s%s",
            tx.to,
            call.label(),
            gas_estimate,
            sender,
            f" via {mechanism}" if mechanism else "",
        )


def _failure(reason: str, *, simulated: bool) -> SimulationResult:
    return SimulationResult(success=False, simulated=simulated, revert_reason=reason, simulator_name=SIMULATOR_NAME)
