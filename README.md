# almanak-keeperhub

Almanak decides. KeeperHub lands it. No strategy code changes.

[Almanak](https://github.com/almanak-co/sdk) is an open-source DeFi strategy framework (PyPI `almanak`, Apache-2.0, 46 protocol connectors). Its execution layer is built around three abstract classes, `Signer`, `Submitter` and `Simulator`, so that "multiple signing backends and submission methods" can be plugged in (`almanak/framework/execution/interfaces.py`). Today the only submitter that ships is the public mempool, and the private-relay submitter is a stub that rejects every transaction.

This package implements all three against [KeeperHub](https://keeperhub.com)'s Direct Execution API. Every Almanak strategy, unchanged, then runs like this:

1. Almanak compiles the strategy's intent into transactions (approve, deposit, swap...).
2. `KeeperHubSimulator` dry-runs the bundle through KeeperHub (`simulate: true`). A revert stops the tick before anything is signed.
3. `KeeperHubSigner` decodes each transaction's calldata into the function call KeeperHub executes and derives one idempotency key per piece of work. No private key exists on the machine; KeeperHub's Turnkey wallet signs.
4. `KeeperHubSubmitter` broadcasts through KeeperHub with that key, waits for the verified receipt, and hands Almanak a normal `TransactionReceipt` with logs, so Almanak's own receipt parsers and position tracking keep working.

## The incident this prevents

Almanak's repository documents it (`almanak/framework/execution/nonce_recovery.py`):

> Arbitrum mainnet 2026-05-18, lp_triple strategy. LP_OPEN C's mint at nonce 1635 actually landed on-chain, but the framework raised NONCE_ERROR on a follow-up read, retried, minted a duplicate dust position, and teardown closed only the tracked positions. NFT 5495063 became a zombie position the framework didn't track. Manual recovery via direct cast calls.

With KeeperHub in the loop the retry carries the same idempotency key, so KeeperHub replays the first execution instead of broadcasting again. `demos/failure_modes/duplicate_blocked_by_idempotency.py` reproduces the "landed but retried" sequence on purpose.

## Run it in five minutes

```bash
git clone <this repo> && cd almanak-keeperhub
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env            # KEEPERHUB_API_KEY (mcp:write), Base RPC URL
set -a; source .env; set +a

almanak-keeperhub doctor --chain base        # key, org wallet, chain, selector index
cd demos/metamorpho_base_yield
almanak-keeperhub run --once --dry-run       # Almanak plans, nothing is broadcast
almanak-keeperhub run --once                 # 5 USDC into the Moonwell Flagship USDC vault via KeeperHub
```

The demo strategy in `demos/metamorpho_base_yield/` is Almanak's own packaged demo, copied unmodified from the `almanak` package (Apache-2.0). Only `config.json` differs: the deposit is 5 USDC instead of 50. Fund the KeeperHub organization wallet with at least 6 USDC and a little ETH on Base first.

`almanak-keeperhub run` accepts every `almanak strat run` flag. It sets `ALMANAK_GATEWAY_WALLETS` for the strategy's chain, installs the KeeperHub gateway servicer, and hands over to Almanak's own `strat run`.

## Where it plugs in

```
almanak strat run (unchanged)
  │  gRPC
  ▼
Almanak gateway (in-process)  ── wallet registry plugin: almanak.wallets entry point
  ExecutionServiceServicer      ── kind "keeperhub" → KeeperHubSigner (address = org wallet)
    └─ ExecutionOrchestrator    ── submitter → KeeperHubSubmitter, simulator → KeeperHubSimulator
         build → validate → SIMULATE → sign → SUBMIT → CONFIRM → parse receipts
                              │                 │           │
                              ▼                 ▼           ▼
                   POST /api/execute/contract-call   GET /api/execute/{id}/status
                   {simulate:true}   Idempotency-Key      receipts[].verified
```

Files:

| File | Role |
|---|---|
| `almanak_keeperhub/client.py` | Direct Execution API client: simulate, execute with `Idempotency-Key`, status polling honouring `X-Poll-Interval-Hint`, typed errors |
| `almanak_keeperhub/calldata.py` | Offline selector index (ABIs shipped in the `almanak` package plus `signatures.json`). Unknown selectors are refused, never guessed |
| `almanak_keeperhub/signer.py` | `Signer`: decode calldata, derive the idempotency key, no local key |
| `almanak_keeperhub/submitter.py` | `Submitter`: sequential broadcast, confirmation between dependent transactions, receipts with logs |
| `almanak_keeperhub/simulator.py` | `Simulator`: KeeperHub dry run of the first transaction, compiler gas for dependent ones (same rule as Almanak's own simulator) |
| `almanak_keeperhub/wallets.py` | Almanak `almanak.wallets` registry plugin resolving every chain to the KeeperHub org wallet |
| `almanak_keeperhub/gateway.py` | Subclass of Almanak's execution servicer that swaps the three interfaces; `install()` |
| `almanak_keeperhub/cli.py` | `almanak-keeperhub run` and `doctor` |
| `patches/` | The upstream proposal for Almanak (same change, without the subclass) |

Idempotency key: `sha256(v1 | chain_id | from | to | data | value | almanak nonce)`. A retry of the same compiled transaction reproduces it; different work changes it. Set `ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT` when you deliberately repeat identical work within KeeperHub's 24-hour replay window.

## Failure modes, on purpose

Each script uses the same signer, simulator and submitter the gateway uses.

| Script | What happens |
|---|---|
| `demos/failure_modes/revert_caught_by_dry_run.py` | Deposit far above balance: KeeperHub simulate answers `wouldRevert`, Almanak stops at SIMULATION, zero broadcasts |
| `demos/failure_modes/duplicate_blocked_by_idempotency.py` | The same compiled approve submitted twice: same execution id, same hash, `idempotentReplay: true`, one transaction on chain |
| `demos/failure_modes/cap_refused.py` | 150 USDC transfer: refused by KeeperHub's 100 USD per-transaction stablecoin cap, `submitted=False`, nothing signed |
| `demos/failure_modes/rpc_outage.py` | Dead local RPC: the broadcast still lands through KeeperHub's RPC pool; only the local log fetch fails, and says so |
| `demos/failure_modes/unknown_selector_refused.py` | Calldata with an unknown selector is refused at SIGNING, offline |

## Proof

`docs/receipts.json` is appended by the demos and the strategy run with execution ids, hashes and explorer links from app.keeperhub.com.

Mainnet proof links: **to be added after the first hosted run** (see "What still breaks").

`docs/rehearsal-fork.md` is the log of the same pipeline on an Anvil fork of Base against a local stand-in for KeeperHub (`tests/e2e/fake_keeperhub.py`, which mirrors the documented API shapes). It proves the wiring; it is not execution through KeeperHub.

## Tests

```bash
pytest -q                      # 62 unit tests: API shapes from the docs, decoder, adapters against Almanak's real interfaces
ruff check almanak_keeperhub tests
tests/e2e/rehearsal.sh         # fork + stand-in + unmodified demo strategy, asserts the vault deposit landed
```

## What still breaks or is unfinished

- KeeperHub has no raw-calldata write on EVM, so calldata is decoded against an offline selector index (339 signatures: Almanak's shipped ABIs plus a curated list). A connector whose selector is missing is refused, not guessed. Add the signature to `almanak_keeperhub/signatures.json`.
- KeeperHub simulate cannot chain calls, so only the first transaction of a bundle is dry-run against live state; later ones use Almanak's compiler gas limit, exactly like Almanak's own `LocalSimulator`. A revert in a dependent transaction is caught at broadcast, with the hash.
- Almanak's Safe plus Zodiac Roles deployment mode is not covered; this runs Almanak's EOA mode with KeeperHub's org wallet as the EOA.
- Multi-transaction bundles are submitted one at a time with confirmation in between, slower than Almanak's parallel public submitter.
- Two Almanak bugs needed workarounds inside `gateway.py` (see `docs/almanak-feedback.md`): the in-process gateway deadlocks for 30 s during `RegisterChains` when any wallet registry plugin is installed, and the strategy runner never enables the orchestrator's simulate phase on live networks. Both are contained and documented.
- Sponsored KeeperHub transactions show the relayer as sender on the explorer; the vault's `Deposit` event `owner` and the `receipts[].verified` flag identify the org wallet.
- Tuple arguments are passed to KeeperHub as nested JSON arrays; tested against the decoder, not yet against the hosted app.
- Almanak's GitHub repository is a one-way mirror of a private monorepo with no pull requests, so the upstream change is offered as a patch and a filed issue, not a merged PR.

## Feedback to KeeperHub and Almanak

`docs/keeperhub-feedback.md` and `docs/almanak-feedback.md`, each item with a repro.

## License

MIT. `demos/metamorpho_base_yield/` is Almanak's demo strategy, Apache-2.0, copyright Almanak.
