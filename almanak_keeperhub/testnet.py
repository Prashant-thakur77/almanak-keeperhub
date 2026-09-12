"""Base Sepolia as a first-class Almanak chain.

Almanak ships mainnet chain descriptors only. Its "sepolia" network mode swaps
the RPC URL but keeps the mainnet chain id in every compiled transaction, so a
strategy run that way would hand KeeperHub a Base mainnet transaction. This
module registers ``base_sepolia`` (chain id 84532) as its own chain, derived
from the Base descriptor, so a testnet strategy compiles testnet transactions
and KeeperHub executes them on Base Sepolia with sponsored gas.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

BASE_SEPOLIA_CHAIN_ID = 84532
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Circle's test USDC (KeeperHub platform reference)
BASE_SEPOLIA_WETH = "0x4200000000000000000000000000000000000006"
BASE_SEPOLIA_RPC = "https://sepolia.base.org"
RPC_ENV = "ALMANAK_BASE_SEPOLIA_RPC_URL"

_registered: dict[str, Any] = {}


def register_testnets() -> dict[str, Any]:
    """Register base_sepolia once; return the descriptors by name."""
    if _registered:
        return _registered
    from almanak.core.chains import ChainRegistry, base
    from almanak.core.chains._registry import register_chain

    existing = ChainRegistry.try_resolve("base_sepolia")
    if existing is not None:
        _registered["base_sepolia"] = existing
        return _registered
    mainnet = base.DESCRIPTOR
    descriptor = dataclasses.replace(
        mainnet,
        name="base_sepolia",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc=dataclasses.replace(
            mainnet.rpc,
            public_rpc=BASE_SEPOLIA_RPC,
            alchemy_prefix="base-sepolia",
            tenderly_subdomain=None,
            anvil_port=8549,
            fork_requires_archive=False,
        ),
        explorer=dataclasses.replace(
            mainnet.explorer,
            api_url="https://api-sepolia.basescan.org/api",
            browse_url="https://sepolia.basescan.org",
        ),
        tokens={"usdc": BASE_SEPOLIA_USDC, "weth": BASE_SEPOLIA_WETH},
        simulation=dataclasses.replace(mainnet.simulation, tenderly_supported=False, alchemy_network="base-sepolia"),
        anvil=dataclasses.replace(
            mainnet.anvil,
            funding_tokens={"USDC": BASE_SEPOLIA_USDC, "WETH": BASE_SEPOLIA_WETH},
            balance_slots={"USDC": 9, "WETH": 3},
            balance_storage_seeds={},
            whale_funded_tokens={},
        ),
        bridged_stablecoin_variants=(),
        aliases=("base-sepolia", "basesepolia"),
        default_display_tokens=("ETH", "WETH", "USDC"),
    )
    _registered["base_sepolia"] = register_chain(descriptor)
    # Almanak resolves RPCs from ALMANAK_{CHAIN}_RPC_URL first; give the testnet a working default.
    os.environ.setdefault(RPC_ENV, BASE_SEPOLIA_RPC)
    _register_tokens()
    _enable_metamorpho_vaults()
    return _registered


def _enable_metamorpho_vaults() -> None:
    """Let the ERC-4626 (MetaMorpho) connector compile on base_sepolia.

    The connector's chain universe is a plain set and the vault registry
    re-registers idempotently (its own docstring says so); the adapter itself is
    generic ERC-4626, which is what contracts/TestVault.sol implements.
    """
    from almanak.connectors._strategy_base.vaults import register_vault_adapter
    from almanak.connectors.morpho_vault import sdk

    sdk.SUPPORTED_CHAINS.add("base_sepolia")

    def build(*, chain: str, wallet_address: str, gateway_client: Any, token_resolver: Any):  # noqa: ANN202
        from almanak.connectors.morpho_vault.adapter import MetaMorphoAdapter, MetaMorphoConfig

        config = MetaMorphoConfig(chain=chain, wallet_address=wallet_address)
        return MetaMorphoAdapter(config, gateway_client=gateway_client, token_resolver=token_resolver)

    for protocol in ("metamorpho", "morpho_vault"):
        register_vault_adapter(protocol, build, supported_chains=sdk.SUPPORTED_CHAINS)


def _register_tokens() -> None:
    """Teach Almanak's token resolver the testnet's ETH, WETH and USDC.

    The resolver's static registry is mainnet-only; unknown symbols on a chain
    make balance reads fail, and the runner halts accounting on a failed native
    balance. The resolver documents ``register`` for exactly this case.
    """
    from almanak.framework.data.tokens import get_token_resolver
    from almanak.framework.data.tokens.models import ResolvedToken

    resolver = get_token_resolver()
    for symbol, address, decimals, native in (
        ("ETH", "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE", 18, True),
        ("WETH", BASE_SEPOLIA_WETH, 18, False),
        ("USDC", BASE_SEPOLIA_USDC, 6, False),
    ):
        resolver.register(
            ResolvedToken(
                symbol=symbol,
                address=address,
                decimals=decimals,
                chain="base_sepolia",
                chain_id=BASE_SEPOLIA_CHAIN_ID,
                is_native=native,
                source="manual",
            )
        )
