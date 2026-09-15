"""``almanak-keeperhub``: run Almanak strategies with KeeperHub as the execution backend.

    almanak-keeperhub doctor                      # key, org wallet, balances, chain, selector index
    almanak-keeperhub run --once --dry-run        # Almanak plans; nothing reaches the gateway
    almanak-keeperhub run --once --simulate-only  # KeeperHub dry-runs the compiled bundle; no broadcast
    almanak-keeperhub run --once                  # broadcast through KeeperHub

``run`` installs the KeeperHub gateway servicer, points Almanak's wallet
registry at the KeeperHub organization wallet, then hands over to Almanak's
own ``strat run`` command unchanged. Every execution is appended to
``keeperhub-receipts.json`` in the strategy directory and summarised at exit.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import click
import httpx

from almanak_keeperhub import __version__
from almanak_keeperhub.calldata import SelectorIndex
from almanak_keeperhub.client import DEFAULT_BASE_URL, KeeperHubClient
from almanak_keeperhub.receipts import DEFAULT_FILENAME, ReceiptLog
from almanak_keeperhub.wallets import KIND

USDC_BY_CHAIN_ID = {
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    84532: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}


@click.group()
@click.version_option(__version__)
def main() -> None:
    """KeeperHub execution backend for Almanak."""
    from almanak_keeperhub.testnet import register_testnets

    register_testnets()  # base_sepolia (84532) as a first-class Almanak chain


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option("--chain", "chain_override", default=None, help="Chain name; defaults to config.json 'chain'.")
@click.option("--working-dir", "-d", default=".", help="Strategy directory (passed through to almanak).")
@click.option("--config", "-c", "config_file", default=None, help="Strategy config JSON (passed through).")
@click.option(
    "--simulate-only",
    is_flag=True,
    default=False,
    help="Compile the strategy's bundle, dry-run it through KeeperHub, stop before broadcasting.",
)
@click.argument("almanak_args", nargs=-1, type=click.UNPROCESSED)
def run(
    chain_override: str | None,
    working_dir: str,
    config_file: str | None,
    simulate_only: bool,
    almanak_args: tuple[str, ...],
) -> None:
    """Run `almanak strat run ...` through KeeperHub.

    `--dry-run` is Almanak's planning mode: the strategy decides, nothing reaches the
    gateway. `--simulate-only` goes one step further: the compiled transactions are
    dry-run by KeeperHub and prepared, and the pipeline stops before submission.
    """
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set (organization API key with mcp:write scope)")
    chain = chain_override or _chain_from_config(Path(working_dir), config_file)
    if not chain:
        raise click.ClickException("could not determine the chain: pass --chain or put 'chain' in config.json")

    os.environ.setdefault("ALMANAK_GATEWAY_WALLETS", json.dumps({chain: {"kind": KIND}}))
    os.environ.setdefault("ALMANAK_KEEPERHUB_RECEIPTS", str(Path(working_dir).resolve() / DEFAULT_FILENAME))
    if simulate_only:
        os.environ["ALMANAK_KEEPERHUB_SIMULATE_ONLY"] = "1"
        # A simulate-only tick must leave no footprint: Almanak strategies move their own
        # state optimistically when they emit an intent, so run against a throwaway store.
        throwaway = Path(tempfile.mkdtemp(prefix="almanak-keeperhub-simulate-")) / "almanak_state.db"
        os.environ["ALMANAK_STATE_DB"] = str(throwaway)
    # No local key: the KeeperHub organization wallet signs inside Turnkey.
    for var in ("ALMANAK_PRIVATE_KEY", "PRIVATE_KEY"):
        if os.environ.pop(var, None):
            click.echo(f"ignoring {var}: KeeperHub signs, no local key is used", err=True)

    # Resolve the org wallet here, on the CLI thread, so the gateway's boot (which has a short
    # start budget) never blocks on an HTTP call inside the wallet registry plugin.
    if not os.environ.get("KEEPERHUB_WALLET_ADDRESS"):
        os.environ["KEEPERHUB_WALLET_ADDRESS"] = asyncio.run(_resolve_wallet(api_key))
    click.echo(f"org wallet: {os.environ['KEEPERHUB_WALLET_ADDRESS']}")

    from almanak_keeperhub.gateway import install

    install()
    mode = "simulate-only (no broadcast)" if simulate_only else "execute"
    click.echo(
        f"almanak-keeperhub {__version__}: chain={chain} backend=keeperhub "
        f"({os.environ.get('KEEPERHUB_BASE_URL', DEFAULT_BASE_URL)}) mode={mode}"
    )

    from almanak.cli import almanak

    args = ["strat", "run", "--working-dir", working_dir]
    if config_file:
        args += ["--config", config_file]
    args += list(almanak_args)
    # KeeperHub's dry run is the point of this backend: simulate every bundle before broadcast
    # unless the operator explicitly turns it off.
    if not {"--simulate-tx", "--no-simulate-tx"} & set(almanak_args):
        args.append("--simulate-tx")

    started = datetime.now(UTC).isoformat()
    exit_code = 0
    try:
        almanak.main(args=args, prog_name="almanak", standalone_mode=True)
    except SystemExit as exc:  # click always exits; keep its code, but print the proof first
        exit_code = _exit_code(exc.code)
    finally:
        _print_execution_summary(started)
    sys.exit(exit_code)


async def _resolve_wallet(api_key: str) -> str:
    client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
    try:
        return await client.wallet_address()
    except Exception as exc:  # noqa: BLE001 - turn into a CLI error with the remedy
        raise click.ClickException(f"could not resolve the KeeperHub organization wallet: {exc}") from exc
    finally:
        await client.aclose()


def _exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _print_execution_summary(started_iso: str) -> None:
    """The proof, where the run ended: every KeeperHub execution this run produced."""
    log = ReceiptLog()
    rows = log.entries_since(started_iso)
    simulations = [r for r in rows if r.get("type") == "simulation"]
    entries = [r for r in rows if r.get("type") != "simulation"]
    click.echo("")
    if simulations:
        refused = sum(1 for s in simulations if not s.get("success"))
        click.echo(
            f"KeeperHub dry runs this run: {len(simulations)} ({refused} would revert, refused before broadcast)"
        )
    if not entries:
        click.echo("KeeperHub executions this run: none (dry run, simulate-only, or nothing to do)")
        return
    click.echo(f"KeeperHub executions this run ({len(entries)}), recorded in {log.path}:")
    for entry in entries:
        flags = [name for name in ("sponsored", "idempotent_replay") if entry.get(name)]
        suffix = f" [{', '.join(flags)}]" if flags else ""
        click.echo(
            f"  {entry.get('function')} -> {entry.get('to')}  execution={entry.get('execution_id')}  "
            f"status={entry.get('status')} verified={entry.get('verified')}{suffix}"
        )
        click.echo(f"    tx {entry.get('tx_hash')}  {entry.get('transaction_link') or ''}")


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option("--chain", "chain_name", default="base", show_default=True, help="Chain the org wallet executes on.")
@click.argument("ax_args", nargs=-1, type=click.UNPROCESSED)
def ax(chain_name: str, ax_args: tuple[str, ...]) -> None:
    """Run Almanak's agent CLI (`almanak ax ...`) with KeeperHub as the executor.

    Structured (`swap USDC WETH 1 --dry-run`) or natural language (`-n "swap 1 USDC to WETH on base"`,
    needs AGENT_LLM_API_KEY). The agent decides; the compiled transactions are dry-run and
    broadcast through KeeperHub exactly like a strategy tick.
    """
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set (organization API key with mcp:write scope)")
    os.environ.setdefault("ALMANAK_GATEWAY_WALLETS", json.dumps({chain_name: {"kind": KIND}}))
    os.environ.setdefault("ALMANAK_KEEPERHUB_RECEIPTS", str(Path.cwd() / DEFAULT_FILENAME))
    for var in ("ALMANAK_PRIVATE_KEY", "PRIVATE_KEY"):
        if os.environ.pop(var, None):
            click.echo(f"ignoring {var}: KeeperHub signs, no local key is used", err=True)
    if not os.environ.get("KEEPERHUB_WALLET_ADDRESS"):
        os.environ["KEEPERHUB_WALLET_ADDRESS"] = asyncio.run(_resolve_wallet(api_key))
    wallet = os.environ["KEEPERHUB_WALLET_ADDRESS"]
    os.environ["ALMANAK_WALLET_ADDRESS"] = wallet

    from almanak_keeperhub.gateway import install

    install()
    click.echo(f"almanak-keeperhub {__version__}: ax on {chain_name} as {wallet} via KeeperHub")

    from almanak.cli import almanak

    started = datetime.now(UTC).isoformat()
    exit_code = 0
    try:
        almanak.main(
            args=["ax", "--wallet", wallet, "--chain", chain_name, *ax_args], prog_name="almanak", standalone_mode=True
        )
    except SystemExit as exc:
        exit_code = _exit_code(exc.code)
    finally:
        _print_execution_summary(started)
    sys.exit(exit_code)


@main.command()
@click.argument("reference")
@click.option("--chain", "chain_name", default="base", show_default=True)
def verify(reference: str, chain_name: str) -> None:
    """Show KeeperHub's verdict and the on-chain evidence for a transaction hash or execution id.

    Sponsored transactions show KeeperHub's relayer as sender on the explorer; the events
    still name the organization wallet. This prints both sides.
    """
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set")
    sys.exit(asyncio.run(_verify(reference, api_key, chain_name)))


async def _verify(reference: str, api_key: str, chain_name: str) -> int:
    from almanak_keeperhub.verify import actor_evidence, valid_reference

    if not valid_reference(reference):
        raise click.ClickException("not a transaction hash or execution id")
    client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
    try:
        org_wallet = await client.wallet_address()
        execution_id, tx_hash = reference, None
        if reference.startswith("0x") and len(reference) == 66:
            tx_hash = reference
            entry = ReceiptLog().find_by_hash(reference)
            if entry is None:
                raise click.ClickException(f"{reference} is not in {ReceiptLog().path}; pass the execution id instead")
            execution_id = str(entry["execution_id"])
        status = await client.execution_status(execution_id)
        tx_hash = tx_hash or status.transaction_hash
        click.echo(f"KeeperHub execution : {status.execution_id}")
        click.echo(f"status              : {status.status}  sponsored={status.sponsored}")
        for r in status.receipts:
            click.echo(f"receipt             : {r.hash} verified={r.verified} receiptStatus={r.receipt_status}")
        click.echo(f"link                : {status.transaction_link or ''}")
        click.echo(f"org wallet          : {org_wallet}")
        if not tx_hash:
            click.echo("no transaction hash yet (not broadcast, or refused before broadcast)")
            return 0
        rpc_url = (
            os.environ.get(f"ALMANAK_{chain_name.upper()}_RPC_URL")
            or os.environ.get(f"{chain_name.upper()}_RPC_URL")
            or os.environ.get("RPC_URL_BASE")
        )
        if not rpc_url:
            click.echo("on-chain evidence   : skipped (set ALMANAK_<CHAIN>_RPC_URL)")
            return 0
        from web3 import AsyncHTTPProvider, AsyncWeb3

        web3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        receipt = await web3.eth.get_transaction_receipt(tx_hash)  # type: ignore[arg-type]
        sender = str(receipt["from"]).lower()
        who = (
            "the org wallet"
            if sender == org_wallet.lower()
            else "KeeperHub's relayer (sponsored gas), not the org wallet"
        )
        click.echo(f"on-chain sender     : {sender} = {who}")
        click.echo(
            f"on-chain status     : {'success' if receipt['status'] == 1 else 'reverted'} in block {receipt['blockNumber']}"
        )
        lines = actor_evidence([dict(log) for log in receipt["logs"]], org_wallet)
        if not lines:
            click.echo("events              : none recognised (Transfer, Approval, Deposit, Withdraw)")
        for line in lines:
            click.echo(f"event               : {line}")
        return 0
    finally:
        await client.aclose()


@main.command()
@click.option("--receipts", default=None, help="Strategy receipts file (default: ./keeperhub-receipts.json).")
@click.option(
    "--docs", "docs_dir", default=None, help="Directory holding receipts.json and benchmark.json (default: ./docs)."
)
@click.option("--port", default=8642, show_default=True)
@click.option("--chain", "chain_name", default="base", show_default=True)
@click.option("--open/--no-open", "open_browser", default=True, help="Open the page in a browser.")
@click.option(
    "--export",
    "export_dir",
    default=None,
    help="Write the console as a static site into this directory (with every execution's evidence frozen) and exit.",
)
@click.option(
    "--refresh-evidence",
    is_flag=True,
    help="With --export: ask KeeperHub again about every execution instead of keeping the verdicts already frozen.",
)
def console(
    receipts: str | None,
    docs_dir: str | None,
    port: int,
    chain_name: str,
    open_browser: bool,
    export_dir: str | None,
    refresh_evidence: bool,
) -> None:
    """Serve the execution console: executions, verdicts, failure modes and the benchmark, live.

    Keep it open in a browser while `run`, `ax` or the demos execute in a terminal; it re-reads
    the proof files every two seconds. Inspect asks KeeperHub and the chain for the evidence.
    With --export it writes the same page as a static site instead, for GitHub Pages.
    """
    import webbrowser

    from almanak_keeperhub.console.server import ConsoleServer

    receipts_path = (
        Path(receipts) if receipts else Path(os.environ.get("ALMANAK_KEEPERHUB_RECEIPTS") or DEFAULT_FILENAME)
    )
    docs = Path(docs_dir) if docs_dir else _find_docs_dir()
    org_wallet = os.environ.get("KEEPERHUB_WALLET_ADDRESS", "")
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not org_wallet and api_key:
        try:
            org_wallet = asyncio.run(_resolve_wallet(api_key))
        except click.ClickException as exc:
            click.echo(f"org wallet unknown ({exc.message}); Inspect will still try", err=True)
    if export_dir:
        from almanak_keeperhub.console.export import export_site_sync

        summary = export_site_sync(
            Path(export_dir),
            receipts=receipts_path.resolve(),
            demo_receipts=(docs / "receipts.json").resolve(),
            benchmark=(docs / "benchmark.json").resolve(),
            org_wallet=org_wallet,
            base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL),
            chain=chain_name,
            reuse=None if refresh_evidence else Path(export_dir) / "console-data" / "verify",
        )
        click.echo(
            f"static console written to {summary['out_dir']}: {summary['executions']} executions, "
            f"{summary['evidence_files']} evidence files, snapshot {summary['snapshot_at']}"
        )
        return
    server = ConsoleServer(
        receipts=receipts_path.resolve(),
        demo_receipts=(docs / "receipts.json").resolve(),
        benchmark=(docs / "benchmark.json").resolve(),
        org_wallet=org_wallet,
        base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL),
        port=port,
        chain_name=chain_name,
    )
    url = f"http://127.0.0.1:{server.port}/"
    click.echo(f"execution console: {url}")
    click.echo(f"  receipts  {server.receipts}")
    click.echo(f"  demos     {server.demo_receipts}")
    click.echo(f"  benchmark {server.benchmark}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _find_docs_dir() -> Path:
    """The repo's docs/ directory when run from inside the checkout, else ./docs."""
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "docs" / "receipts.json").exists() or (candidate / "pyproject.toml").exists():
            return candidate / "docs"
    return Path.cwd() / "docs"


