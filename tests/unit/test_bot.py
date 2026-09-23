"""The Telegram operator bot: owner-only, read commands from the proof files, actions through the CLI."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from almanak_keeperhub.bot import OperatorBot

BASE = "https://app.keeperhub.com"
ORG = "0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9"


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
    assert "--simulate-only" in " ".join(calls[0]) and "SUCCESS" in reply and "dry run" in reply

    reply = await bot.handle(chat_id="42", text="/tick")
    assert "/confirm" in reply and len(calls) == 1  # a real tick needs confirmation
    reply = await bot.handle(chat_id="42", text="/confirm")
    assert len(calls) == 2 and "--simulate-only" not in " ".join(calls[1]) and "--fresh" in calls[1]

    reply = await bot.handle(chat_id="42", text="/exit")
    assert "/confirm" in reply and len(calls) == 2  # the guarded exit needs confirmation too
    await bot.handle(chat_id="42", text="/confirm")
    assert calls[2][:3] == ["exit", "-d", str(strategy_dir)]


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


def test_a_tapped_button_is_the_same_as_typing_the_command() -> None:
    chat, text, _ = OperatorBot.parse_update(
        {"update_id": 1, "callback_query": {"id": "cb1", "data": "/status", "message": {"chat": {"id": 42}}}}
    )
    assert (chat, text) == ("42", "/status")
    chat, text, sent = OperatorBot.parse_update(
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/help", "date": 5}}
    )
    assert (chat, text, sent) == ("42", "/help", 5.0)


async def test_confirm_prompts_carry_confirm_and_cancel_buttons_and_cancel_drops_the_action(strategy_dir: Path) -> None:
    calls: list[list[str]] = []
    bot = make_bot(strategy_dir, runner=lambda args: (calls.append(args) or "", 0))
    reply = await bot.handle(chat_id="42", text="/tick")
    assert OperatorBot.keyboard_for("/tick", reply) == [[("Confirm", "/confirm"), ("Cancel", "/cancel")]]
    assert "cancelled" in (await bot.handle(chat_id="42", text="/cancel")).lower()
    assert "nothing pending" in (await bot.handle(chat_id="42", text="/confirm")).lower()
    assert calls == []
    assert OperatorBot.keyboard_for("/status", "anything") is not None


async def test_a_repeated_tap_does_not_stack_prompts_and_a_running_action_blocks_another(strategy_dir: Path) -> None:
    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def slow_runner(args: list[str]) -> tuple[str, int]:  # runs in a worker thread
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        return ("Status: SUCCESS", 0)

    bot = make_bot(strategy_dir, runner=slow_runner)
    first = await bot.handle(chat_id="42", text="/exit")
    again = await bot.handle(chat_id="42", text="/exit")
    assert "/confirm" in first and "already armed" in again

    task = asyncio.create_task(bot.handle(chat_id="42", text="/confirm"))
    await asyncio.wait_for(started.wait(), 5)
    assert "still working" in (await bot.handle(chat_id="42", text="/confirm")).lower()
    assert "still working" in (await bot.handle(chat_id="42", text="/tick")).lower()
    release.set()
    assert "done" in await task


async def test_guard_runs_the_workflow_exit_after_confirm_and_stale_runs_at_once(strategy_dir: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> tuple[str, int]:
        calls.append(args)
        if "--stale" in args:
            return (
                "executed          : False\nguard             : vault shares held gte 5000001  (Condition node)\n"
                "observed          : 5000000\nexecution_id      : qez8b9fhipm7c4zcqa6sg\nstatus            : completed\n"
                "nodes             : trigger:completed -> shares:completed -> gate:completed  (3 of 4 steps)\n"
                "note              : stopped at the Condition; the redeem node was never reached\nseconds           : 3.7\n",
                0,
            )
        return (
            "executed          : True\nguard             : vault shares held gte 5000000  (Condition node)\n"
            "observed          : 5000000\nexecution_id      : 90eswt00kn44cw2szpl80\nstatus            : completed\n"
            "nodes             : trigger:completed -> shares:completed -> gate:completed -> redeem:completed  (4 of 4 steps)\n"
            "tx_hash           : 0x" + "c1" * 32 + "  verified=True\n"
            "explorer          : https://sepolia.basescan.org/tx/0x" + "c1" * 32 + "\nseconds           : 8.4\n",
            0,
        )

    bot = make_bot(strategy_dir, runner=runner)
    reply = await bot.handle(chat_id="42", text="/guard")
    assert "/confirm" in reply and calls == []  # the live decision redeems, so it is armed, not run
    assert OperatorBot.keyboard_for("/guard", reply) == [[("Confirm", "/confirm"), ("Cancel", "/cancel")]]
    reply = await bot.handle(chat_id="42", text="/confirm")
    assert calls[0][:5] == ["exit-guard", "run", "-d", str(strategy_dir), "--chain"] and "--stale" not in calls[0]
    assert "Redeemed" in reply and "redeem:completed" in reply and "sepolia.basescan.org" in reply
    # the CLI's "tx_hash : 0x...  verified=True" line: the link text is the hash alone, not "...d=True"
    assert ">0xc1c1c1c1c1…c1c1c1</a>" in reply and "verified=True" not in reply

    reply = await bot.handle(chat_id="42", text="/guard stale")
    assert calls[1][-1] == "--stale" and len(calls) == 2  # nothing can be broadcast, so no confirm step
    assert "Not executed" in reply and "3 of 4 steps" in reply and "Nothing was broadcast" in reply
