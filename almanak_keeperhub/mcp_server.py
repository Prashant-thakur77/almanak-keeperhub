"""An MCP server over the strategy: the same proof and controls the Telegram
bot and the console expose, for any agent that speaks MCP (Claude, Cursor, an
n8n AI Agent).

Read tools and the dry run are always registered. The tools that broadcast are
registered only when the server is started with ``--write``, the way
KeeperHub's own keys split ``mcp:read`` from ``mcp:write``: an agent given the
default server can inspect and simulate everything and sign nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from almanak_keeperhub.bot import DEMOS, Runner, _default_runner, _summarise
from almanak_keeperhub.console.server import build_state, keeper_state, verify_reference
from almanak_keeperhub.verify import valid_reference


class StrategyTools:
    """The tool implementations, kept free of the MCP framework so they can be
    tested by calling them and so the bot and the server cannot drift."""

    def __init__(
        self,
        *,
        strategy_dir: Path,
        chain: str,
        base_url: str,
        runner: Runner | None = None,
        allow_broadcast: bool = False,
    ) -> None:
        self.strategy_dir = Path(strategy_dir)
        self.chain = chain
        self.base_url = base_url
        self.allow_broadcast = allow_broadcast
        self._runner = runner or _default_runner

    # -- state ---------------------------------------------------------------------

    def _docs(self) -> Path:
        parents = self.strategy_dir.parents
        return parents[1] / "docs" if len(parents) > 1 else self.strategy_dir / "docs"

    def _receipts(self) -> Path:
        from almanak_keeperhub.receipts import receipts_path

        if os.environ.get("ALMANAK_KEEPERHUB_RECEIPTS"):
            return receipts_path()
        return self.strategy_dir / "keeperhub-receipts.json"

    def state(self) -> dict[str, Any]:
        docs = self._docs()
        return build_state(
            self._receipts(),
            docs / "receipts.json",
            docs / "benchmark.json",
            org_wallet=os.environ.get("KEEPERHUB_WALLET_ADDRESS", ""),
            base_url=self.base_url,
            chain=self.chain,
        )

    # -- read tools ----------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        state = self.state()
        keeper = await keeper_state(Path(state["sources"]["receipts"]))
        return {
            "chain": self.chain,
            "org_wallet": state["org_wallet"] or None,
            "keeperhub": self.base_url,
            "summary": state["summary"],
            "keeper": {k: keeper.get(k) for k in ("deployed", "workflow_id", "enabled", "cron")},
            "broadcast_enabled": self.allow_broadcast,
        }

    def list_executions(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.state()["executions"][: max(1, min(int(limit), 50))]
        keys = (
            "function",
            "to",
            "status",
            "verified",
            "sponsored",
            "idempotent_replay",
            "execution_id",
            "tx_hash",
            "explorer",
            "recorded_at",
        )
        return [{k: e.get(k) for k in keys} for e in rows]

    def list_dry_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.state().get("dry_runs", [])[: max(1, min(int(limit), 50))]
        return [dict(r) for r in rows]

    async def verify(self, reference: str) -> dict[str, Any]:
        if not valid_reference(reference):
            return {"error": "reference must be a transaction hash or a KeeperHub execution id"}
        state = self.state()
        return await verify_reference(reference, Path(state["sources"]["receipts"]), self.chain)

    def benchmark(self) -> dict[str, Any]:
        path = self._docs() / "benchmark.json"
        if not path.exists():
            return {"error": f"no benchmark recorded yet ({path})"}
        return json.loads(path.read_text())

    def failure_modes(self) -> list[dict[str, Any]]:
        path = self._docs() / "receipts.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())

    async def keeper(self) -> dict[str, Any]:
        state = self.state()
        return await keeper_state(Path(state["sources"]["receipts"]))

    # -- actions ---------------------------------------------------------------------

    async def _cli(self, args: list[str]) -> dict[str, Any]:
        before = {e.get("execution_id") for e in self.state()["executions"]}
        output, code = await asyncio.to_thread(self._runner, args)
        after = [e for e in self.state()["executions"] if e.get("execution_id") not in before]
        return {
            "ok": code == 0,
            "exit_code": code,
            "summary": _summarise(output),
            "new_executions": after,
        }

    async def simulate_tick(self) -> dict[str, Any]:
        """One strategy tick, dry-run through KeeperHub; nothing is signed or broadcast."""
        return await self._cli(["run", "-d", str(self.strategy_dir), "--once", "--fresh", "--simulate-only"])

    async def run_tick(self, confirm: bool = False) -> dict[str, Any]:
        if not self.allow_broadcast:
            return {
                "ok": False,
                "error": "broadcast is disabled on this server; start it with --write to allow real ticks",
            }
        if not confirm:
            return {
                "ok": False,
                "error": "a real tick signs and broadcasts through KeeperHub; call again with confirm=true",
            }
        return await self._cli(["run", "-d", str(self.strategy_dir), "--once"])

    async def run_failure_demo(self, name: str) -> dict[str, Any]:
        script = DEMOS.get(name)
        if script is None:
            return {"ok": False, "error": f"unknown demo {name!r}; one of {sorted(DEMOS)}"}
        demos = self._docs().parent / "demos" / "failure_modes"
        output, code = await asyncio.to_thread(self._run_script, demos / f"{script}.py")
        return {"ok": code == 0, "exit_code": code, "summary": _summarise(output)}

    def _run_script(self, path: Path) -> tuple[str, int]:
        import subprocess
        import sys

        proc = subprocess.run(  # noqa: S603 - our own demo script, fixed argv
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            env=os.environ.copy(),
            check=False,
            timeout=900,
        )
        return (proc.stdout + proc.stderr, proc.returncode)


def build_server(tools: StrategyTools):
    """Register the tools on a FastMCP server. Imported lazily: ``mcp`` is an
    optional dependency and nothing else in the package needs it."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "almanak-keeperhub",
        instructions=(
            "An Almanak DeFi strategy whose execution layer is KeeperHub. Read tools report "
            "recorded executions with KeeperHub's verified receipts; simulate_tick dry-runs one "
            "strategy tick through KeeperHub without signing. run_tick broadcasts and is only "
            "available when the server was started with --write."
        ),
    )

    @server.tool()
    async def status() -> dict[str, Any]:
        """Org wallet, chain, execution counts, keeper workflow state, and whether broadcast is enabled."""
        return await tools.status()

    @server.tool()
    def list_executions(limit: int = 5) -> list[dict[str, Any]]:
        """Latest KeeperHub executions this strategy produced: function, status, verified and sponsored flags, hash, explorer link."""
        return tools.list_executions(limit)

    @server.tool()
    def list_dry_runs(limit: int = 5) -> list[dict[str, Any]]:
        """Latest KeeperHub dry runs, including the ones that refused a call that would revert."""
        return tools.list_dry_runs(limit)

    @server.tool()
    async def verify(reference: str) -> dict[str, Any]:
        """Ask KeeperHub for its verdict on a transaction hash or execution id and decode the receipt to name who acted."""
        return await tools.verify(reference)

    @server.tool()
    def benchmark() -> dict[str, Any]:
        """The recorded benchmark: refusals before broadcast, dry runs, landed executions, latency."""
        return tools.benchmark()

    @server.tool()
    def failure_modes() -> list[dict[str, Any]]:
        """The recorded failure-mode demos: what was attempted, what refused it, and whether anything was broadcast."""
        return tools.failure_modes()

    @server.tool()
    async def keeper() -> dict[str, Any]:
        """The scheduled KeeperHub compounder workflow generated from the strategy, and its executions."""
        return await tools.keeper()

    @server.tool()
    async def simulate_tick() -> dict[str, Any]:
        """Dry-run one strategy tick through KeeperHub. Nothing is signed or broadcast."""
        return await tools.simulate_tick()

    @server.tool()
    async def run_failure_demo(name: str) -> dict[str, Any]:
        """Run one recorded failure mode: revert, cap, duplicate, crash, selector or rpc."""
        return await tools.run_failure_demo(name)

    if tools.allow_broadcast:

        @server.tool()
        async def run_tick(confirm: bool = False) -> dict[str, Any]:
            """Run one REAL strategy tick: sign and broadcast through KeeperHub. Requires confirm=true."""
            return await tools.run_tick(confirm)

    @server.resource("almanak-keeperhub://receipts")
    def receipts() -> str:
        """Every execution this strategy produced, as recorded by the submitter."""
        path = tools._receipts()
        return path.read_text() if path.exists() else "[]"

    return server


def serve(
    *,
    strategy_dir: Path,
    chain: str,
    base_url: str,
    allow_broadcast: bool,
) -> None:
    tools = StrategyTools(strategy_dir=strategy_dir, chain=chain, base_url=base_url, allow_broadcast=allow_broadcast)
    build_server(tools).run(transport="stdio")
