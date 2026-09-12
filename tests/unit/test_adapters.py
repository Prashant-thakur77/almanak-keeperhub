"""KeeperHubSigner / KeeperHubSubmitter / KeeperHubSimulator against Almanak's real interfaces."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from almanak.framework.execution.interfaces import (
    Signer,
    SigningError,
    SimulationResult,
    Simulator,
    SubmissionError,
    SubmissionResult,
    Submitter,
    TransactionReceipt,
    TransactionType,
    UnsignedTransaction,
)
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.signer import KeeperHubSignedTransaction, KeeperHubSigner
from almanak_keeperhub.simulator import KeeperHubSimulator
from almanak_keeperhub.submitter import KeeperHubSubmitter

BASE = "https://kh.test"
ORG_WALLET = to_checksum_address("0x0bdf000000000000000000000000000000000001")
VAULT = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TX_HASH = "0x" + "ab" * 32
EXEC_URL = f"{BASE}/api/execute/contract-call"


def _data(signature: str, types: list[str], args: list[Any]) -> str:
    return "0x" + (function_signature_to_4byte_selector(signature) + encode(types, args)).hex()


def approve_tx(nonce: int = 7, amount: int = 5_000_000) -> UnsignedTransaction:
    return UnsignedTransaction(
        to=USDC,
        value=0,
        data=_data("approve(address,uint256)", ["address", "uint256"], [VAULT, amount]),
        chain_id=8453,
        gas_limit=60_000,
        nonce=nonce,
        tx_type=TransactionType.EIP_1559,
        from_address=ORG_WALLET,
        max_fee_per_gas=1,
        max_priority_fee_per_gas=1,
        metadata={
            "tx_type": "approve",
            "description": "approve USDC for vault",
            "intent_type": "vault_deposit",
        },
    )


def deposit_tx(nonce: int = 8) -> UnsignedTransaction:
    return UnsignedTransaction(
        to=VAULT,
        value=0,
        data=_data("deposit(uint256,address)", ["uint256", "address"], [5_000_000, ORG_WALLET]),
        chain_id=8453,
        gas_limit=450_000,
        nonce=nonce,
        tx_type=TransactionType.EIP_1559,
        from_address=ORG_WALLET,
        max_fee_per_gas=1,
        max_priority_fee_per_gas=1,
        metadata={"tx_type": "deposit", "description": "deposit USDC", "intent_type": "vault_deposit"},
    )


def completed(execution_id: str = "exec-1", tx_hash: str = TX_HASH, **extra: Any) -> dict[str, Any]:
    return {
        "executionId": execution_id,
        "status": "completed",
        "transactionHash": tx_hash,
        "transactionLink": f"https://basescan.org/tx/{tx_hash}",
        **extra,
    }


def status_body(execution_id: str, status: str, tx_hash: str | None = TX_HASH, verified: bool = True) -> dict[str, Any]:
    receipts = (
        [
            {
                "hash": tx_hash,
                "chainId": 8453,
                "verified": verified,
                "receiptStatus": "success" if verified else "reverted",
                "blockNumber": 100,
                "gasUsed": "68115",
            }
        ]
        if tx_hash
        else []
    )
    return {
        "executionId": execution_id,
        "status": status,
        "transactionHash": tx_hash,
        "receipts": receipts,
        "sponsored": True,
    }


@pytest.fixture
def client() -> KeeperHubClient:
    return KeeperHubClient(api_key="kh_test", base_url=BASE)


@pytest.fixture
def signer(client: KeeperHubClient) -> KeeperHubSigner:
    return KeeperHubSigner(client=client, address=ORG_WALLET)


# --- signer -----------------------------------------------------------------


def test_signer_implements_almanak_interface(signer: KeeperHubSigner) -> None:
    assert isinstance(signer, Signer)
    assert signer.address == ORG_WALLET


async def test_sign_decodes_call_and_derives_stable_idempotency_key(signer: KeeperHubSigner) -> None:
    signed = await signer.sign(approve_tx(), "base")

    assert isinstance(signed, KeeperHubSignedTransaction)
    assert signed.raw_tx == "0x"  # nothing is signed locally; KeeperHub's Turnkey wallet signs
    assert signed.unsigned_tx.nonce == 7
    assert signed.call.function_name == "approve"
    assert signed.call.function_args == [VAULT, "5000000"]
    assert signed.call.chain_id == 8453
    assert len(signed.idempotency_key) == 64
    # The placeholder hash is derived from the key so the orchestrator can index the tx before broadcast.
    assert signed.tx_hash == "0x" + signed.idempotency_key

    again = await signer.sign(approve_tx(), "base")
    assert again.idempotency_key == signed.idempotency_key


async def test_idempotency_key_identifies_the_work_not_the_attempt(signer: KeeperHubSigner) -> None:
    from almanak_keeperhub.signer import work_id_scope

    with work_id_scope("intent-1"):
        first_attempt = (await signer.sign(approve_tx(nonce=7), "base")).idempotency_key
        # The orchestrator assigns a fresh nonce per attempt; a retry of the same intent must reuse the key.
        retry = (await signer.sign(approve_tx(nonce=8), "base")).idempotency_key
    with work_id_scope("intent-2"):
        other_intent = (await signer.sign(approve_tx(nonce=7), "base")).idempotency_key

    assert retry == first_attempt
    assert other_intent != first_attempt
    assert (await signer.sign(approve_tx(amount=6_000_000), "base")).idempotency_key != first_attempt


async def test_idempotency_key_honours_salt(client: KeeperHubClient, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = await KeeperHubSigner(client=client, address=ORG_WALLET).sign(approve_tx(), "base")
    monkeypatch.setenv("ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT", "run-2")
    salted = await KeeperHubSigner(client=client, address=ORG_WALLET).sign(approve_tx(), "base")
    assert plain.idempotency_key != salted.idempotency_key


async def test_sign_refuses_unknown_selector_as_signing_error(signer: KeeperHubSigner) -> None:
    tx = approve_tx()
    tx.data = "0xdeadbeef" + "00" * 64
    with pytest.raises(SigningError) as excinfo:
        await signer.sign(tx, "base")
    assert "0xdeadbeef" in str(excinfo.value)


async def test_sign_refuses_contract_creation(signer: KeeperHubSigner) -> None:
    tx = approve_tx()
    tx.to = None
    with pytest.raises(SigningError):
        await signer.sign(tx, "base")


async def test_sign_refuses_sender_mismatch(signer: KeeperHubSigner) -> None:
    tx = approve_tx()
    tx.from_address = "0x1111111111111111111111111111111111111111"
    with pytest.raises(SigningError) as excinfo:
        await signer.sign(tx, "base")
    assert "from_address" in str(excinfo.value)


# --- submitter --------------------------------------------------------------


def _submitter(client: KeeperHubClient, receipts: dict[str, dict[str, Any]] | None = None) -> KeeperHubSubmitter:
    async def fetch_receipt(tx_hash: str) -> dict[str, Any] | None:
        return (receipts or {}).get(tx_hash)

    async def no_sleep(_: float) -> None:
        return None

    return KeeperHubSubmitter(client=client, receipt_fetcher=fetch_receipt, sleep=no_sleep)


def rpc_receipt(tx_hash: str = TX_HASH, status: int = 1) -> dict[str, Any]:
    return {
        "transactionHash": tx_hash,
        "blockNumber": 100,
        "blockHash": "0x" + "cd" * 32,
        "gasUsed": 68115,
        "effectiveGasPrice": 1163827869,
        "status": status,
        "logs": [{"address": USDC, "topics": ["0x8c5be1e5"], "data": "0x01"}],
        "from": ORG_WALLET,
        "to": USDC,
        "contractAddress": None,
    }


def test_submitter_implements_almanak_interface(client: KeeperHubClient) -> None:
    assert isinstance(_submitter(client), Submitter)


@respx.mock
async def test_submit_broadcasts_with_key_and_replaces_placeholder_hash(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    route = respx.post(EXEC_URL).mock(return_value=httpx.Response(202, json=completed()))
    signed = await signer.sign(approve_tx(), "base")

    results = await _submitter(client).submit([signed])

    assert route.calls[0].request.headers["idempotency-key"] == signed.idempotency_key
    assert json.loads(route.calls[0].request.content)["functionName"] == "approve"
    assert results == [SubmissionResult(tx_hash=TX_HASH, submitted=True, submitted_at=results[0].submitted_at)]
    assert signed.tx_hash == TX_HASH  # orchestrator indexes results by this


@respx.mock
async def test_submit_is_sequential_and_waits_for_confirmation_between_calls(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    second_hash = "0x" + "ef" * 32
    respx.post(EXEC_URL).mock(
        side_effect=[
            httpx.Response(202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}),
            httpx.Response(202, json=completed("e2", second_hash)),
        ]
    )
    status_route = respx.get(f"{BASE}/api/execute/e1/status").mock(
        side_effect=[
            httpx.Response(
                200,
                headers={"X-Poll-Interval-Hint": "1"},
                json=status_body("e1", "unconfirmed", verified=False),
            ),
            httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed")),
        ]
    )
    approve = await signer.sign(approve_tx(), "base")
    deposit = await signer.sign(deposit_tx(), "base")

    results = await _submitter(client).submit([approve, deposit])

    assert [r.tx_hash for r in results] == [TX_HASH, second_hash]
    assert status_route.call_count == 2  # polled until terminal before the deposit was sent


@respx.mock
async def test_submit_stops_after_a_refused_broadcast(client: KeeperHubClient, signer: KeeperHubSigner) -> None:
    route = respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202,
            json={
                "executionId": "e1",
                "status": "failed",
                "error": "Stablecoin transfer of 150 USDC exceeds the 100.0 USD per-transaction limit",
            },
        )
    )
    approve = await signer.sign(approve_tx(), "base")
    deposit = await signer.sign(deposit_tx(), "base")

    results = await _submitter(client).submit([approve, deposit])

    assert route.call_count == 1
    assert results[0].submitted is False
    assert "per-transaction limit" in results[0].error
    assert results[0].tx_hash == ""
    assert len(results) == 2 and results[1].submitted is False


@respx.mock
async def test_submit_retries_same_key_while_first_attempt_in_progress(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    route = respx.post(EXEC_URL).mock(
        side_effect=[
            httpx.Response(409, json={"code": "idempotency_in_progress", "retryable": True, "error": "processing"}),
            httpx.Response(202, json=completed(idempotentReplay=True)),
        ]
    )
    signed = await signer.sign(approve_tx(), "base")

    results = await _submitter(client).submit([signed])

    keys = {c.request.headers["idempotency-key"] for c in route.calls}
    assert keys == {signed.idempotency_key}
    assert results[0].submitted is True


@respx.mock
async def test_submit_maps_daily_cap_to_submission_error(client: KeeperHubClient, signer: KeeperHubSigner) -> None:
    respx.post(EXEC_URL).mock(return_value=httpx.Response(403, json={"error": "Daily spending cap exceeded"}))
    signed = await signer.sign(approve_tx(), "base")
    with pytest.raises(SubmissionError) as excinfo:
        await _submitter(client).submit([signed])
    assert "Daily spending cap" in str(excinfo.value)
    assert excinfo.value.recoverable is False


@respx.mock
async def test_get_receipt_waits_for_verified_status_then_reads_chain_receipt(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        side_effect=[
            httpx.Response(
                200,
                headers={"X-Poll-Interval-Hint": "1"},
                json=status_body("e1", "unconfirmed", verified=False),
            ),
            httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed")),
        ]
    )
    submitter = _submitter(client, receipts={TX_HASH: rpc_receipt()})
    signed = await signer.sign(approve_tx(), "base")
    await submitter.submit([signed])

    receipt = await submitter.get_receipt(TX_HASH, timeout=30)

    assert isinstance(receipt, TransactionReceipt)
    assert receipt.success is True
    assert receipt.block_number == 100
    assert receipt.gas_used == 68115
    assert receipt.effective_gas_price == 1163827869
    assert receipt.logs[0]["address"] == USDC
    assert submitter.execution_for(TX_HASH).execution_id == "e1"
    assert submitter.execution_for(TX_HASH).sponsored is True


@respx.mock
async def test_get_receipt_reports_revert_from_chain(client: KeeperHubClient, signer: KeeperHubSigner) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(202, json={**completed(), "status": "failed", "error": "execution reverted"})
    )
    respx.get(f"{BASE}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(
            200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("exec-1", "failed", verified=False)
        )
    )
    submitter = _submitter(client, receipts={TX_HASH: rpc_receipt(status=0)})
    signed = await signer.sign(approve_tx(), "base")
    await submitter.submit([signed])

    receipt = await submitter.get_receipt(TX_HASH, timeout=30)

    assert receipt.success is False


async def test_get_receipt_for_unknown_hash_raises(client: KeeperHubClient) -> None:
    with pytest.raises(SubmissionError):
        await _submitter(client).get_receipt("0x" + "00" * 32, timeout=1)


# --- simulator --------------------------------------------------------------


def _simulator(client: KeeperHubClient) -> KeeperHubSimulator:
    return KeeperHubSimulator(client=client, address=ORG_WALLET)


def test_simulator_implements_almanak_interface(client: KeeperHubClient) -> None:
    assert isinstance(_simulator(client), Simulator)


@respx.mock
async def test_simulate_success_returns_keeperhub_gas_estimate(client: KeeperHubClient) -> None:
    route = respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "gasEstimate": "357000", "wouldRevert": False, "from": ORG_WALLET}
        )
    )

    result = await _simulator(client).simulate([deposit_tx()], "base")

    assert isinstance(result, SimulationResult)
    assert result.success is True
    assert result.simulated is True
    assert result.gas_estimates == [357000]
    assert result.simulator_name == "keeperhub"
    assert json.loads(route.calls[0].request.content)["simulate"] is True


@respx.mock
async def test_simulate_revert_fails_with_decoded_reason(client: KeeperHubClient) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "failureKind": "revert",
                "wouldRevert": True,
                "revertReason": "Error(ERC20: transfer amount exceeds balance)",
            },
        )
    )
    result = await _simulator(client).simulate([deposit_tx()], "base")
    assert result.success is False
    assert result.revert_reason == "Error(ERC20: transfer amount exceeds balance)"


@respx.mock
async def test_simulate_only_first_tx_of_bundle_and_warns_for_dependents(client: KeeperHubClient) -> None:
    route = respx.post(EXEC_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "gasEstimate": "46000", "wouldRevert": False})
    )
    result = await _simulator(client).simulate([approve_tx(), deposit_tx()], "base")

    assert route.call_count == 1
    assert result.success is True
    assert result.gas_estimates == [46000, 450_000]  # second uses the compiler gas limit
    assert any("depends on" in w for w in result.warnings)


async def test_simulate_never_raises_for_undecodable_calldata(client: KeeperHubClient) -> None:
    tx = deposit_tx()
    tx.data = "0xdeadbeef"
    result = await _simulator(client).simulate([tx], "base")
    assert result.success is False
    assert "0xdeadbeef" in (result.revert_reason or "")


@respx.mock
async def test_simulate_fails_closed_when_keeperhub_unavailable(client: KeeperHubClient) -> None:
    respx.post(EXEC_URL).mock(return_value=httpx.Response(503, json={"failureKind": "unavailable"}))
    result = await _simulator(client).simulate([deposit_tx()], "base")
    assert result.success is False
    assert result.simulated is False


@respx.mock
async def test_submitter_records_every_execution_for_proof(
    client: KeeperHubClient, signer: KeeperHubSigner, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from almanak_keeperhub.receipts import ReceiptLog

    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(tmp_path / "receipts.json"))
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        return_value=httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed"))
    )
    submitter = _submitter(client, receipts={TX_HASH: rpc_receipt()})
    signed = await signer.sign(approve_tx(), "base")

    await submitter.submit([signed])
    await submitter.get_receipt(TX_HASH, timeout=30)

    entries = ReceiptLog(tmp_path / "receipts.json").entries_since("2000-01-01")
    assert len(entries) == 1
    assert entries[0]["execution_id"] == "e1"
    assert entries[0]["tx_hash"] == TX_HASH
    assert entries[0]["function"] == "approve"
    assert entries[0]["to"] == USDC
    assert entries[0]["chain_id"] == 8453
    assert entries[0]["status"] == "completed"
    assert entries[0]["verified"] is True
    assert entries[0]["sponsored"] is True
    assert entries[0]["idempotency_key"] == signed.idempotency_key


@respx.mock
async def test_submit_stops_the_bundle_after_an_onchain_revert(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    route = respx.post(EXEC_URL).mock(
        return_value=httpx.Response(202, json={**completed("e1"), "status": "failed", "error": "execution reverted"})
    )
    approve = await signer.sign(approve_tx(), "base")
    deposit = await signer.sign(deposit_tx(), "base")

    results = await _submitter(client).submit([approve, deposit])

    assert route.call_count == 1  # the deposit depends on the approve; it is never sent
    assert len(results) == 2
    assert results[0].submitted is True  # it reached the chain: the receipt phase reports the revert
    assert results[0].tx_hash == TX_HASH
    assert results[1].submitted is False  # the orchestrator must see the bundle was not completed
    assert results[1].tx_hash == ""
    assert "not sent" in (results[1].error or "")


@respx.mock
async def test_unconfirmed_past_timeout_is_a_recoverable_submission_error(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        return_value=httpx.Response(
            200, headers={"X-Poll-Interval-Hint": "5"}, json=status_body("e1", "unconfirmed", verified=False)
        )
    )
    clock = iter([0.0, 10.0, 200.0, 400.0])
    submitter = KeeperHubSubmitter(
        client=client,
        receipt_fetcher=_submitter(client)._fetch_receipt,
        sleep=_submitter(client)._sleep,
        now=lambda: next(clock),
    )
    signed = await signer.sign(approve_tx(), "base")
    await submitter.submit([signed])

    with pytest.raises(SubmissionError) as excinfo:
        await submitter.get_receipt(TX_HASH, timeout=30)

    assert excinfo.value.tx_hash == TX_HASH
    assert excinfo.value.recoverable is True
    assert "do not resend" in str(excinfo.value)


@respx.mock
async def test_intermediate_settle_timeout_returns_partial_results_with_the_hash(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        return_value=httpx.Response(
            200, headers={"X-Poll-Interval-Hint": "5"}, json=status_body("e1", "unconfirmed", verified=False)
        )
    )
    clock = iter([0.0, 10.0, 400.0, 800.0, 1600.0])
    base = _submitter(client)
    submitter = KeeperHubSubmitter(
        client=client,
        receipt_fetcher=base._fetch_receipt,
        sleep=base._sleep,
        now=lambda: next(clock),
        confirmation_timeout=30,
    )
    approve = await signer.sign(approve_tx(), "base")
    deposit = await signer.sign(deposit_tx(), "base")

    results = await submitter.submit([approve, deposit])

    assert results[0].submitted is True and results[0].tx_hash == TX_HASH
    assert results[1].submitted is False and "unconfirmed" in (results[1].error or "")


@respx.mock
async def test_broadcast_retries_transport_errors_under_the_same_key(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    route = respx.post(EXEC_URL).mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(503, json={"error": "upstream"}),
            httpx.Response(202, json=completed()),
        ]
    )
    signed = await signer.sign(approve_tx(), "base")

    results = await _submitter(client).submit([signed])

    assert results[0].submitted is True
    assert {c.request.headers["idempotency-key"] for c in route.calls} == {signed.idempotency_key}


@respx.mock
async def test_broadcast_gives_up_as_recoverable_submission_error(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(side_effect=httpx.ConnectError("down"))
    signed = await signer.sign(approve_tx(), "base")
    base = _submitter(client)
    submitter = KeeperHubSubmitter(
        client=client, receipt_fetcher=base._fetch_receipt, sleep=base._sleep, max_in_progress_retries=2
    )

    with pytest.raises(SubmissionError) as excinfo:
        await submitter.submit([signed])
    assert excinfo.value.recoverable is True


@respx.mock
async def test_hashless_non_terminal_envelope_is_polled_before_deciding(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(return_value=httpx.Response(202, json={"executionId": "e1", "status": "unconfirmed"}))
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        side_effect=[
            httpx.Response(
                200, headers={"X-Poll-Interval-Hint": "1"}, json=status_body("e1", "unconfirmed", tx_hash=None)
            ),
            httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed")),
        ]
    )
    signed = await signer.sign(approve_tx(), "base")

    results = await _submitter(client).submit([signed])

    assert results[0].submitted is True
    assert results[0].tx_hash == TX_HASH
    assert signed.tx_hash == TX_HASH


@respx.mock
async def test_get_receipt_trusts_keeperhub_when_it_reports_safe_inner_failure(
    client: KeeperHubClient, signer: KeeperHubSigner
) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(202, json={**completed(), "status": "failed", "error": "safe inner call failed"})
    )
    body = status_body("exec-1", "failed", verified=True)
    body["receipts"][0]["receiptStatus"] = "safe_inner_failure"
    respx.get(f"{BASE}/api/execute/exec-1/status").mock(
        return_value=httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=body)
    )
    submitter = _submitter(client, receipts={TX_HASH: rpc_receipt(status=1)})  # the outer Safe tx succeeded
    signed = await signer.sign(approve_tx(), "base")
    await submitter.submit([signed])

    receipt = await submitter.get_receipt(TX_HASH, timeout=30)

    assert receipt.success is False


@respx.mock
async def test_status_polling_survives_rate_limits_and_5xx(client: KeeperHubClient, signer: KeeperHubSigner) -> None:
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "Rate limit exceeded"}),
            httpx.Response(502, json={"error": "bad gateway"}),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed")),
        ]
    )
    submitter = _submitter(client, receipts={TX_HASH: rpc_receipt()})
    signed = await signer.sign(approve_tx(), "base")
    await submitter.submit([signed])

    receipt = await submitter.get_receipt(TX_HASH, timeout=60)

    assert receipt.success is True


@respx.mock
async def test_simulator_reports_transport_failure_instead_of_raising(client: KeeperHubClient) -> None:
    respx.post(EXEC_URL).mock(side_effect=httpx.ConnectError("down"))
    result = await _simulator(client).simulate([deposit_tx()], "base")
    assert result.success is False
    assert result.simulated is False


@respx.mock
async def test_a_new_process_resumes_settlement_from_the_receipts_log(
    client: KeeperHubClient, signer: KeeperHubSigner, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash after broadcast: the next process knows only the hash Almanak persisted."""
    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(tmp_path / "receipts.json"))
    respx.post(EXEC_URL).mock(
        return_value=httpx.Response(
            202, json={"executionId": "e1", "status": "unconfirmed", "transactionHash": TX_HASH}
        )
    )
    respx.get(f"{BASE}/api/execute/e1/status").mock(
        return_value=httpx.Response(200, headers={"X-Poll-Interval-Hint": "0"}, json=status_body("e1", "completed"))
    )
    first_process = _submitter(client, receipts={TX_HASH: rpc_receipt()})
    signed = await signer.sign(approve_tx(), "base")
    await first_process.submit([signed])
    # crash here: nothing settled, only keeperhub-receipts.json and Almanak's session hold the hash

    second_process = _submitter(client, receipts={TX_HASH: rpc_receipt()})
    receipt = await second_process.get_receipt(TX_HASH, timeout=30)

    assert receipt.success is True
    assert second_process.execution_for(TX_HASH).execution_id == "e1"
