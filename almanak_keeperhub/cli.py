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
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}


@click.group()
@click.version_option(__version__)
def main() -> None:
    """KeeperHub execution backend for Almanak."""


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
    from almanak_keeperhub.verify import actor_evidence

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
def console(receipts: str | None, docs_dir: str | None, port: int, chain_name: str, open_browser: bool) -> None:
    """Serve the execution console: executions, verdicts, failure modes and the benchmark, live.

    Keep it open in a browser while `run`, `ax` or the demos execute in a terminal; it re-reads
    the proof files every two seconds. Inspect asks KeeperHub and the chain for the evidence.
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
    finally:
        await client.aclose()
    return problems


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
