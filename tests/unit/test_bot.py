"""The Telegram operator bot: owner-only, read commands from the proof files, actions through the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from almanak_keeperhub.bot import OperatorBot

BASE = "https://app.keeperhub.com"
ORG = "0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9"


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


def make_bot(strategy_dir: Path, owner: str | None = "42", runner=None) -> OperatorBot:
    return OperatorBot(
        token="123:abc",
        owner_chat_id=owner,
        strategy_dir=strategy_dir,
        chain="base_sepolia",
        api_key="kh_x",
        base_url=BASE,
        runner=runner or (lambda args: ("ran " + " ".join(args), 0)),
    )


async def test_first_start_claims_ownership_when_no_owner_is_configured(strategy_dir: Path) -> None:
    bot = make_bot(strategy_dir, owner=None)
    reply = await bot.handle(chat_id="99", text=f"/start {bot.start_secret}")
    assert bot.owner_chat_id == "99"
    assert "ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID=99" in reply
    assert "not authorised" in (await bot.handle(chat_id="100", text="/status")).lower()


async def test_other_chats_are_refused(strategy_dir: Path) -> None:
    bot = make_bot(strategy_dir, owner="42")
    assert "not authorised" in (await bot.handle(chat_id="7", text="/executions")).lower()


async def test_executions_lists_newest_first_with_links(strategy_dir: Path) -> None:
    reply = await make_bot(strategy_dir).handle(chat_id="42", text="/executions")
    assert reply.index("deposit") < reply.index("approve")
    assert "verified" in reply and "sponsored" in reply
    assert "sepolia.basescan.org/tx/0x29" in reply
    assert "sepolia.basescan.org/tx/0x7070" in reply  # link built from the chain id when the file has none


async def test_keeper_reports_the_remembered_workflow(strategy_dir: Path) -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/workflows/7clo/executions").mock(
            return_value=httpx.Response(
                200, json={"executions": [{"id": "e1", "status": "success", "createdAt": "2026-09-12T12:00:00Z"}]}
            )
        )
        reply = await make_bot(strategy_dir).handle(chat_id="42", text="/keeper")
    assert "7clo" in reply and "enabled" in reply and "0 */6 * * *" in reply
    assert "e1" in reply and "success" in reply


async def test_simulate_and_tick_go_through_the_cli_runner(strategy_dir: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> tuple[str, int]:
        calls.append(args)
        return ("Status: SUCCESS | Intent: VAULT_DEPOSIT\nKeeperHub executions this run: none", 0)

    bot = make_bot(strategy_dir, runner=runner)
    reply = await bot.handle(chat_id="42", text="/simulate")
    assert "--simulate-only" in " ".join(calls[0]) and "Status: SUCCESS" in reply

    reply = await bot.handle(chat_id="42", text="/tick")
    assert "/confirm" in reply and len(calls) == 1  # a real tick needs confirmation
    reply = await bot.handle(chat_id="42", text="/confirm")
    assert len(calls) == 2 and "--simulate-only" not in " ".join(calls[1])


async def test_confirm_without_a_pending_action_is_harmless(strategy_dir: Path) -> None:
    reply = await make_bot(strategy_dir).handle(chat_id="42", text="/confirm")
    assert "nothing" in reply.lower()


async def test_help_and_unknown(strategy_dir: Path) -> None:
    bot = make_bot(strategy_dir)
    assert "/executions" in await bot.handle(chat_id="42", text="/help")
    assert "/help" in await bot.handle(chat_id="42", text="/nope")


async def test_ownership_needs_the_startup_secret(strategy_dir: Path) -> None:
    bot = make_bot(strategy_dir, owner=None)
    assert "secret" in (await bot.handle(chat_id="99", text="/start")).lower()
    assert bot.owner_chat_id is None
    assert "secret" in (await bot.handle(chat_id="99", text="/start wrong")).lower()
    reply = await bot.handle(chat_id="99", text=f"/start {bot.start_secret}")
    assert bot.owner_chat_id == "99" and "ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID=99" in reply


async def test_stale_queued_messages_are_ignored(strategy_dir: Path) -> None:
    import time

    bot = make_bot(strategy_dir)
    assert await bot.handle(chat_id="42", text="/tick", sent_at=time.time() - 3600) == ""
    assert "/confirm" in await bot.handle(chat_id="42", text="/tick", sent_at=time.time())


async def test_verify_rejects_bad_references_before_any_request(strategy_dir: Path) -> None:
    reply = await make_bot(strategy_dir).handle(chat_id="42", text="/verify ../../user/wallet?x=")
    assert "not a transaction hash or execution id" in reply
