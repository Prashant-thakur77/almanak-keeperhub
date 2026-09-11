"""Execution backend in the shape proposed upstream (patches/almanak-execution-backend.diff).

Almanak does not load this yet; ``gateway.py`` does the same job through a subclass.
"""

from __future__ import annotations

from typing import Any

from almanak_keeperhub.gateway import client_from_env
from almanak_keeperhub.signer import KeeperHubSigner
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter


class KeeperHubBackend:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = client_from_env()
        return self._client

    def create_signer(self, wallet: Any, *, settings: Any) -> KeeperHubSigner:
        signer = KeeperHubSigner(client=self._get_client(), address=wallet.account_address)
        signer.execution_backend = "keeperhub"  # type: ignore[attr-defined]
        return signer

    def create_submitter(self, signer: Any, *, chain: str, rpc_url: str, settings: Any) -> KeeperHubSubmitter:
        return KeeperHubSubmitter(client=self._get_client(), rpc_url=rpc_url)

    def create_simulator(self, signer: Any, *, chain: str, rpc_url: str, settings: Any) -> KeeperHubSimulator:
        return KeeperHubSimulator(client=self._get_client(), address=signer.address)
