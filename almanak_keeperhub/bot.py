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
import secrets
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
STALE_AFTER_SECONDS = 180
HELP = """<b>almanak-keeperhub operator bot</b>
Almanak decides. KeeperHub lands it. This chat is the operator's view.

<b>Read</b>
/status  wallet, chain, counts, keeper
/executions [n]  latest executions with links
/dryruns  latest KeeperHub dry runs
/keeper  the scheduled compounder KeeperHub runs
/verify &lt;tx hash or execution id&gt;  KeeperHub's verdict and who acted on chain

<b>Act</b> (each asks for /confirm)
/simulate  dry-run one strategy tick through KeeperHub, nothing broadcast
/tick  one real strategy tick: Almanak plans, KeeperHub dry-runs, signs and broadcasts
/exit  redeem the vault position; KeeperHub re-reads the balance before it acts
/guard  the same exit as a KeeperHub workflow: a Condition node re-reads the shares, then Morpho redeem

<b>Break it on purpose</b>
/demo &lt;revert|cap|duplicate|crash|selector|rpc|stale&gt;
/guard stale  a decision the position no longer covers, sent to the workflow: the Condition stops it

/cancel drops a pending tick, exit or guard. Buttons under each reply do the same as typing."""
COMMAND_MENU = [
    ("status", "Wallet, chain, counts, keeper"),
    ("executions", "Latest executions with links"),
    ("dryruns", "Latest KeeperHub dry runs"),
    ("keeper", "The scheduled compounder KeeperHub runs"),
    ("verify", "KeeperHub's verdict on a hash or execution id"),
    ("simulate", "Dry-run one strategy tick, nothing broadcast"),
    ("tick", "One real strategy tick through KeeperHub"),
    ("exit", "Guarded exit: KeeperHub re-checks, then redeems"),
    ("guard", "The exit as a KeeperHub workflow: Condition, then redeem"),
    ("demo", "Run a failure-mode demo"),
    ("cancel", "Drop a pending tick, exit or guard"),
    ("help", "The command list"),
]
MAIN_KEYBOARD = [
    [("Status", "/status"), ("Executions", "/executions 3"), ("Dry runs", "/dryruns")],
    [("Simulate a tick", "/simulate"), ("Real tick", "/tick"), ("Guarded exit", "/exit")],
    [("Workflow guard", "/guard"), ("Stale decision", "/guard stale"), ("Keeper", "/keeper")],
]
CONFIRM_KEYBOARD = [[("Confirm", "/confirm"), ("Cancel", "/cancel")]]
DEMOS = {
    "revert": "revert_caught_by_dry_run",
    "cap": "cap_refused",
    "duplicate": "duplicate_blocked_by_idempotency",
    "crash": "crash_and_resume",
    "selector": "unknown_selector_refused",
    "rpc": "rpc_outage",
    "stale": "stale_exit_not_executed",
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


def _h(text: object) -> str:
    """Escape for Telegram HTML parse mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TX_LINE = re.compile(r"^\s+tx (0x[0-9a-fA-F]{64})\s+(https?://\S+)")
_EXEC_LINE = re.compile(r"^\s+(\w+) -> (0x[0-9a-fA-F]{40})\s+execution=(\S+)\s+status=(\S+)\s+verified=(\w+)(.*)$")
_SIM_OK = re.compile(r"KeeperHub simulate ok: (0x[0-9a-fA-F]{40})\.(\w+) gas=(\S+)")
_STATUS = re.compile(r"Status: (\w+) \| Intent: (\w+)")
_KV = re.compile(
    r"^(executed|guard|observed|execution_id|tx_hash|status|idempotent_replay|seconds|explorer|note|nodes|error)\s*:\s*(.*)$"
)


def _pretty(label: str, output: str, code: int) -> str:
    """Turn the CLI's summary lines into a phone-sized, formatted report."""
    lines: list[str] = []
    executions: list[str] = []
    pending_exec: str | None = None
    kv: dict[str, str] = {}
    for raw in output.splitlines():
        ln = raw.rstrip()
        if m := _SIM_OK.search(ln):
            lines.append(
                f"Dry run: <b>{_h(m.group(2))}</b> on <code>{_h(_short_addresses(m.group(1)))}</code> would succeed, gas {_h(m.group(3))}"
            )
        elif m := _STATUS.search(ln):
            lines.append(f"Almanak: <b>{_h(m.group(1))}</b>, intent {_h(m.group(2))}")
        elif "dry runs this run" in ln:
            lines.append(_h(ln.strip()))
        elif "executions this run" in ln:
            lines.append(
                _h(ln.split("(")[0].strip() + ("(" + ln.split("(", 1)[1].split(")")[0] + ")" if "(" in ln else ""))
            )
        elif m := _EXEC_LINE.match(ln):
            fn, to, exec_id, status, verified, rest = m.groups()
            flags = ("verified" if verified == "True" else "not verified") + (
                ", sponsored gas" if "sponsored" in rest else ""
            )
            pending_exec = f"<b>{_h(fn)}</b> -> <code>{_h(_short_addresses(to))}</code>: {_h(status)}, {flags}\n  exec <code>{_h(exec_id)}</code>"
        elif (m := _TX_LINE.match(ln)) and pending_exec:
            executions.append(
                pending_exec + f'\n  tx <a href="{_h(m.group(2))}">{_h(m.group(1)[:12])}…{_h(m.group(1)[-6:])}</a>'
            )
            pending_exec = None
        elif m := _KV.match(ln.strip()):
            kv[m.group(1)] = m.group(2).strip()
        elif "iteration_summary" in ln or "[info" in ln:
            continue  # Almanak's structured log line, not a reply
        elif re.search(r"refused|!!!|error(?!=None)", ln, re.IGNORECASE):
            lines.append(_h(ln.strip()[:300]))
    if pending_exec:
        executions.append(pending_exec)
    if kv:
        if kv.get("executed") == "True":
            lines.append(
                f"KeeperHub read the vault balance first: <b>{_h(kv.get('observed'))}</b> shares held, guard <code>{_h(kv.get('guard'))}</code> holds"
            )
            lines.append(f"Redeemed: <b>{_h(kv.get('status'))}</b>, exec <code>{_h(kv.get('execution_id'))}</code>")
            if kv.get("explorer"):
                # the CLI prints "tx_hash : 0x...  verified=True"; the link text wants the hash alone
                tx_hash = (kv.get("tx_hash", "").split() or [""])[0]
                lines.append(f'tx <a href="{_h(kv["explorer"])}">{_h(tx_hash[:12])}…{_h(tx_hash[-6:])}</a>')
            if kv.get("note"):
                lines.append(_h(kv["note"]))
        elif "executed" in kv:
            lines.append(
                f"Not executed. KeeperHub observed <b>{_h(kv.get('observed'))}</b> shares; guard <code>{_h(kv.get('guard'))}</code> does not hold. Nothing was broadcast."
            )
        if kv.get("nodes"):
            lines.append(f"Workflow nodes: <code>{_h(kv['nodes'])}</code>")
        if kv.get("error"):
            lines.append(_h(kv["error"][:300]))
        if kv.get("seconds"):
            lines.append(f"{_h(kv['seconds'])}s end to end")
    head = f"<b>{_h(label)}</b>: {'done' if code == 0 else 'exit ' + str(code)}"
    if "nothing to redeem" in output:
        return head + "\nThe vault holds no shares for this wallet, so there is nothing to redeem. Run a tick first."
    clean = [ln for ln in output.splitlines() if ln.strip() and "collision" not in ln and "[info" not in ln]
    body = "\n".join(lines + executions) or _h("\n".join(clean[-12:]))
    return head + "\n" + body


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
        self._base_url = base_url
        self._runner = runner or _default_runner
        self._pending: tuple[str, float] | None = None
        self._busy: str | None = None  # the action running right now; a second one must wait
        self._offset = 0
        # With no configured owner, only someone who can read this process's output may claim the bot.
        self.start_secret = secrets.token_urlsafe(8)

    # -- command handling -------------------------------------------------------

    async def handle(self, *, chat_id: str, text: str, sent_at: float | None = None) -> str:
        if sent_at is not None and time.time() - sent_at > STALE_AFTER_SECONDS:
            return ""  # queued while the bot was offline: never act on an old /tick or /confirm
        parts = (text or "").strip().split()
        if not parts:
            return HELP
        command, args = parts[0].lower().split("@")[0], parts[1:]
        if self.owner_chat_id is None:
            if command == "/start" and args and secrets.compare_digest(args[0], self.start_secret):
                self.owner_chat_id = str(chat_id)
                return f"This chat now owns the bot. Save it so restarts keep it:\n{CHAT_ENV}={chat_id}\n\n" + HELP
            return "This bot has no owner yet. Send /start <secret>; the secret is printed where the bot was started."
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
            "/exit": self._exit,
            "/guard": self._guard,
            "/confirm": self._confirm,
            "/cancel": self._cancel,
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
            f"<b>Strategy status</b>\n"
            f"chain <code>{_h(self._chain)}</code>\n"
            f"org wallet <code>{_h(_short_addresses(state['org_wallet'] or 'unknown'))}</code> (key in KeeperHub's enclave, none here)\n"
            f"KeeperHub {_h(self._base_url)}\n\n"
            f"executions <b>{s['executions']}</b>: {s['verified']} verified, {s['sponsored']} gas sponsored, "
            f"{s['replays']} replays, {s['in_flight']} in flight, {s['failed']} failed\n"
            f"dry runs <b>{s['dry_runs']}</b>: {s['dry_run_refusals']} refused before broadcast\n"
            f"{_h(keeper_line)}"
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
            link = e.get("explorer")
            tx = str(e.get("tx_hash") or "")
            tx_text = f"{tx[:12]}…{tx[-6:]}" if len(tx) > 20 else tx
            tx_html = f'<a href="{_h(link)}">{_h(tx_text)}</a>' if link else f"<code>{_h(tx_text)}</code>"
            when = str(e.get("recorded_at") or "")[:16].replace("T", " ")
            guarded = " guarded" if e.get("guarded") else ""
            lines.append(
                f"<b>{_h(e.get('function'))}</b> -> <code>{_h(_short_addresses(str(e.get('to'))))}</code>  {_h(e.get('status'))}{guarded}\n"
                f"  {_h(flags)}  {_h(when)} UTC\n  exec <code>{_h(e.get('execution_id'))}</code>  tx {tx_html}"
            )
        return f"<b>Latest {len(rows)} executions</b>\n" + "\n\n".join(lines)

    async def _dryruns(self, _args: list[str]) -> str:
        rows = self._state()["simulations"][:8]
        if not rows:
            return "No dry runs recorded yet."
        return "<b>Latest dry runs</b> (KeeperHub simulate, nothing broadcast)\n" + "\n".join(
            f"<b>{_h(x.get('function'))}</b> -> <code>{_h(_short_addresses(str(x.get('to'))))}</code>: "
            + (
                f"would succeed, gas {_h(x.get('gas_estimate'))}"
                if x.get("success")
                else f"refused: {_h(str(x.get('error'))[:140])}"
            )
            for x in rows
        )

    async def _keeper(self, _args: list[str]) -> str:
        state = self._state()
        k = await keeper_state(Path(state["sources"]["receipts"]))
        if not k.get("deployed"):
            return "No keeper deployed. Run: almanak-keeperhub keeper deploy"
        head = (
            f"<b>Keeper</b>: a KeeperHub workflow generated from the strategy config, run by KeeperHub's scheduler with no Almanak process\n"
            f"workflow <code>{_h(k.get('workflow_id'))}</code>, {'enabled' if k.get('enabled') else 'disabled'}\n"
            f"cron <code>{_h(k.get('cron'))}</code> UTC, deposits idle USDC between {_h(k.get('min'))} and {_h(k.get('max'))}\n"
            f"validated by KeeperHub: {(k.get('validation') or {}).get('valid')}"
        )
        if k.get("error"):
            return head + f"\nexecutions: {k['error']}"
        runs = k.get("executions") or []
        if not runs:
            return head + "\nexecutions: none yet (schedule not fired, or disabled)"
        return (
            head
            + "\n"
            + "\n".join(
                f"{_h(str(r.get('createdAt') or r.get('startedAt') or '')[:16])} <code>{_h(r.get('id'))}</code> {_h(r.get('status'))}"
                for r in runs[:10]
            )
        )

    async def _verify(self, args: list[str]) -> str:
        if not args:
            return "Usage: /verify <tx hash or execution id>"
        from almanak_keeperhub.verify import valid_reference

        if not valid_reference(args[0]):
            return "That is not a transaction hash or execution id."
        state = self._state()
        result = await verify_reference(args[0], Path(state["sources"]["receipts"]), self._chain)
        if result.get("error"):
            return f"verify: {result['error']}"
        lines = [
            "<b>KeeperHub's verdict</b>",
            f"execution <code>{_h(result['execution_id'])}</code>: <b>{_h(result['status'])}</b>"
            + (", gas sponsored" if result.get("sponsored") else ""),
        ]
        for r in result.get("receipts", []):
            lines.append(
                f"receipt <code>{_h(str(r['hash'])[:12])}…</code> {'verified by KeeperHub' if r['verified'] else 'not verified'}, {_h(r['receipt_status'])}"
            )
        if result.get("onchain_error"):
            lines.append(f"on chain: {_h(result['onchain_error'])}")
        on = result.get("onchain")
        if on:
            who = "the org wallet" if on["sender_is_org_wallet"] else "the sponsor's paymaster, not the org wallet"
            lines.append(f"\n<b>On chain</b>: {_h(on['status'])} in block {on['block']}; transaction sender is {who}")
        events = result.get("events", [])
        if events:
            lines.append("<b>Who acted</b>, from the receipt's events:")
            lines += ["  " + _h(_short_addresses(line)) for line in events]
        if result.get("transaction_link"):
            lines.append(f'<a href="{_h(result["transaction_link"])}">open on the explorer</a>')
        return "\n".join(lines)

    async def _simulate(self, _args: list[str]) -> str:
        if self._busy:
            return f"Still working on the {self._busy}. Wait for its reply."
        return await self._run_cli(
            ["run", "-d", str(self._strategy_dir), "--once", "--fresh", "--simulate-only"], "dry run"
        )

    def _arm(self, action: str) -> str | None:
        """Arm an action for /confirm; a repeat tap while it is armed or running is answered, not stacked."""
        if self._busy:
            return f"Still working on the {self._busy}. Wait for its reply before starting another action."
        if self._pending and self._pending[0] == action and time.time() - self._pending[1] < 60:
            return f"The {action} is already armed. Tap Confirm to run it, or Cancel."
        self._pending = (action, time.time())
        return None

    async def _tick(self, _args: list[str]) -> str:
        if already := self._arm("tick"):
            return already
        return (
            "<b>Real tick</b>: Almanak plans the intent, KeeperHub dry-runs it, then signs in its enclave and broadcasts. "
            "Test USDC moves. Send /confirm within 60 seconds."
        )

    async def _exit(self, _args: list[str]) -> str:
        if already := self._arm("exit"):
            return already
        return (
            "<b>Guarded exit</b>: the redeem goes out as KeeperHub check-and-execute. KeeperHub reads the vault balance "
            "itself right before the write and refuses if the position is gone. Send /confirm within 60 seconds."
        )

    async def _guard(self, args: list[str]) -> str:
        """The guarded exit as a KeeperHub workflow (exit-guard run). `stale` asks for one share more
        than the position holds, so the Condition node stops it and nothing is broadcast: no confirm."""
        if args and args[0].lower() == "stale":
            if self._busy:
                return f"Still working on the {self._busy}. Wait for its reply."
            self._busy = "stale decision"
            try:
                return await self._run_cli(
                    ["exit-guard", "run", "-d", str(self._strategy_dir), "--chain", self._chain, "--stale"],
                    "workflow guard, stale decision",
                )
            finally:
                self._busy = None
        if already := self._arm("guard"):
            return already
        return (
            "<b>Workflow guard</b>: the exit decision triggers the KeeperHub workflow. Its nodes read the vault shares, "
            "a Condition compares them with the decision, and the Morpho redeem runs only on the true branch. "
            "Send /confirm within 60 seconds."
        )

    async def _cancel(self, _args: list[str]) -> str:
        if not self._pending:
            return "Nothing pending."
        action, self._pending = self._pending[0], None
        return f"Cancelled the pending {action}. Nothing was sent."

    async def _confirm(self, _args: list[str]) -> str:
        if self._busy:
            return f"Still working on the {self._busy}. Wait for its reply."
        if not self._pending or time.time() - self._pending[1] > 60:
            self._pending = None
            return "Nothing pending, or it expired. Send /tick, /exit or /guard first, then /confirm within 60 seconds."
        action, self._pending = self._pending[0], None
        self._busy = {"exit": "guarded exit", "guard": "workflow guard"}.get(action, "real tick")
        try:
            if action == "guard":
                return await self._run_cli(
                    ["exit-guard", "run", "-d", str(self._strategy_dir), "--chain", self._chain], "workflow guard"
                )
            if action == "exit":
                return await self._run_cli(
                    ["exit", "-d", str(self._strategy_dir), "--chain", self._chain], "guarded exit"
                )
            # --fresh: the position may have been exited outside Almanak (a guarded exit through KeeperHub),
            # and a tick against that stale state answers HOLD instead of acting.
            return await self._run_cli(["run", "-d", str(self._strategy_dir), "--once", "--fresh"], "real tick")
        finally:
            self._busy = None

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
        return f"<b>demo {_h(name)}</b>: {'ok' if code == 0 else 'exit ' + str(code)}\n{_h(_summarise(output))}"

    async def _run_cli(self, args: list[str], label: str) -> str:
        output, code = await asyncio.to_thread(self._runner, args)
        return _pretty(label, output, code)

    # -- Telegram transport ----------------------------------------------------------

    async def send(self, chat_id: str, text: str, keyboard: list[list[tuple[str, str]]] | None = None) -> None:
        chunks = [text[i : i + 3800] for i in range(0, max(len(text), 1), 3800)]
        async with httpx.AsyncClient(timeout=20.0) as http:
            for index, chunk in enumerate(chunks):
                body: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if keyboard and index == len(chunks) - 1:
                    body["reply_markup"] = {
                        "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in keyboard]
                    }
                response = await self._post_with_retries(http, body)
                if response.status_code == 400:  # markup Telegram would not take: send it plain rather than lose it
                    body.pop("parse_mode")
                    body["text"] = re.sub(r"<[^>]+>", "", chunk)
                    await http.post(f"https://api.telegram.org/bot{self._token}/sendMessage", json=body)

    async def _post_with_retries(self, http: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        """A dropped connection must not lose a reply: retry the send a few times before giving up."""
        last: Exception | None = None
        for attempt in range(4):
            try:
                return await http.post(f"https://api.telegram.org/bot{self._token}/sendMessage", json=body)
            except httpx.TransportError as exc:
                last = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last if last else RuntimeError("send failed")

    async def _typing(self, chat_id: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{self._token}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )

    async def _install_menu(self) -> None:
        """The slash menu Telegram shows when the user types /."""
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{self._token}/setMyCommands",
                json={"commands": [{"command": c, "description": d} for c, d in COMMAND_MENU]},
            )

    @staticmethod
    def keyboard_for(command: str, reply: str) -> list[list[tuple[str, str]]] | None:
        """Buttons under a reply: confirm/cancel after an armed action, the main menu after the rest."""
        if command in ("/tick", "/exit", "/guard") and "/confirm" in reply:
            return CONFIRM_KEYBOARD
        if command in ("/help", "/start", "/status", "/confirm", "/cancel", "/simulate", "/executions"):
            return MAIN_KEYBOARD
        return None

    async def run_forever(self) -> None:
        if self.owner_chat_id is None:
            print(f"operator bot has no owner yet: send it  /start {self.start_secret}  from your Telegram chat")
        logger.info(
            "operator bot polling (owner=%s, strategy=%s)", self.owner_chat_id or "unclaimed", self._strategy_dir
        )
        try:
            await self._install_menu()
        except Exception as exc:  # noqa: BLE001 - the menu is a nicety
            logger.warning("could not install the command menu: %s", exc)
        async with httpx.AsyncClient(timeout=60.0) as http:
            while True:
                try:
                    r = await http.get(
                        f"https://api.telegram.org/bot{self._token}/getUpdates",
                        params={
                            "timeout": 50,
                            "offset": self._offset,
                            "allowed_updates": '["message","callback_query"]',
                        },
                    )
                    payload = r.json() if r.content else {}
                    if not payload.get("ok"):
                        description = str(payload.get("description") or r.status_code)
                        if r.status_code == 401:
                            raise SystemExit(f"telegram rejected the bot token: {description}")
                        logger.warning("telegram getUpdates not ok: %s", description)
                        await asyncio.sleep(5)
                        continue
                    for update in payload.get("result", []):
                        self._offset = int(update["update_id"]) + 1
                        chat_id, text, sent_at = self.parse_update(update)
                        if not chat_id or not text:
                            continue
                        if update.get("callback_query"):
                            asyncio.create_task(self._ack_callback(http, update["callback_query"]["id"]))
                        asyncio.create_task(self._answer(chat_id, text, sent_at))
                except SystemExit:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep polling
                    logger.warning("telegram polling error: %s", exc)
                    await asyncio.sleep(5)

    @staticmethod
    def parse_update(update: dict[str, Any]) -> tuple[str, str, float]:
        """A typed message or a tapped button, as (chat id, command text, sent-at)."""
        if query := update.get("callback_query"):
            chat = (query.get("message") or {}).get("chat") or {}
            return str(chat.get("id", "")), str(query.get("data") or ""), time.time()
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        return chat_id, message.get("text") or "", float(message.get("date") or time.time())

    async def _ack_callback(self, http: httpx.AsyncClient, callback_id: str) -> None:
        try:
            await http.post(
                f"https://api.telegram.org/bot{self._token}/answerCallbackQuery",
                json={"callback_query_id": callback_id},
            )
        except Exception:  # noqa: BLE001 - only stops the button spinner
            pass

    async def _answer(self, chat_id: str, text: str, sent_at: float) -> None:
        if time.time() - sent_at > STALE_AFTER_SECONDS:
            return
        command = text.split()[0].lower() if text.split() else ""
        if command in ("/simulate", "/confirm", "/demo", "/verify") or text.lower().startswith("/guard stale"):
            try:
                await self.send(
                    chat_id,
                    "Working. This goes through KeeperHub; a real tick takes about a minute and a half (Almanak's gateway boots first), the workflow guard a few seconds.",
                )
                await self._typing(chat_id)
            except Exception as exc:  # noqa: BLE001 - the notice is optional; the command still runs
                logger.warning("could not send the working notice: %s", exc)
        reply = await self.handle(chat_id=chat_id, text=text, sent_at=sent_at)
        if reply:
            try:
                await self.send(chat_id, reply, self.keyboard_for(command, reply))
            except Exception as exc:  # noqa: BLE001
                logger.error("reply lost after retries: %s", exc)


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