@main.command("exit")
@click.option("--chain", "chain_name", default=None, help="Chain name; defaults to config.json 'chain'.")
@click.option("-d", "--working-dir", default=".", show_default=True, help="Strategy directory (config.json, receipts).")
@click.option("--simulate", is_flag=True, help="Dry-run the guarded exit; nothing is signed.")
@click.option(
    "--expect-shares",
    type=int,
    default=None,
    help="Redeem this many shares instead of the current balance; the guard then decides whether it still holds.",
)
def guarded_exit(chain_name: str | None, working_dir: str, simulate: bool, expect_shares: int | None) -> None:
    """Redeem the vault position through KeeperHub's check-and-execute: KeeperHub re-reads the balance
    right before the write and only redeems if it still covers the request.

    A decision taken on a stale snapshot (the keeper compounded, another process already exited,
    a retry of an exit that landed) comes back executed=false with the observed balance, instead of
    a revert. Set ALMANAK_KEEPERHUB_EXIT_ID to retry one decision under its own idempotency key.
    """
    import time

    from almanak_keeperhub.demo_targets import demo_targets
    from almanak_keeperhub.guarded_exit import GuardedExit, current_shares, describe, run_guarded_exit
    from almanak_keeperhub.receipts import ReceiptLog

    chain = chain_name or _chain_from_config(Path(working_dir), None) or "base"
    os.environ["ALMANAK_KEEPERHUB_CHAIN"] = chain
    targets = demo_targets()
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set")

    async def run() -> int:
        client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
        try:
            wallet = await client.wallet_address()
            held = await current_shares(targets.rpc, targets.vault, wallet)
            shares = held if expect_shares is None else expect_shares
            click.echo(f"vault {targets.vault} on {targets.chain}: {held} shares held by {wallet}; asking for {shares}")
            if shares <= 0:
                click.echo("nothing to redeem")
                return 0
            if targets.chain_id != 84532 and not simulate and not click.confirm("redeem on a mainnet?"):
                return 2
            started = time.perf_counter()
            exit_ = GuardedExit(
                vault=targets.vault,
                chain_id=targets.chain_id,
                wallet=wallet,
                shares=shares,
                work_id=os.environ.get("ALMANAK_KEEPERHUB_EXIT_ID") or f"exit-{int(time.time())}",
            )
            receipts = ReceiptLog(Path(working_dir) / "keeperhub-receipts.json")
            outcome = await run_guarded_exit(client, exit_, simulate=simulate, receipts=receipts)
            for key, value in describe(outcome, exit_, started).items():
                click.echo(f"{key:<18}: {value}")
            if outcome.executed and outcome.transaction_hash:
                click.echo(f"{'explorer':<18}: {targets.explorer}{outcome.transaction_hash}")
            if outcome.idempotent_replay:
                click.echo(
                    "note              : KeeperHub replayed an earlier execution of this decision; nothing new landed"
                )
            return 0 if outcome.executed or simulate else 3
        finally:
            await client.aclose()

    sys.exit(asyncio.run(run()))


