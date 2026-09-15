import json
from pathlib import Path

import pytest

BASE = "https://app.keeperhub.com"


@pytest.fixture(autouse=True)
def _receipts_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the receipts log out of the working directory during tests."""
    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(tmp_path / "receipts.json"))


# The recorded run the bot, the console and the MCP server all read: two executions,
# one dry run, one deployed keeper.
@pytest.fixture
def strategy_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "strategy"
    d.mkdir()
    (d / "keeperhub-receipts.json").write_text(
        json.dumps(
            [
                {
                    "type": "simulation",
                    "recorded_at": "2026-09-12T06:18:00+00:00",
                    "chain_id": 84532,
                    "to": "0xUSDC",
                    "function": "approve",
                    "success": True,
                    "gas_estimate": 56240,
                },
                {
                    "execution_id": "az13",
                    "recorded_at": "2026-09-12T06:19:20+00:00",
                    "chain_id": 84532,
                    "to": "0xUSDC",
                    "function": "approve",
                    "tx_hash": "0x" + "29" * 32,
                    "status": "completed",
                    "verified": True,
                    "sponsored": True,
                    "transaction_link": "https://sepolia.basescan.org/tx/0x29",
                },
                {
                    "execution_id": "au5z",
                    "recorded_at": "2026-09-12T06:19:28+00:00",
                    "chain_id": 84532,
                    "to": "0xVault",
                    "function": "deposit",
                    "tx_hash": "0x" + "70" * 32,
                    "status": "completed",
                    "verified": True,
                    "sponsored": True,
                },
            ]
        )
    )
    (d / "keeperhub-keeper.json").write_text(
        json.dumps(
            {
                "workflow_id": "7clo",
                "enabled": True,
                "cron": "0 */6 * * *",
                "min": "1",
                "max": "90",
                "validation": {"valid": True},
            }
        )
    )
    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(d / "keeperhub-receipts.json"))
    monkeypatch.setenv("ALMANAK_KEEPERHUB_KEEPER_STATE", str(d / "keeperhub-keeper.json"))
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_x")
    monkeypatch.setenv("KEEPERHUB_BASE_URL", BASE)
    return d
