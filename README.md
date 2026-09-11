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

almanak-keeperhub doctor --chain base           # key, org wallet + balances, chain, selector index
cd demos/metamorpho_base_yield
almanak-keeperhub run --once --dry-run          # Almanak plans; nothing reaches the gateway
almanak-keeperhub run --once --simulate-only    # KeeperHub dry-runs the compiled bundle; nothing broadcast
almanak-keeperhub run --once                    # 5 USDC into the Moonwell Flagship USDC vault via KeeperHub
```

Or run the whole sequence, failure modes included: `scripts/first_run.sh`.

Every run ends with a proof summary and appends to `keeperhub-receipts.json` in the strategy directory:

```
KeeperHub executions this run (2), recorded in .../demos/metamorpho_base_yield/keeperhub-receipts.json:
  approve -> 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913  execution=b0c3...  status=completed verified=True
    tx 0x7970a839...  https://basescan.org/tx/0x7970a839...
  deposit -> 0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca  execution=2e3b...  status=completed verified=True
    tx 0xee489263...  https://basescan.org/tx/0xee489263...
```

`--simulate-only` is the orchestrator-level dry run: Almanak compiles the exact bundle, KeeperHub simulates it, the signer prepares it, and the pipeline stops before submission. It runs against a throwaway state store, so it leaves no footprint on the strategy (Almanak strategies move their own state optimistically when they emit an intent).

Almanak's own agent CLI runs through the same backend. `almanak ax` compiles an agent's decision (structured or natural language) into transactions and executes them through the gateway, so with the KeeperHub servicer installed the agent decides and KeeperHub executes:

```bash
almanak-keeperhub ax --chain base swap USDC WETH 1 --dry-run     # agent plans, KeeperHub simulates, nothing sent
almanak-keeperhub ax --chain base swap USDC WETH 1 --yes         # Uniswap v3 exactInputSingle through KeeperHub
almanak-keeperhub ax --chain base -n "swap 1 USDC to WETH"       # natural language; needs AGENT_LLM_API_KEY
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
| `almanak_keeperhub/cli.py` | `almanak-keeperhub run`, `ax` and `doctor` |
| `patches/` | The upstream proposal for Almanak (same change, without the subclass) |

