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
    entries = log.entries_since(started_iso)
    click.echo("")
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
