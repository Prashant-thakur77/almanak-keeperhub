"""Wallet registry plugin: makes the KeeperHub organization wallet Almanak's execution identity.

Almanak's gateway discovers a registry through the ``almanak.wallets`` entry
point group when ``ALMANAK_GATEWAY_WALLETS`` is set
(almanak/gateway/_server_start_helpers.py::load_wallet_registry). The runner
then pins ``runtime_config.wallet_address`` to what ``resolve(chain)`` returns,
so balances, accounting and signing all refer to the same address.

Configure with ``ALMANAK_GATEWAY_WALLETS='{"base": {"kind": "keeperhub"}}'``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from almanak_keeperhub.client import DEFAULT_BASE_URL

KIND = "keeperhub"


@dataclass(frozen=True)
class ResolvedKeeperHubWallet:
    chain: str
    account_address: str  # the address whose balances Almanak reads and that is msg.sender on chain
    kind: str = KIND
    config: dict[str, Any] = field(default_factory=dict)
    private_key: str | None = None  # never set: KeeperHub's Turnkey enclave holds the key
    signer_address: str | None = None  # the org EOA when the acting account is a Safe


SAFE_ENV = "KEEPERHUB_SAFE_ADDRESS"


class KeeperHubWalletRegistry:
    """Every chain resolves to the KeeperHub organization wallet.

    Almanak's production shape is one Safe per chain with Zodiac Roles. When the
    organization has configured that Safe as its sender in KeeperHub
    (docs/wallet-management/safe.md), set ``KEEPERHUB_SAFE_ADDRESS``: Almanak then
    treats the Safe as the account (balances, ``from_address``, receipts) while
    KeeperHub wraps each call through the Safe and signs with the org EOA.
    """

    def __init__(self, address: str, chains: list[str], safe_address: str | None = None) -> None:
        self._address = address
        self._safe = safe_address
        self._chains = list(chains)

    @classmethod
    def from_env(cls, default_chains: list[str] | None = None) -> KeeperHubWalletRegistry:
        raw = os.environ.get("ALMANAK_GATEWAY_WALLETS", "")
        configured = json.loads(raw) if raw else {}
        chains = [chain for chain, cfg in configured.items() if isinstance(cfg, dict) and cfg.get("kind") == KIND]
        if not chains and default_chains:
            chains = list(default_chains)
        return cls(address=resolve_wallet_address(), chains=chains, safe_address=os.environ.get(SAFE_ENV) or None)

    def all_chains(self) -> list[str]:
        return list(self._chains)

    def resolve(self, chain: str) -> ResolvedKeeperHubWallet:
        if chain not in self._chains:
            raise KeyError(chain)
        if self._safe:
            return ResolvedKeeperHubWallet(chain=chain, account_address=self._safe, signer_address=self._address)
        return ResolvedKeeperHubWallet(chain=chain, account_address=self._address)


def resolve_wallet_address() -> str:
    """Org wallet from ``GET /api/user``, cross-checked against ``KEEPERHUB_WALLET_ADDRESS`` (sync: gateway boot).

    An explicit address is only a shortcut for the same answer. If the API names a different
    wallet, the run is refused: Almanak would compile every intent (deposit receivers, redeem
    owners) for one address while KeeperHub signs and sends from another. A fork rehearsal
    once did exactly that through a ``.env`` Almanak loaded on its own, and the shares went
    to a wallet the rehearsal did not control.
    """
    explicit = os.environ.get("KEEPERHUB_WALLET_ADDRESS")
    api_key = os.environ.get("KEEPERHUB_API_KEY", "")
    if not api_key:
        if explicit:
            return explicit
        raise RuntimeError("KEEPERHUB_API_KEY is not set; create an organization API key with mcp:write scope")
    base_url = os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    address = None
    for path in ("/api/user", "/api/user/wallet"):
        response = httpx.get(f"{base_url}{path}", headers=headers, timeout=30.0)
        if response.status_code != 200:
            raise RuntimeError(f"GET {base_url}{path} returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            address = response.json().get("walletAddress")
        except ValueError:
            address = None
        if address:
            break
    if not address:
        raise RuntimeError(
            "KeeperHub returned no walletAddress; provision the organization wallet in the app "
            "(Settings > Organization > Wallets) or set KEEPERHUB_WALLET_ADDRESS"
        )
    if explicit and explicit.lower() != str(address).lower():
        raise RuntimeError(
            f"KEEPERHUB_WALLET_ADDRESS is {explicit} but {base_url} signs as {address}; refusing to compile "
            "intents for a wallet KeeperHub will not send from. Unset the variable or point it at that wallet."
        )
    return str(address)
