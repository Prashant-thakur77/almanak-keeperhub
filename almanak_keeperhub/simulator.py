"""KeeperHubSimulator: Almanak's dry run, executed by KeeperHub without touching the chain.

Mirrors Almanak's LocalSimulator bundle rule: only the first transaction of a
multi-transaction bundle is simulated against live state, because later ones
depend on state the earlier ones create (approve before deposit). Those keep
the compiler's gas limit and are reported as warnings, never as success.
"""

from __future__ import annotations

import logging

import httpx
from almanak.framework.execution.interfaces import SimulationResult, Simulator, UnsignedTransaction

from almanak_keeperhub.calldata import SelectorIndex, decode_calldata
from almanak_keeperhub.client import ContractCall, KeeperHubClient
from almanak_keeperhub.errors import KeeperHubAPIError, UndecodableCalldata

logger = logging.getLogger(__name__)
SIMULATOR_NAME = "keeperhub"
FALLBACK_GAS = 300_000


class KeeperHubSimulator(Simulator):
    def __init__(self, client: KeeperHubClient, address: str, index: SelectorIndex | None = None) -> None:
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

        gas_estimates: list[int] = []
        warnings: list[str] = []
        for index, tx in enumerate(txs):
            if index > 0:
                fallback = tx.gas_limit if tx.gas_limit and tx.gas_limit > 0 else FALLBACK_GAS
                gas_estimates.append(fallback)
                warnings.append(
                    f"tx[{index}] depends on state written by tx[{index - 1}]; KeeperHub simulate cannot chain calls, "
                    f"using compiler gas limit {fallback}"
                )
                continue
            try:
                decoded = decode_calldata(
                    tx.data or "0x", to=str(tx.to), value_wei=int(tx.value or 0), index=self._index
                )
            except UndecodableCalldata as exc:
                return _failure(str(exc), simulated=False)
            call = ContractCall(
                contract_address=str(tx.to),
                chain_id=int(tx.chain_id),
                function_name=decoded.function_name,
                function_args=decoded.function_args,
                abi=decoded.abi,
                value_wei=int(tx.value or 0),
            )
            try:
                outcome = await self._client.simulate_contract_call(call)
            except (KeeperHubAPIError, httpx.TransportError) as exc:
                return _failure(f"KeeperHub simulator unavailable: {exc}", simulated=False)
            if not outcome.success:
                reason = outcome.revert_reason or "simulation failed"
                if outcome.code:
                    reason = f"{reason} [code={outcome.code}]"
                return _failure(reason, simulated=True)
            gas_estimates.append(outcome.gas_estimate or tx.gas_limit or FALLBACK_GAS)
            logger.info(
                "KeeperHub simulate ok: %s.%s gas=%s from=%s",
                tx.to,
                decoded.function_name,
                outcome.gas_estimate,
                outcome.sender,
            )

        return SimulationResult(
            success=True,
            simulated=True,
            gas_estimates=gas_estimates,
            warnings=warnings,
            simulator_name=SIMULATOR_NAME,
        )


def _failure(reason: str, *, simulated: bool) -> SimulationResult:
    return SimulationResult(success=False, simulated=simulated, revert_reason=reason, simulator_name=SIMULATOR_NAME)
