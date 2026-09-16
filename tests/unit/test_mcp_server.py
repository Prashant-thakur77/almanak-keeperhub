"""The MCP server: the bot's proof and controls for any agent, with broadcast off by default."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from almanak_keeperhub.mcp_server import StrategyTools, build_server

BASE = "https://app.keeperhub.com"

pytest.importorskip("mcp")


def make_tools(strategy_dir: Path, runner=None, allow_broadcast: bool = False) -> StrategyTools:
    return StrategyTools(
        strategy_dir=strategy_dir,
        chain="base_sepolia",
        base_url=BASE,
        runner=runner or (lambda args: ("ran " + " ".join(args), 0)),
        allow_broadcast=allow_broadcast,
    )


async def test_status_reads_the_same_files_the_bot_reads(strategy_dir: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/api/workflows/7clo/executions").mock(return_value=httpx.Response(200, json={"executions": []}))
        status = await make_tools(strategy_dir).status()
    assert status["chain"] == "base_sepolia"
    assert status["summary"]["executions"] == 2
    assert status["summary"]["verified"] == 2
    assert status["keeper"]["workflow_id"] == "7clo"
    assert status["broadcast_enabled"] is False


def test_executions_are_newest_first_and_structured(strategy_dir: Path) -> None:
    rows = make_tools(strategy_dir).list_executions(limit=1)
    assert rows == [
        {
            "function": "deposit",
            "to": "0xVault",
            "status": "completed",
            "verified": True,
            "sponsored": True,
            "idempotent_replay": None,
            "execution_id": "au5z",
            "tx_hash": "0x" + "70" * 32,
            "explorer": "https://sepolia.basescan.org/tx/0x" + "70" * 32,
            "recorded_at": "2026-09-12T06:19:28+00:00",
        }
    ]


def test_dry_runs_are_listed_from_the_same_log(strategy_dir: Path) -> None:
    """A simulate-only tick leaves a row the agent can see, not an empty list."""
    rows = make_tools(strategy_dir).list_dry_runs(limit=5)

    assert len(rows) == 1
    assert rows[0]["function"] == "approve"
    assert rows[0]["success"] is True


async def test_verify_rejects_a_bad_reference_before_any_request(strategy_dir: Path) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.get(path__regex=r"/api/execute/.*")
        # a space is outside the URL-safe charset the guard allows, so this never becomes a path
        result = await make_tools(strategy_dir).verify("not a hash")
    assert "error" in result
    assert not route.called


async def test_simulate_tick_runs_the_dry_run_through_the_cli(strategy_dir: Path) -> None:
    seen: list[list[str]] = []

    def runner(args: list[str]) -> tuple[str, int]:
        seen.append(args)
        return ("KeeperHub simulate: ok\nStatus: dry run", 0)

    result = await make_tools(strategy_dir, runner=runner).simulate_tick()
    assert result["ok"] is True
    assert seen == [["run", "-d", str(strategy_dir), "--once", "--fresh", "--simulate-only"]]
    assert "KeeperHub simulate" in result["summary"]


async def test_run_tick_is_refused_unless_the_server_allows_broadcast(strategy_dir: Path) -> None:
    seen: list[list[str]] = []
    runner = lambda args: (seen.append(args) or "", 0)  # noqa: E731

    off = await make_tools(strategy_dir, runner=runner).run_tick(confirm=True)
    assert off["ok"] is False and "--write" in off["error"]
    assert seen == []

    on = make_tools(strategy_dir, runner=runner, allow_broadcast=True)
    unconfirmed = await on.run_tick(confirm=False)
    assert unconfirmed["ok"] is False and "confirm" in unconfirmed["error"]
    assert seen == []

    confirmed = await on.run_tick(confirm=True)
    assert confirmed["ok"] is True
    assert seen == [["run", "-d", str(strategy_dir), "--once", "--fresh"]]  # fresh by default

    await on.run_tick(confirm=True, fresh=False)
    assert seen[-1] == ["run", "-d", str(strategy_dir), "--once"]

    off_exit = await make_tools(strategy_dir, runner=runner).exit_position(confirm=True)
    assert off_exit["ok"] is False and "--write" in off_exit["error"]
    assert (await on.exit_position(confirm=False))["ok"] is False
    await on.exit_position(confirm=True)
    assert seen[-1] == ["exit", "-d", str(strategy_dir), "--chain", "base_sepolia"]


async def test_run_tick_reports_the_executions_it_produced(strategy_dir: Path) -> None:
    receipts = strategy_dir / "keeperhub-receipts.json"

    def runner(args: list[str]) -> tuple[str, int]:
        rows = json.loads(receipts.read_text())
        rows.append(
            {
                "execution_id": "new1",
                "recorded_at": "2026-09-12T07:00:00+00:00",
                "chain_id": 84532,
                "to": "0xVault",
                "function": "deposit",
                "tx_hash": "0x" + "ab" * 32,
                "status": "completed",
                "verified": True,
            }
        )
        receipts.write_text(json.dumps(rows))
        return ("broadcast tx 0xab", 0)

    result = await make_tools(strategy_dir, runner=runner, allow_broadcast=True).run_tick(confirm=True)
    assert [e["execution_id"] for e in result["new_executions"]] == ["new1"]


async def test_unknown_demo_is_refused(strategy_dir: Path) -> None:
    result = await make_tools(strategy_dir).run_failure_demo("nope")
    assert result["ok"] is False
    assert "duplicate" in result["error"]


async def test_server_registers_run_tick_only_with_write(strategy_dir: Path) -> None:
    read_only = build_server(make_tools(strategy_dir))
    names = {t.name for t in await read_only.list_tools()}
    assert "simulate_tick" in names
    assert "run_tick" not in names

    writable = build_server(make_tools(strategy_dir, allow_broadcast=True))
    assert "run_tick" in {t.name for t in await writable.list_tools()}
