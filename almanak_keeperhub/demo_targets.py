"""Chain-specific targets for the demos and the benchmark.

``ALMANAK_KEEPERHUB_CHAIN`` selects ``base`` (default, mainnet) or
``base_sepolia`` (the free path). The vault on Base Sepolia is the
``contracts/TestVault.sol`` you deployed; pass it in ``ALMANAK_KEEPERHUB_VAULT``
or let it be read from ``demos/metamorpho_base_sepolia/config.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from almanak_keeperhub.testnet import BASE_SEPOLIA_CHAIN_ID, BASE_SEPOLIA_USDC

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DemoTargets:
    chain: str
    chain_id: int
    usdc: str
    vault: str
    explorer: str


def _vault_from_config(demo_dir: str) -> str | None:
    try:
        return (
            str(json.loads((REPO / "demos" / demo_dir / "config.json").read_text()).get("vault_address") or "") or None
        )
    except (OSError, ValueError):
        return None


def demo_targets() -> DemoTargets:
    chain = os.environ.get("ALMANAK_KEEPERHUB_CHAIN", "base").strip().lower()
    if chain in ("base_sepolia", "base-sepolia", "basesepolia"):
        vault = os.environ.get("ALMANAK_KEEPERHUB_VAULT") or _vault_from_config("metamorpho_base_sepolia")
        if not vault:
            raise SystemExit("set ALMANAK_KEEPERHUB_VAULT to the TestVault you deployed (scripts/deploy_test_vault.sh)")
        return DemoTargets(
            "base_sepolia", BASE_SEPOLIA_CHAIN_ID, BASE_SEPOLIA_USDC, vault, "https://sepolia.basescan.org/tx/"
        )
    if chain != "base":
        raise SystemExit(f"ALMANAK_KEEPERHUB_CHAIN={chain!r} is not supported by the demos (base or base_sepolia)")
    vault = (
        os.environ.get("ALMANAK_KEEPERHUB_VAULT")
        or _vault_from_config("metamorpho_base_yield")
        or ("0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca")
    )
    return DemoTargets("base", 8453, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", vault, "https://basescan.org/tx/")