@main.command("merge-receipts")
@click.argument("logs", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--into", "into", required=True, type=click.Path(dir_okay=False), help="The union to write.")
def merge_receipts(logs: tuple[str, ...], into: str) -> None:
    """Union several receipts logs into one file, one entry per execution, oldest first.

    The file named by --into is read as one of the inputs, so entries only ever accumulate.
    """
    from almanak_keeperhub.receipts import merge_logs

    target = Path(into)
    sources = [target] if target.exists() else []
    sources += [Path(log) for log in logs]
    merged = merge_logs(*sources)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=1) + "\n")
    executions = sum(1 for e in merged if e.get("execution_id"))
    click.echo(f"{target}: {len(merged)} entries, {executions} executions")


@main.group()
def keeper() -> None:
    """The scheduled KeeperHub workflow that compounds idle balances between Almanak ticks.

    Almanak decides entry and exit. This keeper runs on KeeperHub's own scheduler, with no
    Almanak process required, and only moves balances inside a bounded window.
    """


def _keeper_client() -> KeeperHubClient:
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set")
    return KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))


def _keeper_workflow(
    working_dir: str,
    vault: str | None,
    token: str | None,
    symbol: str | None,
    min_amount: str,
    max_amount: str,
    cron: str,
) -> dict:
    from almanak_keeperhub.keeper import build_compounder_workflow, keeper_params_from_config

    params: dict = {}
    config_path = Path(working_dir) / "config.json"
    if config_path.exists():
        try:
            params = keeper_params_from_config(config_path)
        except ValueError as exc:
            click.echo(f"config.json: {exc}", err=True)
    vault = vault or params.get("vault")
    token = token or params.get("token")
    symbol = symbol or params.get("token_symbol") or "USDC"
    chain_id = int(params.get("chain_id") or 8453)
    if not vault or not token:
        raise click.ClickException(
            "need --vault and --token (or a strategy config.json with vault_address and deposit_token)"
        )
    wallet = os.environ.get("KEEPERHUB_WALLET_ADDRESS") or asyncio.run(
        _resolve_wallet(os.environ.get("KEEPERHUB_API_KEY", ""))
    )
    return build_compounder_workflow(
        vault=vault,
        token=token,
        token_symbol=symbol,
        chain_id=chain_id,
        wallet=wallet,
        min_amount=min_amount,
        max_amount=max_amount,
        cron=cron,
    )


