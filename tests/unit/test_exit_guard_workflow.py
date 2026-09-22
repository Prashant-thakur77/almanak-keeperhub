"""The guarded exit as a KeeperHub workflow: the Condition is the guard, the redeem sits behind it."""

from __future__ import annotations

import json

from almanak_keeperhub.exit_workflow import build_exit_guard_workflow, guard_outcome

VAULT = "0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04"
ORG = "0x0bdf000000000000000000000000000000000001"


def _workflow():
    return build_exit_guard_workflow(vault=VAULT, chain_id=84532, wallet=ORG, share_symbol="TVUSDC")


def test_shape_matches_keeperhub_create_body() -> None:
    wf = _workflow()

    assert set(wf) >= {"name", "description", "nodes", "edges"}
    assert wf["enabled"] is False
    assert [n["id"] for n in wf["nodes"]] == ["trigger", "shares", "gate", "redeem"]
    assert wf["nodes"][0]["data"]["config"] == {"triggerType": "Manual"}
    assert all("label" in n["data"] and "config" in n["data"] for n in wf["nodes"])


def test_uses_only_free_tier_actions() -> None:
    actions = [n["data"]["config"].get("actionType") for n in _workflow()["nodes"] if n["type"] == "action"]
    assert actions == ["web3/check-token-balance", "Condition", "morpho/vault-redeem"]


def test_the_guard_compares_shares_held_with_the_decision() -> None:
    gate = _workflow()["nodes"][2]["data"]["config"]
    rule = gate["conditionConfig"]["group"]["rules"][0]
    assert rule["operator"] == ">="
    assert rule["leftOperand"] == "{{@shares:Vault shares held.balance.balanceRaw}}"
    assert rule["rightOperand"] == "{{@trigger:Exit decision.shares}}"
    assert gate["condition"] == f"{rule['leftOperand']} >= {rule['rightOperand']}"


def test_shares_are_read_as_the_vault_token_balance_of_the_wallet() -> None:
    shares = _workflow()["nodes"][1]["data"]["config"]
    assert shares["address"] == ORG
    assert shares["network"] == "84532"
    assert json.loads(shares["tokenConfig"])["customToken"]["address"] == VAULT


def test_redeem_takes_the_decided_amount_to_the_wallet() -> None:
    redeem = _workflow()["nodes"][3]["data"]["config"]
    assert redeem["contractAddress"] == VAULT
    assert redeem["shares"] == "{{@trigger:Exit decision.shares}}"
    assert redeem["receiver"] == ORG and redeem["owner"] == ORG
    assert json.loads(redeem["_protocolMeta"]) == {
        "protocolSlug": "morpho",
        "contractKey": "vault",
        "functionName": "redeem",
        "actionType": "write",
    }


def test_redeem_hangs_off_the_true_branch_only() -> None:
    edges = {(e["source"], e["target"]): e for e in _workflow()["edges"]}
    assert set(edges) == {("trigger", "shares"), ("shares", "gate"), ("gate", "redeem")}
    assert edges[("gate", "redeem")]["sourceHandle"] == "true"


def test_guard_outcome_reads_a_refusal_and_an_execution() -> None:
    # What the status route returned for the real runs on 22 Sep 2026: nodeStatuses is
    # unordered and a node the Condition never released has no entry at all.
    stopped = guard_outcome(
        {
            "status": "success",
            "node_statuses": [
                {"nodeId": "trigger", "status": "success"},
                {"nodeId": "shares", "status": "success"},
                {"nodeId": "gate", "status": "success"},
            ],
            "progress": {"totalSteps": 4, "completedSteps": 3},
            "transaction_hashes": [],
        }
    )
    assert stopped["stopped_at_gate"] is True and stopped["executed"] is False
    assert stopped["trace"] == ["trigger:success", "shares:success", "gate:success"]
    assert stopped["steps"] == "3 of 4"

    landed = guard_outcome(
        {
            "status": "success",
            "node_statuses": [
                {"nodeId": "shares", "status": "success"},
                {"nodeId": "trigger", "status": "success"},
                {"nodeId": "gate", "status": "success"},
                {"nodeId": "redeem", "status": "success"},
            ],
            "progress": {"totalSteps": 4, "completedSteps": 4},
            "transaction_hashes": [{"nodeId": "redeem", "hash": "0xabc", "verified": True}],
        }
    )
    assert landed["executed"] is True and landed["stopped_at_gate"] is False
    assert landed["trace"][0] == "trigger:success" and landed["trace"][-1] == "redeem:success"

    failed = guard_outcome(
        {
            "status": "error",
            "node_statuses": [{"nodeId": "trigger", "status": "success"}, {"nodeId": "shares", "status": "error"}],
            "transaction_hashes": [],
            "error": "rpc",
        }
    )
    assert failed["executed"] is False and failed["stopped_at_gate"] is False and failed["error"] == "rpc"


