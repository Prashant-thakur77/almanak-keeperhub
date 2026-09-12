"""Base Sepolia as a first-class Almanak chain, so nothing compiled there can target mainnet."""

from __future__ import annotations

from almanak.core.chains import ChainRegistry

from almanak_keeperhub.testnet import BASE_SEPOLIA_CHAIN_ID, BASE_SEPOLIA_USDC, register_testnets


def test_base_sepolia_registers_with_its_own_chain_id() -> None:
    descriptor = register_testnets()["base_sepolia"]

    assert descriptor.chain_id == BASE_SEPOLIA_CHAIN_ID == 84532
    assert ChainRegistry.resolve("base_sepolia").chain_id == 84532
    assert ChainRegistry.try_resolve_id(84532) is descriptor
    assert ChainRegistry.resolve("base").chain_id == 8453  # mainnet untouched


def test_base_sepolia_points_at_testnet_resources() -> None:
    descriptor = register_testnets()["base_sepolia"]

    assert descriptor.tokens["usdc"].lower() == BASE_SEPOLIA_USDC.lower()
    assert "sepolia" in descriptor.rpc.public_rpc
    assert "sepolia.basescan.org" in descriptor.explorer.browse_url
    assert descriptor.simulation.tenderly_supported is False


def test_registering_twice_is_idempotent() -> None:
    first = register_testnets()["base_sepolia"]
    second = register_testnets()["base_sepolia"]
    assert first is second


def test_testnet_tokens_resolve_on_almanaks_resolver() -> None:
    from almanak.framework.data.tokens import get_token_resolver

    register_testnets()
    resolver = get_token_resolver()
    usdc = resolver.resolve(BASE_SEPOLIA_USDC, "base_sepolia")
    assert usdc.decimals == 6 and usdc.chain_id == 84532


def test_metamorpho_connector_accepts_base_sepolia() -> None:
    from almanak.connectors._strategy_base.vaults import supported_vault_chains
    from almanak.connectors.morpho_vault import sdk

    register_testnets()
    assert "base_sepolia" in sdk.SUPPORTED_CHAINS
    assert "base_sepolia" in (supported_vault_chains("metamorpho") or set())


def test_demo_targets_switch_with_the_chain_env(monkeypatch) -> None:
    from almanak_keeperhub.demo_targets import demo_targets

    monkeypatch.setenv("ALMANAK_KEEPERHUB_CHAIN", "base_sepolia")
    monkeypatch.setenv("ALMANAK_KEEPERHUB_VAULT", "0x" + "ab" * 20)
    testnet = demo_targets()
    assert (testnet.chain_id, testnet.usdc.lower()) == (84532, BASE_SEPOLIA_USDC.lower())
    assert "sepolia" in testnet.explorer

    monkeypatch.setenv("ALMANAK_KEEPERHUB_CHAIN", "base")
    assert demo_targets().chain_id == 8453