KEEPER_OPTIONS = [
    click.option("--working-dir", "-d", default=".", help="Strategy directory with config.json (vault, token, chain)."),
    click.option("--vault", default=None, help="ERC-4626 vault address (default: config.json vault_address)."),
    click.option("--token", default=None, help="Token address (default: the strategy's deposit token)."),
    click.option("--symbol", default=None, help="Token symbol (default: config.json deposit_token)."),
    click.option(
        "--min", "min_amount", default="1", show_default=True, help="Smallest idle balance to compound (human units)."
    ),
    click.option(
        "--max",
        "max_amount",
        default="90",
        show_default=True,
        help="Largest balance the keeper may move; above it, Almanak decides.",
    ),
    click.option("--every", "cron", default="0 */6 * * *", show_default=True, help="Cron schedule (UTC)."),
]


def _with_keeper_options(fn):
    for option in reversed(KEEPER_OPTIONS):
        fn = option(fn)
    return fn


@keeper.command("show")
@_with_keeper_options
def keeper_show(
    working_dir: str,
    vault: str | None,
    token: str | None,
    symbol: str | None,
    min_amount: str,
    max_amount: str,
    cron: str,
) -> None:
    """Print the workflow JSON that would be deployed."""
    click.echo(json.dumps(_keeper_workflow(working_dir, vault, token, symbol, min_amount, max_amount, cron), indent=2))


