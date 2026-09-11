"""KeeperHubClient talks to the documented Direct Execution API shapes.

Response bodies below are copied from docs/api/direct-execution.md and
docs/api/user.md in the KeeperHub repository (staging, 2026-09-11).
"""

import json

import httpx
import pytest
import respx

from almanak_keeperhub.client import ContractCall, KeeperHubClient
from almanak_keeperhub.errors import (
    KeeperHubAuthError,
    KeeperHubIdempotencyConflict,
    KeeperHubIdempotencyInProgress,
    KeeperHubRateLimited,
    KeeperHubUnavailable,
)

BASE = "https://kh.test"
KEY = "kh_test_key"
VAULT = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
ABI = [
    {
        "type": "function",
        "name": "deposit",
        "inputs": [{"name": "arg0", "type": "uint256"}, {"name": "arg1", "type": "address"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    }
]


def _call(value_wei: int = 0) -> ContractCall:
    return ContractCall(
        contract_address=VAULT,
        chain_id=8453,
        function_name="deposit",
        function_args=["5000000", "0x0bdf000000000000000000000000000000000001"],
        abi=ABI,
        value_wei=value_wei,
    )


@pytest.fixture
def client() -> KeeperHubClient:
    return KeeperHubClient(api_key=KEY, base_url=BASE)


@respx.mock
async def test_wallet_address_comes_from_user_endpoint_and_is_cached(client: KeeperHubClient) -> None:
    route = respx.get(f"{BASE}/api/user").mock(
        return_value=httpx.Response(200, json={"id": "u1", "walletAddress": "0x0bdf000000000000000000000000000000000001"})
    )

    first = await client.wallet_address()
    second = await client.wallet_address()

    assert first == second == "0x0bdf000000000000000000000000000000000001"
    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_simulate_sends_boolean_flag_and_documented_fields(client: KeeperHubClient) -> None:
    route = respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "status": "simulated",
                "from": "0x0bdf000000000000000000000000000000000001",
                "to": VAULT,
                "value": "0",
                "gasEstimate": "357000",
                "simulatedReturnValue": None,
                "wouldRevert": False,
            },
        )
    )

    outcome = await client.simulate_contract_call(_call(value_wei=10**15))

    body = json.loads(route.calls[0].request.content)
    assert body["simulate"] is True
    assert body["contractAddress"] == VAULT
    assert body["chainId"] == 8453
    assert body["functionName"] == "deposit"
    assert json.loads(body["functionArgs"]) == ["5000000", "0x0bdf000000000000000000000000000000000001"]
    assert json.loads(body["abi"]) == ABI
    assert body["value"] == "0.001"
    assert "Idempotency-Key" not in route.calls[0].request.headers
    assert outcome.success is True
    assert outcome.would_revert is False
    assert outcome.gas_estimate == 357000
    assert outcome.sender == "0x0bdf000000000000000000000000000000000001"


@respx.mock
async def test_simulate_omits_value_when_zero(client: KeeperHubClient) -> None:
    route = respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"success": True, "gasEstimate": "1", "wouldRevert": False})
    )
    await client.simulate_contract_call(_call(value_wei=0))
    assert "value" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_simulate_reports_revert_as_failed_outcome_not_exception(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "status": "simulated",
                "failureKind": "revert",
                "wouldRevert": True,
                "revertReason": "Error(ERC20: transfer amount exceeds balance)",
                "error": "Error(ERC20: transfer amount exceeds balance)",
            },
        )
    )

    outcome = await client.simulate_contract_call(_call())

    assert outcome.success is False
    assert outcome.would_revert is True
    assert outcome.failure_kind == "revert"
    assert outcome.revert_reason == "Error(ERC20: transfer amount exceeds balance)"
    assert outcome.code is None


@respx.mock
async def test_simulate_surfaces_machine_readable_code(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "failureKind": "validation",
                "wouldRevert": True,
                "revertReason": "Insufficient ETH balance. Have: 0.25, Need: 1.0.",
                "error": "Insufficient ETH balance. Have: 0.25, Need: 1.0.",
                "code": "insufficient_balance",
                "balanceWei": "250000000000000000",
                "requiredWei": "1000000000000000000",
                "shortfallWei": "750000000000000000",
            },
        )
    )

    outcome = await client.simulate_contract_call(_call())

    assert outcome.code == "insufficient_balance"
    assert outcome.balance_wei == 250000000000000000
    assert outcome.required_wei == 1000000000000000000


@respx.mock
async def test_simulate_infrastructure_failure_raises(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(503, json={"success": False, "failureKind": "unavailable", "wouldRevert": False})
    )
    with pytest.raises(KeeperHubUnavailable):
        await client.simulate_contract_call(_call())


@respx.mock
async def test_execute_sends_idempotency_key_and_parses_envelope(client: KeeperHubClient) -> None:
    route = respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            202,
            json={
                "executionId": "n3364uzl2s6aram5v558c",
                "status": "completed",
                "transactionHash": "0xabc",
                "transactionLink": "https://basescan.org/tx/0xabc",
            },
        )
    )

    envelope = await client.execute_contract_call(_call(), idempotency_key="work-1")

    request = route.calls[0].request
    assert request.headers["idempotency-key"] == "work-1"
    assert "simulate" not in json.loads(request.content)
    assert envelope.execution_id == "n3364uzl2s6aram5v558c"
    assert envelope.status == "completed"
    assert envelope.transaction_hash == "0xabc"
    assert envelope.transaction_link == "https://basescan.org/tx/0xabc"
    assert envelope.idempotent_replay is False
    assert envelope.error is None


