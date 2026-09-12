"""The keeper Almanak hands to KeeperHub: a scheduled workflow that compounds idle USDC.

Almanak decides entry and exit on its own tick. Between ticks, and when no
Almanak process is running at all, this KeeperHub workflow keeps small idle
balances working: every N hours it reads the wallet's idle token balance and,
when the balance is inside a bounded window, approves and deposits it into the
same vault. Balances above the window are left alone: sizing a large move is
the strategy's decision, not the keeper's.

Free-tier nodes only (Schedule trigger, ERC-20 balance read, Condition,
approve, Morpho vault deposit). The node shapes mirror KeeperHub's own seed
workflows and are checked against its validator in tests/e2e.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from almanak_keeperhub.client import KeeperHubClient

BALANCE_LABEL = "Idle {symbol}"


def _ref(node_id: str, label: str, field: str) -> str:
    """KeeperHub's stored template form: {{@nodeId:Label.field}} (docs/workflows/templating.md)."""
    return f"{{{{@{node_id}:{label}.{field}}}}}"


def build_compounder_workflow(
    *,
    vault: str,
    token: str,
    token_symbol: str,
    chain_id: int,
    wallet: str,
    min_amount: str = "1",
    max_amount: str = "90",
    cron: str = "0 */6 * * *",
    name: str | None = None,
) -> dict[str, Any]:
    label = BALANCE_LABEL.format(symbol=token_symbol)
    human = _ref("balance", label, "balance.balance")
    raw = _ref("balance", label, "balance.balanceRaw")
    network = str(chain_id)
    nodes = [
        {
            "id": "trigger",
            "type": "trigger",
            "data": {
                "type": "trigger",
                "label": "Compound schedule",
                "config": {"triggerType": "Schedule", "scheduleCron": cron, "scheduleTimezone": "UTC"},
            },
        },
        {
            "id": "balance",
            "type": "action",
            "data": {
                "type": "action",
                "label": label,
                "config": {
                    "actionType": "web3/check-token-balance",
                    "network": network,
                    "address": wallet,
                    "tokenConfig": json.dumps(
                        {"mode": "custom", "customToken": {"address": token, "symbol": token_symbol}}
                    ),
                },
            },
        },
        {
            "id": "gate",
            "type": "action",
            "data": {
                "type": "action",
                "label": f"Between {min_amount} and {max_amount} {token_symbol}",
                "config": {
                    "actionType": "Condition",
                    "condition": f"{human} >= {min_amount} && {human} <= {max_amount}",
                    "conditionConfig": {
                        "group": {
                            "id": "window",
                            "logic": "AND",
                            "rules": [
                                {
                                    "id": "at-least-min",
                                    "operator": ">=",
                                    "leftOperand": human,
                                    "rightOperand": min_amount,
                                },
                                {
                                    "id": "at-most-max",
                                    "operator": "<=",
                                    "leftOperand": human,
                                    "rightOperand": max_amount,
                                },
                            ],
                        }
                    },
                },
            },
        },
        {
            "id": "approve",
            "type": "action",
            "data": {
                "type": "action",
                "label": f"Approve {token_symbol} for the vault",
                "config": {
                    "actionType": "web3/approve-token",
                    "network": network,
                    "tokenConfig": json.dumps(
                        {"mode": "custom", "customToken": {"address": token, "symbol": token_symbol}}
                    ),
                    "spenderAddress": vault,
                    "amount": human,
                },
            },
        },
        {
            "id": "deposit",
            "type": "action",
            "data": {
                "type": "action",
                "label": "Deposit into the vault",
                "config": {
                    "actionType": "morpho/vault-deposit",
                    "network": network,
                    "contractAddress": vault,
                    "assets": raw,
                    "receiver": wallet,
                    "_protocolMeta": json.dumps(
                        {
                            "protocolSlug": "morpho",
                            "contractKey": "vault",
                            "functionName": "deposit",
                            "actionType": "write",
                        }
                    ),
                },
            },
        },
    ]
    edges = [
        {"id": "trigger->balance", "source": "trigger", "target": "balance"},
        {"id": "balance->gate", "source": "balance", "target": "gate"},
        {"id": "gate->approve", "source": "gate", "target": "approve", "sourceHandle": "true"},
        {"id": "approve->deposit", "source": "approve", "target": "deposit"},
    ]
    return {
        "name": name or f"almanak-keeperhub compounder ({token_symbol} -> {vault[:6]}...{vault[-4:]})",
        "description": (
            f"Every tick of `{cron}`: read idle {token_symbol} of {wallet}; if between {min_amount} and "
            f"{max_amount}, approve and deposit it into vault {vault}. Larger balances are left for the "
            "Almanak strategy to size. Generated by almanak-keeperhub."
        ),
        "nodes": nodes,
        "edges": edges,
        "enabled": False,
    }