Idempotency key: `sha256(v2 | chain_id | from | to | data | value | almanak intent id)`. Almanak assigns a fresh nonce on every attempt, so the nonce is deliberately not part of the key: a retry of the same intent reproduces it and KeeperHub replays the first execution. The intent id (the execution context's correlation id) separates two intents that compile to identical calldata within KeeperHub's 24-hour replay window. Set `ALMANAK_KEEPERHUB_IDEMPOTENCY_SALT` for a deliberate repeat outside an orchestrated run.

## KeeperHub surfaces used

| Surface | Used | How |
|---|---|---|
| Direct execution REST | yes | `POST /api/execute/contract-call` with `simulate: true`, then with `Idempotency-Key`; `GET /api/execute/{id}/status` |
| Audit trail | yes | every execution id, hash, verified flag and link recorded in `keeperhub-receipts.json`, printed at the end of each run, plus the Runs page in the app |
| Agent-authored execution | partly | Almanak's `ax` agent (structured or natural language) decides; KeeperHub executes the compiled transactions |
| MCP | no | Almanak's execution layer is Python inside a gRPC gateway; the REST surface is the right one there. The bounty adds a `data` input to the same endpoint the MCP tool wraps |
| CLI (`kh`) | no | not needed by the integration |
| x402 / MPP | no, deliberately | this executes a framework's own transactions; nothing here is sold per call |

## What we got wrong first

The first idempotency key included the nonce Almanak assigns to a transaction. An independent review showed Almanak assigns that nonce per attempt, so a genuine retry after a landed transaction would have produced a new key, which is the exact failure the README claims to prevent. The key is now the intent id plus the transaction fields (`almanak_keeperhub/signer.py`), and the duplicate demo proves it by changing the nonce on the retry. The same review found a truncated bundle could read as success and that a transport error rotated nothing but still halted the strategy; both fixed, all in `git log`.

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

`demos/metamorpho_base_yield/keeperhub-receipts.json` is written by the strategy run and `docs/receipts.json` by the failure-mode demos: execution ids, hashes, verified flags and explorer links from app.keeperhub.com.

`scripts/benchmark.py` measures the backend the way judges compare it: impossible deposits refused before broadcast, valid dry runs, real approvals landed and verified, retry replayed instead of resent, p50 and p95 latency. It writes `docs/benchmark.md`.

Mainnet proof links: **to be added after the first hosted run** (see "What still breaks").

`docs/rehearsal-fork.md` is the log of the same pipeline on an Anvil fork of Base against a local stand-in for KeeperHub (`tests/e2e/fake_keeperhub.py`, which mirrors the documented API shapes). It proves the wiring; it is not execution through KeeperHub.

## Try it without a KeeperHub account

`tests/e2e/rehearsal.sh` starts an Anvil fork of Base and a local stand-in for the KeeperHub API (`tests/e2e/fake_keeperhub.py`, documented request and response shapes, signs with a throwaway Anvil key), then runs doctor, the failure modes, a simulate-only tick and a real tick of the unmodified demo strategy, and asserts the vault deposit landed on the fork. Needs foundry and a Base RPC. It proves the wiring; it is not execution through KeeperHub.

## Tests

```bash
pytest -q                      # 84 unit tests: API shapes from the docs, decoder, adapters against Almanak's real interfaces
ruff check almanak_keeperhub tests
tests/e2e/rehearsal.sh         # fork + stand-in + unmodified demo strategy, asserts the vault deposit landed
```

## What still breaks or is unfinished

- KeeperHub has no raw-calldata write on EVM, so calldata is decoded against an offline selector index (339 signatures: Almanak's shipped ABIs plus a curated list). A connector whose selector is missing is refused, not guessed. Add the signature to `almanak_keeperhub/signatures.json`.
- KeeperHub simulate cannot chain calls, so only the first transaction of a bundle is dry-run against live state; later ones use Almanak's compiler gas limit, exactly like Almanak's own `LocalSimulator`. A revert in a dependent transaction is caught at broadcast, with the hash.
- Almanak's Safe plus Zodiac Roles deployment mode is not covered; this runs Almanak's EOA mode with KeeperHub's org wallet as the EOA.
- Multi-transaction bundles are submitted one at a time with confirmation in between, slower than Almanak's parallel public submitter.
- Two Almanak bugs needed workarounds inside `gateway.py` (see `docs/almanak-feedback.md`): the in-process gateway deadlocks for 30 s during `RegisterChains` when any wallet registry plugin is installed, and the strategy runner never enables the orchestrator's simulate phase on live networks. Both are contained and documented.
- The idempotency key protects a retry of the same Almanak intent. A strategy that crashes before persisting its state and then decides again compiles a new intent, which is new work by construction; KeeperHub cannot tell those apart, and neither can this package.
- Sponsored KeeperHub transactions show the relayer as sender on the explorer; the vault's `Deposit` event `owner` and the `receipts[].verified` flag identify the org wallet.
- Tuple arguments are rendered as objects keyed by component name, the shape KeeperHub's own argument reshaping expects (read from its source); exercised by the Uniswap v3 swap in the rehearsal, not yet against the hosted app.
- Almanak's GitHub repository is a one-way mirror of a private monorepo with no pull requests, so the upstream change is offered as a patch and a filed issue, not a merged PR.

## Feedback to KeeperHub and Almanak

`docs/keeperhub-feedback.md` and `docs/almanak-feedback.md`, each item with a repro.

## License

MIT. `demos/metamorpho_base_yield/` is Almanak's demo strategy, Apache-2.0, copyright Almanak.