def test_run_now_forwards_the_trigger_input() -> None:
    import asyncio

    import httpx
    import respx

    from almanak_keeperhub.client import KeeperHubClient
    from almanak_keeperhub.keeper import run_now

    seen: dict = {}

    async def go() -> dict:
        client = KeeperHubClient(api_key="kh_x", base_url="https://app.keeperhub.com")
        try:
            with respx.mock:
                route = respx.post("https://app.keeperhub.com/api/workflows/wf1/execute").mock(
                    return_value=httpx.Response(200, json={"executionId": "ex1", "status": "running"})
                )
                respx.get("https://app.keeperhub.com/api/workflows/executions/ex1/status").mock(
                    return_value=httpx.Response(200, json={"status": "success", "transactionHashes": []})
                )
                result = await run_now(client, "wf1", input={"shares": "5000000"}, sleep=_no_sleep)
                seen["body"] = json.loads(route.calls[0].request.content)
                return result
        finally:
            await client.aclose()

    result = asyncio.run(go())
    assert seen["body"] == {"input": {"shares": "5000000"}}
    assert result["status"] == "success"


async def _no_sleep(_seconds: float) -> None:
    return None


def test_run_stale_asks_for_one_share_more_than_held_and_reports_the_stop(tmp_path, monkeypatch) -> None:
    """`exit-guard run --stale` sends a decision the position cannot cover; the trace shows the
    Condition stopping the workflow and the exit code says the guard did its job."""
    import json

    from click.testing import CliRunner

    from almanak_keeperhub import cli, guarded_exit, keeper

    vault = "0x" + "d3" * 20
    wallet = "0x" + "e7" * 20
    (tmp_path / "keeperhub-exit-guard.json").write_text(
        json.dumps({"workflow_id": "zhanaalz8k47rrjspihct", "vault": vault, "wallet": wallet, "chain": "base_sepolia"})
    )
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test")
    monkeypatch.delenv("ALMANAK_KEEPERHUB_EXIT_GUARD_STATE", raising=False)
    asked: dict = {}

    async def fake_shares(rpc: str, vault_address: str, owner: str) -> int:
        return 5_000_000

    async def fake_run_now(client, workflow_id, *, input=None, **kwargs):
        asked.update({"workflow_id": workflow_id, "input": input})
        return {
            "execution_id": "qez8b9fhipm7c4zcqa6sg",
            "status": "completed",
            "transaction_hashes": [],
            "node_statuses": [
                {"nodeId": "gate", "status": "completed"},
                {"nodeId": "shares", "status": "completed"},
                {"nodeId": "trigger", "status": "completed"},
            ],
            "progress": {"totalSteps": 4, "completedSteps": 3},
            "error": None,
        }

    monkeypatch.setattr(guarded_exit, "current_shares", fake_shares)
    monkeypatch.setattr(keeper, "run_now", fake_run_now)
    result = CliRunner().invoke(
        cli.main, ["exit-guard", "run", "-d", str(tmp_path), "--chain", "base_sepolia", "--stale"]
    )
    assert result.exit_code == 0, result.output
    assert asked == {"workflow_id": "zhanaalz8k47rrjspihct", "input": {"shares": "5000001"}}
    assert "executed          : False" in result.output
    assert "trigger:completed -> shares:completed -> gate:completed  (3 of 4 steps)" in result.output
    assert "stopped at the Condition" in result.output
    state = json.loads((tmp_path / "keeperhub-exit-guard.json").read_text())
    assert state["manual_runs"][-1]["stopped_at_gate"] is True and state["manual_runs"][-1]["shares"] == 5_000_001