async def deploy(client: KeeperHubClient, workflow: dict[str, Any]) -> dict[str, Any]:
    """POST /api/workflows/create; the workflow is created disabled."""
    response = await client._http.post("/api/workflows/create", json=workflow)
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        raise RuntimeError(f"create failed (HTTP {response.status_code}): {json.dumps(payload)[:400]}")
    return payload


async def set_enabled(client: KeeperHubClient, workflow_id: str, enabled: bool) -> dict[str, Any]:
    response = await client._http.patch(f"/api/workflows/{workflow_id}", json={"enabled": enabled})
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        raise RuntimeError(f"update failed (HTTP {response.status_code}): {json.dumps(payload)[:400]}")
    return payload


async def validate_remote(client: KeeperHubClient, workflow_id: str) -> dict[str, Any]:
    # GET on the hosted app (app/api/workflows/[workflowId]/validate/route.ts exports GET only).
    response = await client._http.get(f"/api/workflows/{workflow_id}/validate", params={"deepCheck": "true"})
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        return {"status": response.status_code, "error": payload}
    # The hosted app wraps the verdict: {"ok": true, "result": {"valid": ..., "nodeCount": ...}}
    result = payload.get("result") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else payload
    return {
        "valid": result.get("valid"),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
        **result,
    }


async def executions(client: KeeperHubClient, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
    response = await client._http.get(f"/api/workflows/{workflow_id}/executions", params={"limit": limit})
    payload = response.json() if response.content else {}
    rows = payload.get("executions", payload) if isinstance(payload, dict) else payload
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def list_workflows(client: KeeperHubClient) -> list[dict[str, Any]]:
    response = await client._http.get("/api/workflows", params={"limit": 50})
    payload = response.json() if response.content else {}
    rows = payload.get("workflows", payload) if isinstance(payload, dict) else payload
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


KNOWN_TOKENS: dict[tuple[int, str], str] = {
    (8453, "USDC"): "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    (84532, "USDC"): "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    (8453, "WETH"): "0x4200000000000000000000000000000000000006",
    (42161, "USDC"): "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    (1, "USDC"): "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}


def keeper_params_from_config(config_path: Path) -> dict[str, Any]:
    """Vault, token and chain for the keeper, read from an Almanak strategy config.json."""
    from almanak.core.chains import ChainRegistry

    from almanak_keeperhub.testnet import register_testnets

    register_testnets()
    config = json.loads(Path(config_path).read_text())
    chain_name = str(config.get("chain") or "base")
    descriptor = ChainRegistry.try_resolve(chain_name)
    chain_id = int(descriptor.chain_id) if descriptor else 8453
    symbol = str(config.get("deposit_token") or "USDC").upper()
    token = KNOWN_TOKENS.get((chain_id, symbol))
    if token is None:
        for entry in config.get("token_funding", []):
            if str(entry.get("symbol", "")).upper() == symbol and entry.get("address"):
                token = str(entry["address"])
    if token is None:
        raise ValueError(f"no address known for {symbol} on chain {chain_id}; pass --token")
    vault = config.get("vault_address")
    if not vault:
        raise ValueError("config.json has no vault_address; pass --vault")
    return {"vault": str(vault), "token": token, "token_symbol": symbol, "chain_id": chain_id, "chain_name": chain_name}


def state_path(strategy_dir: Path | None = None) -> Path:
    configured = os.environ.get("ALMANAK_KEEPERHUB_KEEPER_STATE")
    if configured:
        return Path(configured)
    return (strategy_dir or Path.cwd()) / "keeperhub-keeper.json"