@keeper.command("deploy")
@_with_keeper_options
@click.option("--enable/--no-enable", default=False, help="Enable the workflow right after creating it.")
def keeper_deploy(
    working_dir: str,
    vault: str | None,
    token: str | None,
    symbol: str | None,
    min_amount: str,
    max_amount: str,
    cron: str,
    enable: bool,
) -> None:
    """Create the compounder workflow in KeeperHub (disabled unless --enable) and remember its id."""
    from almanak_keeperhub.keeper import deploy, set_enabled, state_path, validate_remote

    workflow = _keeper_workflow(working_dir, vault, token, symbol, min_amount, max_amount, cron)

    async def go() -> dict:
        client = _keeper_client()
        try:
            created = await deploy(client, workflow)
            workflow_id = str(
                created.get("id") or created.get("workflowId") or created.get("workflow", {}).get("id", "")
            )
            if not workflow_id:
                raise click.ClickException(f"KeeperHub returned no workflow id: {json.dumps(created)[:300]}")
            # Remember the id before validating or enabling: a failure after create must not orphan it.
            state_path(Path(working_dir)).write_text(
                json.dumps(
                    {
                        "workflow_id": workflow_id,
                        "name": workflow["name"],
                        "enabled": False,
                        "created_at": datetime.now(UTC).isoformat(),
                        "cron": cron,
                        "min": min_amount,
                        "max": max_amount,
                    },
                    indent=2,
                )
                + "\n"
            )
            verdict = await validate_remote(client, workflow_id)
            if enable:
                await set_enabled(client, workflow_id, True)
            return {"workflow_id": workflow_id, "name": workflow["name"], "validation": verdict, "enabled": enable}
        finally:
            await client.aclose()

    result = asyncio.run(go())
    state = state_path(Path(working_dir))
    state.write_text(
        json.dumps(
            {**result, "created_at": datetime.now(UTC).isoformat(), "cron": cron, "min": min_amount, "max": max_amount},
            indent=2,
        )
        + "\n"
    )
    click.echo(
        f"keeper workflow {result['workflow_id']} created ({'enabled' if enable else 'disabled'}); remembered in {state}"
    )
    validation = result.get("validation") or {}
    click.echo(
        f"KeeperHub validation: valid={validation.get('valid')} errors={len(validation.get('errors') or [])} warnings={len(validation.get('warnings') or [])}"
    )
    click.echo(f"open it: {os.environ.get('KEEPERHUB_BASE_URL', DEFAULT_BASE_URL)}/workflows/{result['workflow_id']}")


@keeper.command("enable")
@click.option("--working-dir", "-d", default=".")
@click.option("--off", is_flag=True, default=False, help="Disable instead.")
def keeper_enable(working_dir: str, off: bool) -> None:
    """Enable (or disable) the remembered keeper workflow."""
    from almanak_keeperhub.keeper import set_enabled, state_path

    state = _read_keeper_state(Path(working_dir))

    async def go() -> None:
        client = _keeper_client()
        try:
            await set_enabled(client, state["workflow_id"], not off)
        finally:
            await client.aclose()

    asyncio.run(go())
    state["enabled"] = not off
    state_path(Path(working_dir)).write_text(json.dumps(state, indent=2) + "\n")
    click.echo(f"keeper workflow {state['workflow_id']} {'disabled' if off else 'enabled'}")


