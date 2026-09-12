"""The compounder workflow Almanak hands to KeeperHub: free-tier nodes only, bounded amounts."""

from __future__ import annotations

import json

from almanak_keeperhub.keeper import build_compounder_workflow

VAULT = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
ORG = "0x0bdf000000000000000000000000000000000001"


def _workflow():
    return build_compounder_workflow(
        vault=VAULT,
        token=USDC,
        token_symbol="USDC",
        chain_id=8453,
        wallet=ORG,
        min_amount="1",
        max_amount="90",
        cron="0 */6 * * *",
    )


def test_shape_matches_keeperhub_create_body() -> None:
    wf = _workflow()

    assert set(wf) >= {"name", "description", "nodes", "edges"}
    assert wf["enabled"] is False  # created disabled; enabling is an explicit step
    ids = [n["id"] for n in wf["nodes"]]
    assert ids == ["trigger", "balance", "gate", "approve", "deposit"]
    assert all(n["type"] in ("trigger", "action") for n in wf["nodes"])
    assert all("label" in n["data"] and "config" in n["data"] for n in wf["nodes"])


def test_uses_only_free_tier_actions() -> None:
    wf = _workflow()
    actions = [n["data"]["config"].get("actionType") for n in wf["nodes"] if n["type"] == "action"]
    assert actions == ["web3/check-token-balance", "Condition", "web3/approve-token", "morpho/vault-deposit"]
    forbidden = {"code/run-code", "http-request", "webhook", "database-query"}
    assert not forbidden & set(actions)


def test_schedule_and_bounded_gate() -> None:
    wf = _workflow()
    trigger = wf["nodes"][0]["data"]["config"]
    assert trigger == {"triggerType": "Schedule", "scheduleCron": "0 */6 * * *", "scheduleTimezone": "UTC"}
    gate = wf["nodes"][2]["data"]["config"]
    rules = gate["conditionConfig"]["group"]["rules"]
    assert gate["conditionConfig"]["group"]["logic"] == "AND"
    assert [(r["operator"], r["rightOperand"]) for r in rules] == [(">=", "1"), ("<=", "90")]
    assert rules[0]["leftOperand"] == "{{@balance:Idle USDC.balance.balance}}"


def test_approve_and_deposit_reference_the_balance_node() -> None:
    wf = _workflow()
    approve = wf["nodes"][3]["data"]["config"]
    deposit = wf["nodes"][4]["data"]["config"]
    assert approve["spenderAddress"] == VAULT
    assert approve["amount"] == "{{@balance:Idle USDC.balance.balance}}"  # human units, as the field expects
    assert json.loads(approve["tokenConfig"])["customToken"]["address"] == USDC
    assert deposit["contractAddress"] == VAULT
    assert deposit["assets"] == "{{@balance:Idle USDC.balance.balanceRaw}}"  # uint256 raw
    assert deposit["receiver"] == ORG
    meta = json.loads(deposit["_protocolMeta"])
    assert meta == {"protocolSlug": "morpho", "contractKey": "vault", "functionName": "deposit", "actionType": "write"}


def test_edges_follow_the_true_branch_of_the_gate() -> None:
    wf = _workflow()
    edges = {(e["source"], e["target"]): e for e in wf["edges"]}
    assert set(edges) == {("trigger", "balance"), ("balance", "gate"), ("gate", "approve"), ("approve", "deposit")}
    assert edges[("gate", "approve")]["sourceHandle"] == "true"


def test_defaults_come_from_the_strategy_config(tmp_path) -> None:
    from almanak_keeperhub.keeper import keeper_params_from_config

    (tmp_path / "config.json").write_text(
        json.dumps({"chain": "base", "vault_address": VAULT, "deposit_token": "USDC", "deposit_token_decimals": 6})
    )

    params = keeper_params_from_config(tmp_path / "config.json")

    assert params["vault"] == VAULT
    assert params["token_symbol"] == "USDC"
    assert params["chain_id"] == 8453
    assert params["token"].lower() == USDC.lower()
