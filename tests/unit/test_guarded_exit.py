"""The guarded exit: KeeperHub re-reads the position before the redeem (check-and-execute)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from almanak_keeperhub.client import KeeperHubClient
from almanak_keeperhub.guarded_exit import GuardedExit, run_guarded_exit
from almanak_keeperhub.receipts import ReceiptLog

BASE = "https://kh.test"
URL = f"{BASE}/api/execute/check-and-execute"
VAULT = "0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04"
WALLET = "0xe7DbACbDD4Cb2ddfF5681dCD9E56Fcf488E36Ac9"
TX = "0x" + "7c" * 32


def exit_(shares: int = 5_000_000, work_id: str = "decision-1") -> GuardedExit:
    return GuardedExit(vault=VAULT, chain_id=84532, wallet=WALLET, shares=shares, work_id=work_id)


def test_the_request_is_the_documented_shape() -> None:
    e = exit_()
    assert e.check.body() == {
        "contractAddress": VAULT,
        "chainId": 84532,
        "functionName": "balanceOf",
        "functionArgs": json.dumps([WALLET]),
        "abi": e.check.body()["abi"],
    }
    action = e.action.body()
    assert action["functionName"] == "redeem"
    assert json.loads(action["functionArgs"]) == ["5000000", WALLET, WALLET]
    assert e.action.data and e.action.data.startswith("0xba087652")  # redeem(uint256,address,address)
    assert e.idempotency_key() == exit_().idempotency_key()  # a retry of the same decision
    assert e.idempotency_key() != exit_(1).idempotency_key()
    # Two exits of the same size are different decisions when the position was rebuilt in between;
    # the first CI run proved it: a same-shape key replayed an old redeem and left the position open.
    assert e.idempotency_key() != exit_(work_id="decision-2").idempotency_key()


@respx.mock
async def test_a_met_guard_redeems_and_the_receipt_is_recorded(tmp_path: Path) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "executed": True,
                "executionId": "n3364uzl2s6aram5v558c",
                "status": "completed",
                "transactionHash": TX,
                "conditionResult": {
                    "met": True,
                    "observedValue": "5000000",
                    "targetValue": "5000000",
                    "operator": "gte",
                },
            },
        )
    )
    respx.get(f"{BASE}/api/execute/n3364uzl2s6aram5v558c/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "executionId": "n3364uzl2s6aram5v558c",
                "status": "completed",
                "transactionHash": TX,
                "sponsored": True,
                "receipts": [{"hash": TX, "chainId": 84532, "verified": True, "receiptStatus": "success"}],
            },
            headers={"X-Poll-Interval-Hint": "0"},
        )
    )
    client = KeeperHubClient(api_key="kh_test", base_url=BASE)
    log = ReceiptLog(tmp_path / "keeperhub-receipts.json")

    outcome = await run_guarded_exit(client, exit_(), receipts=log)

    body = json.loads(route.calls[0].request.content)
    assert body["condition"] == {"operator": "gte", "value": "5000000"}
    assert body["action"]["functionName"] == "redeem" and "chainId" not in body["action"]
    assert route.calls[0].request.headers["idempotency-key"] == exit_().idempotency_key()
    assert outcome.executed and outcome.condition.met and outcome.transaction_hash == TX
    entry = log.find_by_hash(TX)
    assert entry and entry["guarded"] is True and entry["verified"] is True and entry["function"] == "redeem"
    await client.aclose()


@respx.mock
async def test_a_stale_decision_is_not_executed_and_is_recorded_as_a_refusal(tmp_path: Path) -> None:
    """The position was already closed: KeeperHub observes 0, the guard fails, nothing is broadcast."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "executed": False,
                "conditionResult": {"met": False, "observedValue": "0", "targetValue": "5000000", "operator": "gte"},
            },
        )
    )
    client = KeeperHubClient(api_key="kh_test", base_url=BASE)
    log = ReceiptLog(tmp_path / "keeperhub-receipts.json")

    outcome = await run_guarded_exit(client, exit_(), receipts=log)

    assert outcome.executed is False and outcome.condition.observed_value == "0"
    assert outcome.execution_id is None
    rows = log.entries_since("2000")
    assert len(rows) == 1 and rows[0]["type"] == "simulation" and rows[0]["guarded"] is True
    assert "guard not met" in rows[0]["error"]
    await client.aclose()


@respx.mock
async def test_simulate_dry_runs_the_guard_without_a_key(tmp_path: Path) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "status": "simulated",
                "executed": True,
                "gasEstimate": "67899",
                "conditionResult": {
                    "met": True,
                    "observedValue": "5000000",
                    "targetValue": "5000000",
                    "operator": "gte",
                },
            },
        )
    )
    client = KeeperHubClient(api_key="kh_test", base_url=BASE)
    log = ReceiptLog(tmp_path / "keeperhub-receipts.json")

    outcome = await run_guarded_exit(client, exit_(), simulate=True, receipts=log)

    assert json.loads(route.calls[0].request.content)["simulate"] is True
    assert "idempotency-key" not in route.calls[0].request.headers
    assert outcome.executed and log.entries_since("2000")[0]["type"] == "simulation"
    await client.aclose()