@keeper.command("run")
@click.option("--working-dir", "-d", default=".")
def keeper_run(working_dir: str) -> None:
    """Trigger the remembered keeper workflow now (instead of waiting for its schedule) and follow it."""
    from almanak_keeperhub.keeper import run_now, state_path

    state = _read_keeper_state(Path(working_dir))

    async def go() -> dict:
        client = _keeper_client()
        try:
            return await run_now(client, state["workflow_id"])
        finally:
            await client.aclose()

    result = asyncio.run(go())
    click.echo(f"keeper execution {result['execution_id']}: {result['status']}  trace={' -> '.join(result['trace'])}")
    for tx in result["transaction_hashes"]:
        link = (
            f"https://sepolia.basescan.org/tx/{tx.get('hash')}"
            if int(tx.get("chainId") or 0) == 84532
            else tx.get("hash")
        )
        click.echo(f"  {tx.get('nodeId')}: {tx.get('hash')} verified={tx.get('verified')}  {link}")
    if result.get("error"):
        click.echo(f"  error: {result['error']}")
    runs = state.setdefault("manual_runs", [])
    runs.append(
        {
            "execution_id": result["execution_id"],
            "status": result["status"],
            "at": datetime.now(UTC).isoformat(),
            "started_at": result.get("started_at"),
            "completed_at": result.get("completed_at"),
            "transactions": [
                {"node": tx.get("nodeId"), "hash": tx.get("hash"), "verified": tx.get("verified")}
                for tx in result["transaction_hashes"]
            ],
            "error": result.get("error"),
        }
    )
    state_path(Path(working_dir)).write_text(json.dumps(state, indent=2) + "\n")


@keeper.command("status")
@click.option("--working-dir", "-d", default=".")
def keeper_status(working_dir: str) -> None:
    """Show the remembered keeper workflow and its latest KeeperHub executions."""
    from almanak_keeperhub.keeper import executions

    state = _read_keeper_state(Path(working_dir))

    async def go() -> list[dict]:
        client = _keeper_client()
        try:
            return await executions(client, state["workflow_id"])
        finally:
            await client.aclose()

    rows = asyncio.run(go())
    click.echo(
        f"keeper workflow {state['workflow_id']}  enabled={state.get('enabled')}  cron={state.get('cron')}  window={state.get('min')}..{state.get('max')}"
    )
    if not rows:
        click.echo("executions: none yet (the schedule has not fired, or the workflow is disabled)")
        return
    for row in rows[:20]:
        click.echo(
            f"  {row.get('createdAt') or row.get('startedAt') or ''}  {row.get('id')}  status={row.get('status')}"
        )


def _read_keeper_state(working_dir: Path) -> dict:
    from almanak_keeperhub.keeper import state_path

    path = state_path(working_dir)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"no keeper deployed yet ({path}); run `almanak-keeperhub keeper deploy`") from exc


@main.command()
@click.option(
    "--working-dir", "-d", default=".", help="Strategy directory the server operates (config.json, receipts)."
)
@click.option("--chain", "chain_name", default=None, help="Chain name; defaults to config.json 'chain'.")
@click.option("--write", "allow_broadcast", is_flag=True, help="Also register run_tick, which signs and broadcasts.")
def mcp(working_dir: str, chain_name: str | None, allow_broadcast: bool) -> None:
    """Serve this strategy to any MCP client over stdio: Claude, Cursor, an n8n agent.

    Read tools and the KeeperHub dry run are always on. Broadcasting needs --write, the
    same split as KeeperHub's own mcp:read and mcp:write keys. Needs `pip install
    almanak-keeperhub[mcp]`.
    """
    try:
        from almanak_keeperhub.mcp_server import serve
    except ModuleNotFoundError as exc:
        raise click.ClickException(
            "the MCP server needs the optional dependency: pip install 'almanak-keeperhub[mcp]'"
        ) from exc

    strategy = Path(working_dir).resolve()
    chain = chain_name or _chain_from_config(strategy, None) or "base"
    os.environ.setdefault("ALMANAK_KEEPERHUB_RECEIPTS", str(strategy / DEFAULT_FILENAME))
    if not os.environ.get("KEEPERHUB_WALLET_ADDRESS") and os.environ.get("KEEPERHUB_API_KEY"):
        try:
            os.environ["KEEPERHUB_WALLET_ADDRESS"] = asyncio.run(_resolve_wallet(os.environ["KEEPERHUB_API_KEY"]))
        except click.ClickException as exc:
            click.echo(f"org wallet unknown ({exc.message})", err=True)
    # stdout is the MCP transport; everything human goes to stderr.
    click.echo(
        f"mcp server: strategy={strategy} chain={chain} broadcast={'on (--write)' if allow_broadcast else 'off'}",
        err=True,
    )
    serve(
        strategy_dir=strategy,
        chain=chain,
        base_url=os.environ.get("KEEPERHUB_BASE_URL", "https://app.keeperhub.com"),
        allow_broadcast=allow_broadcast,
    )


