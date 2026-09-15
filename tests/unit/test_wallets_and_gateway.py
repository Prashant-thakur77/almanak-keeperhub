"""Wallet registry plugin (almanak.wallets entry point) and gateway servicer override."""

from __future__ import annotations

import json
from importlib.metadata import entry_points

import httpx
import pytest
import respx
from almanak.gateway.services.execution_service import ExecutionServiceServicer

from almanak_keeperhub.gateway import KeeperHubExecutionServiceServicer, install
from almanak_keeperhub.signer import KeeperHubSigner
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter
from almanak_keeperhub.wallets import KeeperHubWalletRegistry, ResolvedKeeperHubWallet

BASE = "https://kh.test"
ORG_WALLET = "0x0BDf000000000000000000000000000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test")
    monkeypatch.setenv("KEEPERHUB_BASE_URL", BASE)
    monkeypatch.delenv("KEEPERHUB_WALLET_ADDRESS", raising=False)
    monkeypatch.setenv(
        "ALMANAK_GATEWAY_WALLETS",
        json.dumps({"base": {"kind": "keeperhub"}, "arbitrum": {"kind": "keeperhub"}}),
    )


def test_registry_is_discoverable_through_almanaks_entry_point_group() -> None:
    names = {(ep.name, ep.value) for ep in entry_points(group="almanak.wallets")}
    assert ("registry", "almanak_keeperhub.wallets:KeeperHubWalletRegistry") in names


@respx.mock
def test_registry_resolves_every_configured_chain_to_the_org_wallet() -> None:
    route = respx.get(f"{BASE}/api/user").mock(return_value=httpx.Response(200, json={"walletAddress": ORG_WALLET}))

    registry = KeeperHubWalletRegistry.from_env(default_chains=None)

    assert sorted(registry.all_chains()) == ["arbitrum", "base"]
    wallet = registry.resolve("base")
    assert isinstance(wallet, ResolvedKeeperHubWallet)
    assert wallet.kind == "keeperhub"
    assert wallet.account_address == ORG_WALLET
    assert wallet.chain == "base"
    assert route.call_count == 1  # fetched once for every chain


def test_registry_unknown_chain_raises_key_error_so_gateway_falls_back() -> None:
    registry = KeeperHubWalletRegistry(address=ORG_WALLET, chains=["base"])
    with pytest.raises(KeyError):
        registry.resolve("ethereum")


def test_registry_accepts_wallet_address_from_env_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_WALLET_ADDRESS", ORG_WALLET)
    monkeypatch.delenv("KEEPERHUB_API_KEY")
    registry = KeeperHubWalletRegistry.from_env(default_chains=["base"])
    assert registry.resolve("base").account_address == ORG_WALLET


