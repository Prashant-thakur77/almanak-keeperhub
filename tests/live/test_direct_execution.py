"""Each test is one sentence of KeeperHub's Direct Execution docs, checked against the live API.

The sentence checked is in the docstring; the section is the docs heading it comes from
(docs/api/direct-execution.md in KeeperHub/keeperhub).
"""

from __future__ import annotations

import asyncio
import warnings

import httpx
import pytest

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.errors import KeeperHubAuthError, KeeperHubIdempotencyConflict

from .conftest import approve, fresh_key, impossible_deposit


def doc(section: str):
    return pytest.mark.doc(section)


@doc("Authentication")
async def test_the_api_key_names_one_organization_wallet(client: KeeperHubClient, wallet: str) -> None:
    """A key belongs to an organization; its wallet is what every execution is sent from."""
    assert wallet.startswith("0x") and len(wallet) == 42


@doc("Authentication")
async def test_a_bad_key_is_refused_with_401() -> None:
    bad = KeeperHubClient(api_key="kh_not_a_real_key")
    try:
        with pytest.raises(KeeperHubAuthError):
            await bad.wallet_address()
    finally:
        await bad.aclose()


@doc("Dry-Run Simulation")
async def test_a_valid_write_dry_runs_with_a_gas_estimate_from_the_org_wallet(
    client: KeeperHubClient, wallet: str
) -> None:
    """simulate: true returns success, a gasEstimate, and the sender it would use."""
    outcome = await client.simulate_contract_call(approve())
    assert outcome.success is True and outcome.would_revert is False
    assert outcome.gas_estimate and outcome.gas_estimate > 21_000
    assert (outcome.sender or "").lower() == wallet.lower()


@doc("Dry-Run Simulation / Response - would-revert")
async def test_an_impossible_write_is_refused_before_broadcast_with_a_reason(
    client: KeeperHubClient, wallet: str
) -> None:
    """A call that would revert answers wouldRevert with the revert reason and creates no execution."""
    outcome = await client.simulate_contract_call(impossible_deposit(wallet))
    assert outcome.success is False and outcome.would_revert is True
    assert outcome.revert_reason
    assert "executionId" not in outcome.raw


@doc("Idempotency / Recognising a replay")
async def test_the_same_key_replays_the_first_execution_instead_of_sending_again(client: KeeperHubClient) -> None:
    """A retry with the same Idempotency-Key returns the original executionId and idempotentReplay: true."""
    key = fresh_key("replay")
    first = await client.execute_contract_call(approve(), idempotency_key=key)
    assert first.execution_id and first.idempotent_replay is False
    again = await client.execute_contract_call(approve(), idempotency_key=key)
    assert again.execution_id == first.execution_id
    assert again.idempotent_replay is True


@doc("Idempotency / When to reuse a key, and when to rotate it")
async def test_the_same_key_with_a_different_body_is_a_conflict(client: KeeperHubClient) -> None:
    """Reusing a key for different work is refused with 409 idempotency_conflict, naming the original."""
    key = fresh_key("conflict")
    first = await client.execute_contract_call(approve(1), idempotency_key=key)
    with pytest.raises(KeeperHubIdempotencyConflict) as excinfo:
        await client.execute_contract_call(approve(2), idempotency_key=key)
    assert excinfo.value.original_execution_id == first.execution_id


@doc("Get Execution Status")
async def test_status_reaches_a_terminal_state_with_a_verified_receipt(client: KeeperHubClient) -> None:
    """The status endpoint ends in completed with receipts[].verified true and a poll-interval hint."""
    envelope = await client.execute_contract_call(approve(), idempotency_key=fresh_key("status"))
    deadline = asyncio.get_running_loop().time() + 180
    while True:
        status = await client.execution_status(envelope.execution_id)
        if status.terminal:
            break
        assert asyncio.get_running_loop().time() < deadline, "execution did not settle in 180s"
        await asyncio.sleep(status.poll_hint_seconds or 3)
    assert status.status == "completed"
    assert status.receipts and status.receipts[0].verified is True
    assert status.receipts[0].receipt_status == "success"
    assert status.transaction_hash and status.transaction_link


@doc("Sponsored Executions")
async def test_a_base_sepolia_write_completes_sponsored_or_paid_from_the_wallet(client: KeeperHubClient) -> None:
    """On Base Sepolia the execution is gas sponsored, or, when Turnkey declines sponsorship for the wallet
    (which the docs say can happen), it completes paid from the wallet's own native balance. Either way it
    completes and says which in `sponsored`."""
    envelope = await client.execute_contract_call(approve(), idempotency_key=fresh_key("sponsored"))
    for _ in range(60):
        status = await client.execution_status(envelope.execution_id)
        if status.terminal:
            break
        await asyncio.sleep(3)
    assert status.status == "completed"
    assert status.sponsored in (True, False)
    if not status.sponsored:
        warnings.warn("KeeperHub did not sponsor this execution; the org wallet paid the gas", stacklevel=1)


@doc("Call Smart Contract / Raw calldata")
async def test_raw_calldata_is_either_accepted_or_refused_the_old_way(client: KeeperHubClient) -> None:
    """Until the merged change deploys, a body with data is refused for missing functionName; after, it dry-runs."""
    call = approve()
    response = await client._http.post("/api/execute/contract-call", json={**call.body(raw=True), "simulate": True})
    if response.status_code == 400:
        assert response.json().get("field") == "functionName"
    else:
        assert response.status_code == 200 and response.json().get("success") is True


@doc("Dry-Run Simulation / A sequence of calls")
async def test_a_call_sequence_is_either_simulated_in_order_or_refused_the_old_way(client: KeeperHubClient) -> None:
    """Until the merged change deploys, calls[] is refused for missing contractAddress; after, results[] comes back in order."""
    outcome = await client.simulate_call_sequence([approve(), approve(2)])
    if outcome is None:
        assert client.capabilities.call_sequence is False
    else:
        assert len(outcome.results) == 2 and outcome.atomic is False


@doc("Rate Limits")
async def test_status_responses_carry_a_poll_interval_hint(client: KeeperHubClient) -> None:
    """GET status sets X-Poll-Interval-Hint so clients back off the way the server asks."""
    envelope = await client.execute_contract_call(approve(), idempotency_key=fresh_key("hint"))
    response: httpx.Response = await client._http.get(f"/api/execute/{envelope.execution_id}/status")
    assert response.headers.get("X-Poll-Interval-Hint") is not None