@main.command()
@click.option("--working-dir", "-d", default=".", help="Strategy directory the bot operates (config.json, receipts).")
@click.option("--chain", "chain_name", default=None, help="Chain name; defaults to config.json 'chain'.")
def bot(working_dir: str, chain_name: str | None) -> None:
    """Run the Telegram operator bot: proof and controls for this strategy from a phone.

    Needs ALMANAK_KEEPERHUB_TELEGRAM_BOT_TOKEN. With no ALMANAK_KEEPERHUB_TELEGRAM_CHAT_ID the
    first chat that sends /start becomes the owner; every other chat is refused.
    """
    from almanak_keeperhub.bot import bot_from_env

    strategy = Path(working_dir).resolve()
    chain = chain_name or _chain_from_config(strategy, None) or "base"
    os.environ.setdefault("ALMANAK_KEEPERHUB_RECEIPTS", str(strategy / DEFAULT_FILENAME))
    if not os.environ.get("KEEPERHUB_WALLET_ADDRESS") and os.environ.get("KEEPERHUB_API_KEY"):
        try:
            os.environ["KEEPERHUB_WALLET_ADDRESS"] = asyncio.run(_resolve_wallet(os.environ["KEEPERHUB_API_KEY"]))
        except click.ClickException as exc:
            click.echo(f"org wallet unknown ({exc.message})", err=True)
    try:
        operator = bot_from_env(strategy, chain)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"operator bot: strategy={strategy} chain={chain} owner={operator.owner_chat_id or 'first /start claims it'}"
    )
    try:
        asyncio.run(operator.run_forever())
    except KeyboardInterrupt:
        pass


@main.command()
@click.option("--chain", "chain_name", default="base", show_default=True)
def doctor(chain_name: str) -> None:
    """Check the KeeperHub key, org wallet and balances, chain support and the calldata index."""
    problems = 0
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    base_url = os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL)
    click.echo(f"KeeperHub base URL : {base_url}")
    if not api_key:
        click.echo("KEEPERHUB_API_KEY  : MISSING  (create one under Settings > API keys, scope mcp:write)")
        problems += 1
    else:
        click.echo(f"KEEPERHUB_API_KEY  : set ({api_key[:6]}...)")
        problems += asyncio.run(_check_account(api_key, base_url, chain_name))

    index = SelectorIndex.default()
    click.echo(f"selector index     : {index.size} function signatures (almanak ABIs + signatures.json)")

    from almanak.core.chains import ChainRegistry

    descriptor = ChainRegistry.try_resolve(chain_name)
    if descriptor is None:
        click.echo(f"almanak chain      : '{chain_name}' is unknown to almanak")
        problems += 1
    else:
        click.echo(f"almanak chain      : {descriptor.name} (chain_id {descriptor.chain_id})")

    click.echo("result             : " + ("OK" if problems == 0 else f"{problems} problem(s)"))
    sys.exit(1 if problems else 0)


async def _check_account(api_key: str, base_url: str, chain_name: str) -> int:
    problems = 0
    client = KeeperHubClient(api_key=api_key, base_url=base_url)
    try:
        try:
            address = await client.wallet_address()
            click.echo(f"org wallet         : {address}")
        except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
            click.echo(f"org wallet         : FAILED ({exc})")
            return 1
        from almanak.core.chains import ChainRegistry

        descriptor = ChainRegistry.try_resolve(chain_name)
        wanted = descriptor.chain_id if descriptor else None
        try:
            response = await client._http.get("/api/chains")
            chains = response.json()
            rows = chains.get("chains", chains) if isinstance(chains, dict) else chains
            match = next(
                (c for c in rows if isinstance(c, dict) and int(c.get("chainId", c.get("id", 0)) or 0) == wanted),
                None,
            )
            if match is None:
                click.echo(f"chain {chain_name:<12}: not enabled on KeeperHub")
                problems += 1
            else:
                click.echo(
                    f"chain {chain_name:<12}: enabled on KeeperHub (chainId {wanted}, testnet={match.get('isTestnet')})"
                )
        except (httpx.HTTPError, ValueError) as exc:
            click.echo(f"chains             : could not read /api/chains ({exc})")
        try:
            response = await client._http.get("/api/analytics/spend-cap")
            caps = response.json() if response.status_code == 200 else {}
            eth_cap = caps.get("effectiveDailyCapWei")
            if eth_cap is not None:
                click.echo(
                    f"spend caps         : {int(eth_cap) / 10**18:.4f} native/day (org-wide), "
                    "100 USD per stablecoin transfer (platform)"
                )
            else:
                click.echo(f"spend caps         : not reported (HTTP {response.status_code}); platform defaults apply")
        except (httpx.HTTPError, ValueError) as exc:
            click.echo(f"spend caps         : could not read ({exc})")
        if wanted is not None:
            problems += await _report_balances(address, chain_name, wanted)
            await _report_api_features(client, address, wanted)
    finally:
        await client.aclose()
    return problems