@respx.mock
def test_an_explicit_wallet_that_disagrees_with_the_api_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise Almanak compiles intents for one wallet while KeeperHub sends from another."""
    monkeypatch.setenv("KEEPERHUB_WALLET_ADDRESS", "0x0bdf000000000000000000000000000000000002")
    respx.get(f"{BASE}/api/user").mock(return_value=httpx.Response(200, json={"walletAddress": ORG_WALLET}))
    with pytest.raises(RuntimeError, match="refusing to compile"):
        KeeperHubWalletRegistry.from_env(default_chains=["base"])


@respx.mock
def test_an_explicit_wallet_that_matches_the_api_is_fine_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_WALLET_ADDRESS", ORG_WALLET.lower())
    respx.get(f"{BASE}/api/user").mock(return_value=httpx.Response(200, json={"walletAddress": ORG_WALLET}))
    assert KeeperHubWalletRegistry.from_env(default_chains=["base"]).resolve("base").account_address == ORG_WALLET


def test_registry_ignores_chains_of_other_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_WALLET_ADDRESS", ORG_WALLET)
    monkeypatch.delenv("KEEPERHUB_API_KEY")
    monkeypatch.setenv(
        "ALMANAK_GATEWAY_WALLETS", json.dumps({"base": {"kind": "keeperhub"}, "ethereum": {"kind": "zodiac"}})
    )
    registry = KeeperHubWalletRegistry.from_env(default_chains=None)
    assert registry.all_chains() == ["base"]


def test_servicer_subclasses_almanaks_execution_service() -> None:
    assert issubclass(KeeperHubExecutionServiceServicer, ExecutionServiceServicer)


def test_servicer_builds_keeperhub_signer_for_keeperhub_wallet_kind() -> None:
    servicer = KeeperHubExecutionServiceServicer.__new__(KeeperHubExecutionServiceServicer)
    servicer._keeperhub_client = None  # lazily created
    wallet = ResolvedKeeperHubWallet(chain="base", account_address=ORG_WALLET)

    signer = servicer._create_signer_from_resolved(wallet)

    assert isinstance(signer, KeeperHubSigner)
    assert signer.address == ORG_WALLET


async def test_servicer_swaps_submitter_and_simulator_when_signer_is_keeperhub() -> None:
    class FakeOrchestrator:
        def __init__(self) -> None:
            self.signer = KeeperHubSigner(client=None, address=ORG_WALLET)  # type: ignore[arg-type]
            self.submitter = object()
            self.simulator = object()
            self.rpc_url = "https://rpc.test"

        async def execute(self, action_bundle, context):
            return None

    class Parent:
        async def _get_orchestrator(self, chain: str, wallet_address: str) -> FakeOrchestrator:
            return FakeOrchestrator()

    servicer = KeeperHubExecutionServiceServicer.__new__(KeeperHubExecutionServiceServicer)
    servicer._keeperhub_client = None
    orchestrator = await KeeperHubExecutionServiceServicer._get_orchestrator.__wrapped__(  # type: ignore[attr-defined]
        servicer, "base", ORG_WALLET, parent_get=Parent()._get_orchestrator
    )

    assert isinstance(orchestrator.submitter, KeeperHubSubmitter)
    assert isinstance(orchestrator.simulator, KeeperHubSimulator)


async def test_servicer_forces_the_dry_run_on_for_keeperhub_orchestrators() -> None:
    from almanak.framework.execution.orchestrator import ExecutionContext

    seen: list[ExecutionContext] = []

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.signer = KeeperHubSigner(client=None, address=ORG_WALLET)  # type: ignore[arg-type]
            self.submitter = object()
            self.simulator = object()
            self.rpc_url = "https://rpc.test"

        async def execute(self, action_bundle, context):
            seen.append(context)
            return "result"

    class Parent:
        async def _get_orchestrator(self, chain: str, wallet_address: str) -> FakeOrchestrator:
            return FakeOrchestrator()

    servicer = KeeperHubExecutionServiceServicer.__new__(KeeperHubExecutionServiceServicer)
    servicer._keeperhub_client = None
    orchestrator = await KeeperHubExecutionServiceServicer._get_orchestrator.__wrapped__(  # type: ignore[attr-defined]
        servicer, "base", ORG_WALLET, parent_get=Parent()._get_orchestrator
    )
    context = ExecutionContext(deployment_id="d", chain="base", wallet_address=ORG_WALLET)
    assert context.simulation_enabled is False  # Almanak's runner default on mainnet

    result = await orchestrator.execute(object(), context)

    assert result == "result"
    assert seen[0].simulation_enabled is True


def test_install_replaces_the_servicer_the_gateway_server_constructs() -> None:
    import almanak.gateway.server as server

    original = server.ExecutionServiceServicer
    try:
        install()
        assert server.ExecutionServiceServicer is KeeperHubExecutionServiceServicer
        install()  # idempotent
        assert server.ExecutionServiceServicer is KeeperHubExecutionServiceServicer
    finally:
        server.ExecutionServiceServicer = original


@respx.mock
def test_registry_falls_back_to_the_wallet_endpoint() -> None:
    respx.get(f"{BASE}/api/user").mock(return_value=httpx.Response(200, json={"id": "u1"}))
    respx.get(f"{BASE}/api/user/wallet").mock(return_value=httpx.Response(200, json={"walletAddress": ORG_WALLET}))

    registry = KeeperHubWalletRegistry.from_env(default_chains=["base"])

    assert registry.resolve("base").account_address == ORG_WALLET


async def test_simulate_only_env_turns_the_orchestrator_run_into_a_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from almanak.framework.execution.orchestrator import ExecutionContext

    seen: list[ExecutionContext] = []

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.signer = KeeperHubSigner(client=None, address=ORG_WALLET)  # type: ignore[arg-type]
            self.submitter = object()
            self.simulator = object()
            self.rpc_url = "https://rpc.test"

        async def execute(self, action_bundle, context):
            seen.append(context)
            return "result"

    class Parent:
        async def _get_orchestrator(self, chain: str, wallet_address: str) -> FakeOrchestrator:
            return FakeOrchestrator()

    monkeypatch.setenv("ALMANAK_KEEPERHUB_SIMULATE_ONLY", "1")
    servicer = KeeperHubExecutionServiceServicer.__new__(KeeperHubExecutionServiceServicer)
    servicer._keeperhub_client = None
    orchestrator = await KeeperHubExecutionServiceServicer._get_orchestrator.__wrapped__(  # type: ignore[attr-defined]
        servicer, "base", ORG_WALLET, parent_get=Parent()._get_orchestrator
    )
    context = ExecutionContext(deployment_id="d", chain="base", wallet_address=ORG_WALLET)

    await orchestrator.execute(object(), context)

    assert seen[0].simulation_enabled is True
    assert seen[0].dry_run is True


@respx.mock
def test_safe_address_becomes_the_acting_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Almanak deploys one Safe per chain; KeeperHub executes through the org's configured Safe."""
    safe = "0x5afe000000000000000000000000000000000001"
    monkeypatch.setenv("KEEPERHUB_SAFE_ADDRESS", safe)
    respx.get(f"{BASE}/api/user").mock(return_value=httpx.Response(200, json={"walletAddress": ORG_WALLET}))

    registry = KeeperHubWalletRegistry.from_env(default_chains=["base"])

    resolved = registry.resolve("base")
    assert resolved.account_address == safe
    assert resolved.signer_address == ORG_WALLET  # the EOA that signs inside Turnkey for the Safe
