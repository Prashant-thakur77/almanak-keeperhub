"""The Telegram operator bot: the strategy's proof and controls from a phone.

Read commands answer from the same files the console reads. Action commands run
the CLI (``run --simulate-only``, ``run``, the failure demos) in a subprocess
and post the summary. Only the owner chat may talk to it; the first ``/start``
claims ownership when no chat id is configured. Long polling, standard
library plus httpx, no webhook and no public endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from almanak_keeperhub.console.server import build_state, keeper_state, verify_reference
from almanak_keeperhub.notify import CHAT_ENV, TOKEN_ENV

logger = logging.getLogger(__name__)

Runner = Callable[[list[str]], tuple[str, int]]
HELP = """almanak-keeperhub operator bot

/status     org wallet, chain, counts, keeper
/executions [n]   latest executions with links (default 5)
/dryruns    latest KeeperHub dry runs
/keeper     the scheduled compounder and its executions
/verify <tx hash or execution id>   KeeperHub verdict and who acted on chain
/simulate   dry-run one strategy tick through KeeperHub (nothing broadcast)
/tick       run one real strategy tick (asks for /confirm)
/demo <revert|cap|duplicate|crash|selector|rpc>   run a failure-mode demo
/help       this list"""
DEMOS = {
    "revert": "revert_caught_by_dry_run",
    "cap": "cap_refused",
    "duplicate": "duplicate_blocked_by_idempotency",
    "crash": "crash_and_resume",
    "selector": "unknown_selector_refused",
    "rpc": "rpc_outage",
}
SUMMARY_MARKERS = (
    "Status:",
    "KeeperHub simulate",
    "broadcast tx",
    "executions this run",
    "dry runs this run",
    "  approve",
    "  deposit",
    "  redeem",
    "    tx ",
    "refused",
    "replay=",
    "resumed",
    "no second broadcast",
    "settled from",
    "Error",
    "!!!",
)


def _default_runner(args: list[str]) -> tuple[str, int]:
    proc = subprocess.run(  # noqa: S603 - our own CLI, fixed argv
        [sys.executable, "-m", "almanak_keeperhub.cli", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
        timeout=900,
    )
    return (proc.stdout + proc.stderr, proc.returncode)


_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def _short_addresses(text: str) -> str:
    """Phone-sized: 0xe7dbacbd…36ac9 instead of the full 40 hex characters."""
    return _ADDRESS.sub(lambda m: m.group(0)[:10] + "…" + m.group(0)[-5:], text)


def _summarise(output: str, limit: int = 30) -> str:
    lines = [ln.rstrip() for ln in output.splitlines() if any(m in ln for m in SUMMARY_MARKERS) and "Symbol" not in ln]
    if not lines:
        lines = [ln for ln in output.splitlines() if ln.strip()][-limit:]
    text = "\n".join(lines[-limit:])
    return text[-3500:] if len(text) > 3500 else text


class OperatorBot:
    def __init__(
        self,
        *,
        token: str,
        owner_chat_id: str | None,
        strategy_dir: Path,
        chain: str,
        api_key: str,
        base_url: str,
        runner: Runner | None = None,
    ) -> None:
        self._token = token
        self.owner_chat_id = owner_chat_id or None
        self._strategy_dir = Path(strategy_dir)
        self._chain = chain
        self._api_key = api_key
        self._base_url = base_url
        self._runner = runner or _default_runner
        self._pending: tuple[str, float] | None = None
        self._offset = 0

    # -- command handling -------------------------------------------------------

    async def handle(self, *, chat_id: str, text: str) -> str:
        parts = (text or "").strip().split()
        if not parts:
            return HELP
        command, args = parts[0].lower().split("@")[0], parts[1:]
        if self.owner_chat_id is None and command == "/start":
            self.owner_chat_id = str(chat_id)
            return f"This chat now owns the bot. Save it so restarts keep it:\n{CHAT_ENV}={chat_id}\n\n" + HELP
        if str(chat_id) != str(self.owner_chat_id):
            return "Not authorised: this bot answers only its owner's chat."
        handler = {
            "/start": self._help,
            "/help": self._help,
            "/status": self._status,
            "/executions": self._executions,
            "/dryruns": self._dryruns,
            "/keeper": self._keeper,
            "/verify": self._verify,
            "/simulate": self._simulate,
            "/tick": self._tick,
            "/confirm": self._confirm,
            "/demo": self._demo,
        }.get(command)
        if handler is None:
            return f"Unknown command {command}. Send /help for the list."
        try:
            return await handler(args)
        except Exception as exc:  # noqa: BLE001 - the chat gets the error text
            logger.exception("bot command failed")
            return f"{command} failed: {type(exc).__name__}: {exc}"

    async def _help(self, _args: list[str]) -> str:
        return HELP

    def _state(self) -> dict[str, Any]:
        from almanak_keeperhub.keeper import state_path
        from almanak_keeperhub.receipts import receipts_path

        docs = (
            self._strategy_dir.parents[1] / "docs"
            if len(self._strategy_dir.parents) > 1
            else self._strategy_dir / "docs"
        )
        return build_state(
            receipts_path()
            if os.environ.get("ALMANAK_KEEPERHUB_RECEIPTS")
            else self._strategy_dir / "keeperhub-receipts.json",
            docs / "receipts.json",
            docs / "benchmark.json",
            org_wallet=os.environ.get("KEEPERHUB_WALLET_ADDRESS", ""),
            base_url=self._base_url,
            chain=self._chain,
        ) | {"keeper_path": str(state_path(self._strategy_dir))}

    async def _status(self, _args: list[str]) -> str:
        state = self._state()
        s = state["summary"]
        keeper = await keeper_state(Path(state["sources"]["receipts"]))
        keeper_line = (
            f"keeper {keeper.get('workflow_id')} {'enabled' if keeper.get('enabled') else 'disabled'}, cron {keeper.get('cron')}"
            if keeper.get("deployed")
            else "keeper: not deployed"
        )
        return (
            f"chain {self._chain}\norg wallet {state['org_wallet'] or 'unknown'}\nKeeperHub {self._base_url}\n"
            f"executions {s['executions']} (verified {s['verified']}, replays {s['replays']}, sponsored {s['sponsored']}, in flight {s['in_flight']}, failed {s['failed']})\n"
            f"dry runs {s['dry_runs']} ({s['dry_run_refusals']} would revert)\n{keeper_line}"
        )

    async def _executions(self, args: list[str]) -> str:
        n = int(args[0]) if args and args[0].isdigit() else 5
        rows = self._state()["executions"][:n]
        if not rows:
            return "No executions recorded yet."
        lines = []
        for e in rows:
            flags = " ".join(
                f
                for f, on in (
                    ("verified", e.get("verified") is True),
                    ("sponsored", e.get("sponsored")),
                    ("replay", e.get("idempotent_replay")),
                )
                if on
            )
            lines.append(
                f"{e.get('function')} -> {str(e.get('to'))[:10]}… {e.get('status')} {flags}\n  exec {e.get('execution_id')}\n  {e.get('explorer') or e.get('tx_hash')}"
            )
        return "\n".join(lines)

    async def _dryruns(self, _args: list[str]) -> str:
        rows = self._state()["simulations"][:8]
        if not rows:
            return "No dry runs recorded yet."
        return "\n".join(
            f"{x.get('function')} -> {str(x.get('to'))[:10]}…: {'would succeed, gas ' + str(x.get('gas_estimate')) if x.get('success') else 'would revert: ' + str(x.get('error'))[:120]}"
            for x in rows
        )

    async def _keeper(self, _args: list[str]) -> str:
        state = self._state()
        k = await keeper_state(Path(state["sources"]["receipts"]))
        if not k.get("deployed"):
            return "No keeper deployed. Run: almanak-keeperhub keeper deploy"
        head = f"keeper workflow {k.get('workflow_id')} {'enabled' if k.get('enabled') else 'disabled'}\ncron {k.get('cron')} UTC, window {k.get('min')}..{k.get('max')}\nvalidation valid={(k.get('validation') or {}).get('valid')}"
        if k.get("error"):
            return head + f"\nexecutions: {k['error']}"
        runs = k.get("executions") or []
        if not runs:
            return head + "\nexecutions: none yet (schedule not fired, or disabled)"
        return (
            head
            + "\n"
            + "\n".join(
                f"{r.get('createdAt') or r.get('startedAt') or ''} {r.get('id')} {r.get('status')}" for r in runs[:10]
            )
        )

    async def _verify(self, args: list[str]) -> str:
        if not args:
            return "Usage: /verify <tx hash or execution id>"
        state = self._state()
        result = await verify_reference(args[0], Path(state["sources"]["receipts"]), self._chain)
        if result.get("error"):
            return f"verify: {result['error']}"
        lines = [
            f"execution {result['execution_id']}: {result['status']}"
            + (", sponsored gas" if result.get("sponsored") else "")
        ]
        for r in result.get("receipts", []):
            lines.append(f"receipt {str(r['hash'])[:12]}… verified={r['verified']} {r['receipt_status']}")
        on = result.get("onchain")
        if on:
            who = "the org wallet" if on["sender_is_org_wallet"] else "KeeperHub's relayer (sponsored gas)"
            lines.append(f"on chain: {on['status']} in block {on['block']}, sender is {who}")
        lines += [_short_addresses(line) for line in result.get("events", [])]
        if result.get("transaction_link"):
            lines.append(result["transaction_link"])
        return "\n".join(lines)

    async def _simulate(self, _args: list[str]) -> str:
        return await self._run_cli(
            ["run", "-d", str(self._strategy_dir), "--once", "--fresh", "--simulate-only"], "dry run"
        )

    async def _tick(self, _args: list[str]) -> str:
        self._pending = ("tick", time.time())
        return "This runs one REAL strategy tick through KeeperHub (value may move). Send /confirm within 60 seconds."

    async def _confirm(self, _args: list[str]) -> str:
        if not self._pending or time.time() - self._pending[1] > 60:
            self._pending = None
            return "Nothing pending (or it expired). Send /tick first."
        self._pending = None
        return await self._run_cli(["run", "-d", str(self._strategy_dir), "--once"], "real tick")

    async def _demo(self, args: list[str]) -> str:
        name = DEMOS.get((args[0] if args else "").lower())
        if not name:
            return "Usage: /demo <" + "|".join(DEMOS) + ">"
        script = self._strategy_dir.parent / "failure_modes" / f"{name}.py"
        cwd = script.parent
        env = os.environ | {"ALMANAK_KEEPERHUB_CHAIN": self._chain}

        def run_demo(_args: list[str]) -> tuple[str, int]:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                check=False,
                timeout=900,
            )  # noqa: S603
            return (proc.stdout + proc.stderr, proc.returncode)

        output, code = await asyncio.to_thread(run_demo, [])
        return f"{name}: {'ok' if code == 0 else 'exit ' + str(code)}\n{_summarise(output)}"

    async def _run_cli(self, args: list[str], label: str) -> str:
        output, code = await asyncio.to_thread(self._runner, args)
        return f"{label}: {'done' if code == 0 else 'exit ' + str(code)}\n{_summarise(output)}"

    # -- Telegram transport ----------------------------------------------------------

    async def send(self, chat_id: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=20.0) as http:
            for chunk in [text[i : i + 3800] for i in range(0, max(len(text), 1), 3800)]:
                await http.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                )

    async def run_forever(self) -> None:
        logger.info(
            "operator bot polling (owner=%s, strategy=%s)",
            self.owner_chat_id or "first /start claims",
            self._strategy_dir,
        )
        async with httpx.AsyncClient(timeout=60.0) as http:
            while True:
                try:
                    r = await http.get(
                        f"https://api.telegram.org/bot{self._token}/getUpdates",
                        params={"timeout": 50, "offset": self._offset, "allowed_updates": '["message"]'},
                    )
                    for update in r.json().get("result", []):
                        self._offset = int(update["update_id"]) + 1
                        message = update.get("message") or {}
                        chat_id = str((message.get("chat") or {}).get("id", ""))
                        text = message.get("text") or ""
                        if not chat_id or not text:
                            continue
                        asyncio.create_task(self._answer(chat_id, text))
                except Exception as exc:  # noqa: BLE001 - keep polling
                    logger.warning("telegram polling error: %s", exc)
                    await asyncio.sleep(5)

    async def _answer(self, chat_id: str, text: str) -> None:
        if text.split()[0].lower() in ("/simulate", "/confirm", "/demo"):
            await self.send(chat_id, "working…")
        reply = await self.handle(chat_id=chat_id, text=text)
        await self.send(chat_id, reply)


def bot_from_env(strategy_dir: Path, chain: str, runner: Runner | None = None) -> OperatorBot:
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    return OperatorBot(
        token=token,
        owner_chat_id=os.environ.get(CHAT_ENV) or None,
        strategy_dir=strategy_dir,
        chain=chain,
        api_key=os.environ.get("KEEPERHUB_API_KEY", ""),
        base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com"),
        runner=runner,
    )


def command_line(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)