@respx.mock
async def test_execute_marks_replayed_response(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            202,
            json={"executionId": "orig", "status": "completed", "transactionHash": "0xabc", "idempotentReplay": True},
        )
    )
    envelope = await client.execute_contract_call(_call(), idempotency_key="work-1")
    assert envelope.idempotent_replay is True
    assert envelope.execution_id == "orig"


@respx.mock
async def test_execute_failed_without_hash_is_an_envelope_not_an_exception(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            202,
            json={
                "executionId": "x1",
                "status": "failed",
                "error": "Stablecoin transfer of 150 USDC exceeds the 100.0 USD per-transaction limit",
            },
        )
    )
    envelope = await client.execute_contract_call(_call(), idempotency_key="work-2")
    assert envelope.status == "failed"
    assert envelope.transaction_hash is None
    assert "per-transaction limit" in envelope.error


@respx.mock
async def test_execute_conflict_raises_with_original_execution(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            409,
            json={
                "error": "Idempotency-Key reused with a different body",
                "code": "idempotency_conflict",
                "retryable": False,
                "originalExecutionId": "orig-1",
            },
        )
    )
    with pytest.raises(KeeperHubIdempotencyConflict) as excinfo:
        await client.execute_contract_call(_call(), idempotency_key="work-3")
    assert excinfo.value.original_execution_id == "orig-1"
    assert excinfo.value.retryable is False


@respx.mock
async def test_execute_in_progress_raises_retryable(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            409, json={"error": "already being processed", "code": "idempotency_in_progress", "retryable": True}
        )
    )
    with pytest.raises(KeeperHubIdempotencyInProgress) as excinfo:
        await client.execute_contract_call(_call(), idempotency_key="work-4")
    assert excinfo.value.retryable is True


@respx.mock
async def test_execute_insufficient_scope_raises_auth_error(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": "insufficient_scope",
                "message": "This endpoint requires the `mcp:write` scope.",
                "retryable": False,
                "required_scope": "mcp:write",
                "granted_scope": "mcp:read",
            },
        )
    )
    with pytest.raises(KeeperHubAuthError) as excinfo:
        await client.execute_contract_call(_call(), idempotency_key="work-5")
    assert "mcp:write" in str(excinfo.value)


@respx.mock
async def test_rate_limit_raises_with_retry_after(client: KeeperHubClient) -> None:
    respx.post(f"{BASE}/api/execute/contract-call").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "Rate limit exceeded"})
    )
    with pytest.raises(KeeperHubRateLimited) as excinfo:
        await client.execute_contract_call(_call(), idempotency_key="work-6")
    assert excinfo.value.retry_after_seconds == 7


@respx.mock
async def test_execution_status_parses_receipts_and_poll_hint(client: KeeperHubClient) -> None:
    respx.get(f"{BASE}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(
            200,
            headers={"X-Poll-Interval-Hint": "0"},
            json={
                "executionId": "exec-1",
                "status": "completed",
                "type": "contract-call",
                "network": "8453",
                "transactionHash": "0xabc",
                "transactionLink": "https://basescan.org/tx/0xabc",
                "sponsored": True,
                "receipts": [
                    {
                        "hash": "0xabc",
                        "chainId": 8453,
                        "verified": True,
                        "receiptStatus": "success",
                        "blockNumber": 11413447,
                        "gasUsed": "68115",
                    }
                ],
                "gasUsedWei": "21000000000000",
                "gasPriceWei": "1163827869",
                "error": None,
            },
        )
    )

    status = await client.execution_status("exec-1")

    assert status.status == "completed"
    assert status.terminal is True
    assert status.poll_hint_seconds == 0
    assert status.sponsored is True
    assert status.transaction_hash == "0xabc"
    assert status.receipts[0].verified is True
    assert status.receipts[0].receipt_status == "success"
    assert status.receipts[0].block_number == 11413447
    assert status.receipts[0].gas_used == 68115


@respx.mock
async def test_wait_for_terminal_polls_until_hint_is_zero(client: KeeperHubClient) -> None:
    responses = [
        httpx.Response(200, headers={"X-Poll-Interval-Hint": "2"}, json={"executionId": "e", "status": "unconfirmed", "receipts": []}),
        httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json={"executionId": "e", "status": "completed", "transactionHash": "0x1", "receipts": []}),
    ]
    respx.get(f"{BASE}/api/execute/e/status").mock(side_effect=responses)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    status = await client.wait_for_terminal("e", timeout_seconds=30, sleep=fake_sleep)

    assert status.status == "completed"
    assert sleeps == [2.0]


@respx.mock
async def test_wait_for_terminal_times_out(client: KeeperHubClient) -> None:
    respx.get(f"{BASE}/api/execute/e/status").mock(
        return_value=httpx.Response(200, headers={"X-Poll-Interval-Hint": "5"}, json={"executionId": "e", "status": "unconfirmed", "receipts": []})
    )
    clock = iter([0.0, 10.0, 40.0])

    async def fake_sleep(seconds: float) -> None:
        return None

    with pytest.raises(TimeoutError):
        await client.wait_for_terminal("e", timeout_seconds=30, sleep=fake_sleep, now=lambda: next(clock))
