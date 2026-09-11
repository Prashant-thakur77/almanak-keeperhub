"""``almanak-keeperhub``: run Almanak strategies with KeeperHub as the execution backend.

    almanak-keeperhub doctor                      # check key, wallet, chain, selector index
    almanak-keeperhub run --once --dry-run        # same flags as `almanak strat run`
    almanak-keeperhub run --once                  # broadcast through KeeperHub

``run`` installs the KeeperHub gateway servicer, points Almanak's wallet
registry at the KeeperHub organization wallet, then hands over to Almanak's
own ``strat run`` command unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
import httpx

from almanak_keeperhub import __version__
from almanak_keeperhub.calldata import SelectorIndex
from almanak_keeperhub.client import DEFAULT_BASE_URL, KeeperHubClient
from almanak_keeperhub.wallets import KIND


@click.group()
@click.version_option(__version__)
def main() -> None:
    """KeeperHub execution backend for Almanak."""


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option("--chain", "chain_override", default=None, help="Chain name; defaults to config.json 'chain'.")
@click.option("--working-dir", "-d", default=".", help="Strategy directory (passed through to almanak).")
@click.option("--config", "-c", "config_file", default=None, help="Strategy config JSON (passed through).")
@click.argument("almanak_args", nargs=-1, type=click.UNPROCESSED)
def run(chain_override: str | None, working_dir: str, config_file: str | None, almanak_args: tuple[str, ...]) -> None:
    """Run `almanak strat run ...` through KeeperHub."""
    api_key = os.environ.get("KEEPERHUB_API_KEY")
    if not api_key:
        raise click.ClickException("KEEPERHUB_API_KEY is not set (organization API key with mcp:write scope)")
    chain = chain_override or _chain_from_config(Path(working_dir), config_file)
    if not chain:
        raise click.ClickException("could not determine the chain: pass --chain or put 'chain' in config.json")

    os.environ.setdefault("ALMANAK_GATEWAY_WALLETS", json.dumps({chain: {"kind": KIND}}))
    # No local key: the KeeperHub organization wallet signs inside Turnkey.
    for var in ("ALMANAK_PRIVATE_KEY", "PRIVATE_KEY"):
        if os.environ.pop(var, None):
            click.echo(f"ignoring {var}: KeeperHub signs, no local key is used", err=True)

    from almanak_keeperhub.gateway import install

    install()
    click.echo(f"almanak-keeperhub {__version__}: chain={chain} backend=keeperhub ({os.environ.get('KEEPERHUB_BASE_URL', DEFAULT_BASE_URL)})")

    from almanak.cli import almanak

    args = ["strat", "run", "--working-dir", working_dir]
    if config_file:
        args += ["--config", config_file]
    args += list(almanak_args)
    sys.exit(almanak.main(args=args, prog_name="almanak", standalone_mode=True))


@main.command()
@click.option("--chain", "chain_name", default="base", show_default=True)
def doctor(chain_name: str) -> None:
    """Check the KeeperHub key, org wallet, chain support and the calldata index."""
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
        try:
            response = await client._http.get("/api/chains")
            chains = response.json()
            rows = chains.get("chains", chains) if isinstance(chains, dict) else chains
            from almanak.core.chains import ChainRegistry

            descriptor = ChainRegistry.try_resolve(chain_name)
            wanted = descriptor.chain_id if descriptor else None
            match = next((c for c in rows if isinstance(c, dict) and int(c.get("chainId", c.get("id", 0)) or 0) == wanted), None)
            if match is None:
                click.echo(f"chain {chain_name:<12}: not enabled on KeeperHub")
                problems += 1
            else:
                click.echo(
                    f"chain {chain_name:<12}: enabled on KeeperHub (chainId {wanted}, testnet={match.get('isTestnet')})"
                )
        except (httpx.HTTPError, ValueError) as exc:
            click.echo(f"chains             : could not read /api/chains ({exc})")
    finally:
        await client.aclose()
    return problems


def _chain_from_config(working_dir: Path, config_file: str | None) -> str | None:
    path = Path(config_file) if config_file else working_dir / "config.json"
    try:
        chain = json.loads(path.read_text()).get("chain")
    except (OSError, ValueError):
        return None
    return str(chain) if chain else None


if __name__ == "__main__":  # pragma: no cover
    main()