USDC_BY_CHAIN = {
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    84532: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}
APPROVE_ABI = [
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    }
]


async def probe_api_features(client: KeeperHubClient, address: str, chain_id: int) -> dict:
    """Whether this KeeperHub takes raw calldata and sequence dry runs (both contributed upstream).

    Probes with a zero-amount USDC approve to the org wallet itself, simulate only, so nothing
    is signed. Each answer latches on the client the way a real call would.
    """
    from datetime import UTC, datetime

    from almanak_keeperhub.client import ContractCall

    usdc = USDC_BY_CHAIN.get(chain_id)
    result: dict = {
        "probed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": str(client._http.base_url),
        "chain_id": chain_id,
        "raw_calldata": None,
        "call_sequence": None,
    }
    if usdc is None:
        result["error"] = "no known USDC on this chain to probe with"
        return result
    data = "0x095ea7b3" + address[2:].lower().rjust(64, "0") + "0" * 64
    call = ContractCall(usdc, chain_id, "approve", [address, "0"], APPROVE_ABI, data=data)
    try:
        await client.simulate_contract_call(call)
        result["raw_calldata"] = client.capabilities.raw_calldata
        await client.simulate_call_sequence([call, call])
        result["call_sequence"] = client.capabilities.call_sequence
    except Exception as exc:  # noqa: BLE001 - a probe reports, never crashes
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def _report_api_features(client: KeeperHubClient, address: str, chain_id: int) -> None:
    features = await probe_api_features(client, address, chain_id)
    if features.get("error"):
        click.echo(f"api features       : probe failed ({features['error']})")
        return
    raw = "yes" if features["raw_calldata"] else "no (typed calls sent instead)"
    sequence = "yes" if features["call_sequence"] else "no (first call only, as before)"
    click.echo(f"raw calldata       : {raw}")
    click.echo(f"sequence dry run   : {sequence}")


@main.command("api-features")
@click.option("--chain", "chain_name", default="base", show_default=True)
@click.option("--out", "out_path", default=None, help="Also write the answer as JSON to this file.")
def api_features(chain_name: str, out_path: str | None) -> None:
    """Ask the live KeeperHub whether it accepts raw calldata and sequence dry runs.

    Both were contributed upstream by this project; this records whether the deployment
    this client talks to has them yet. The proof workflow runs it on every tick.
    """
    from almanak.core.chains import ChainRegistry

    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set")
    descriptor = ChainRegistry.try_resolve(chain_name)
    if descriptor is None:
        raise click.ClickException(f"'{chain_name}' is unknown to almanak")

    async def run() -> dict:
        client = KeeperHubClient(api_key=api_key, base_url=os.environ.get("KEEPERHUB_BASE_URL", DEFAULT_BASE_URL))
        try:
            return await probe_api_features(client, await client.wallet_address(), descriptor.chain_id)
        finally:
            await client.aclose()

    features = asyncio.run(run())
    click.echo(json.dumps(features, indent=1))
    if out_path:
        Path(out_path).write_text(json.dumps(features, indent=1) + "\n")


async def _report_balances(address: str, chain_name: str, chain_id: int) -> int:
    """Native and USDC balance of the org wallet, read from the RPC the strategy will use."""
    rpc_url = (
        os.environ.get(f"ALMANAK_{chain_name.upper()}_RPC_URL")
        or os.environ.get(f"{chain_name.upper()}_RPC_URL")
        or os.environ.get("RPC_URL_BASE" if chain_id == 8453 else "RPC_URL")
    )
    if not rpc_url:
        click.echo("balances           : skipped (set ALMANAK_<CHAIN>_RPC_URL to read them)")
        return 0
    try:
        from web3 import AsyncHTTPProvider, AsyncWeb3

        web3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        checksum = web3.to_checksum_address(address)
        native = await web3.eth.get_balance(checksum)
        line = f"balances           : native {native / 10**18:.6f}"
        usdc = USDC_BY_CHAIN_ID.get(chain_id)
        if usdc:
            erc20 = web3.eth.contract(
                address=web3.to_checksum_address(usdc),
                abi=[
                    {
                        "type": "function",
                        "name": "balanceOf",
                        "stateMutability": "view",
                        "inputs": [{"name": "a", "type": "address"}],
                        "outputs": [{"name": "", "type": "uint256"}],
                    }
                ],
            )
            balance = await erc20.functions.balanceOf(checksum).call()
            line += f", USDC {balance / 10**6:.2f}"
        click.echo(line)
        if native == 0:
            click.echo(
                "                     native balance is 0: fund the org wallet or rely on KeeperHub gas sponsorship"
            )
    except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
        click.echo(f"balances           : could not read from {rpc_url} ({exc})")
    return 0


def _chain_from_config(working_dir: Path, config_file: str | None) -> str | None:
    path = Path(config_file) if config_file else working_dir / "config.json"
    try:
        chain = json.loads(path.read_text()).get("chain")
    except (OSError, ValueError):
        return None
    return str(chain) if chain else None


if __name__ == "__main__":  # pragma: no cover
    main()
