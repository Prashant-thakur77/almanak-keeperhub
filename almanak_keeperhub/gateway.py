"""Gateway servicer override: the one seam where Almanak picks signer, submitter and simulator.

Almanak's ``ExecutionServiceServicer._get_orchestrator`` hardcodes
``PublicMempoolSubmitter`` and ``create_simulator`` (execution_service.py).
This subclass keeps everything else the parent does (RPC resolution, wallet
registry, risk config, managed-fork flag) and only swaps the three execution
interfaces when the resolved wallet is a KeeperHub wallet.

``install()`` points ``almanak.gateway.server`` at this subclass so the managed
gateway that ``almanak strat run`` starts in-process uses it. The same three
lines are the proposed upstream change (patches/almanak-keeperhub-backend.diff).
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any

from almanak.gateway.services.execution_service import ExecutionServiceServicer

from almanak_keeperhub.client import DEFAULT_BASE_URL, KeeperHubClient
from almanak_keeperhub.signer import KeeperHubSigner
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter
from almanak_keeperhub.wallets import KIND

logger = logging.getLogger(__name__)


def client_from_env() -> KeeperHubClient:
    api_key = os.environ.get("KEEPERHUB_API_KEY", "")
    base_url = os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL)
    return KeeperHubClient(api_key=api_key, base_url=base_url)


def _with_parent(method):  # small indirection so the override is testable without a real gateway
    @functools.wraps(method)
    async def wrapper(self, chain: str, wallet_address: str, parent_get=None):
        if parent_get is None:
            parent_get = functools.partial(super(KeeperHubExecutionServiceServicer, self)._get_orchestrator)
        return await method(self, chain, wallet_address, parent_get)

    wrapper.__wrapped__ = method  # type: ignore[attr-defined]
    return wrapper


class KeeperHubExecutionServiceServicer(ExecutionServiceServicer):
    _keeperhub_client: KeeperHubClient | None = None

    def _client(self) -> KeeperHubClient:
        if self._keeperhub_client is None:
            self._keeperhub_client = client_from_env()
        return self._keeperhub_client

    def _create_signer_from_resolved(self, wallet: Any):
        if getattr(wallet, "kind", None) == KIND:
            logger.info("Using KeeperHubSigner for %s on %s", wallet.account_address[:10], wallet.chain)
            return KeeperHubSigner(client=self._client(), address=wallet.account_address)
        return super()._create_signer_from_resolved(wallet)

    @_with_parent
    async def _get_orchestrator(self, chain: str, wallet_address: str, parent_get):
        orchestrator = await parent_get(chain, wallet_address)
        signer = orchestrator.signer
        if isinstance(signer, KeeperHubSigner) and not isinstance(orchestrator.submitter, KeeperHubSubmitter):
            client = self._client()
            orchestrator.submitter = KeeperHubSubmitter(client=client, rpc_url=orchestrator.rpc_url)
            orchestrator.simulator = KeeperHubSimulator(client=client, address=signer.address)
            logger.info("KeeperHub execution backend active for chain=%s wallet=%s", chain, signer.address[:10])
        return orchestrator


def install() -> None:
    """Make the in-process managed gateway construct the KeeperHub servicer."""
    import almanak.gateway.server as server

    if server.ExecutionServiceServicer is not KeeperHubExecutionServiceServicer:
        server.ExecutionServiceServicer = KeeperHubExecutionServiceServicer
