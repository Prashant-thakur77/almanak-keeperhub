# Roadmap

What this project does next, in the order it will be done, and why. Dates are targets, not promises;
each item lands as its own change with tests and a note in [CHANGELOG.md](../CHANGELOG.md).

## Where it stands (23 September 2026)

Any Almanak strategy, unchanged, executes through KeeperHub: dry run of the exact calldata, one idempotency
key per intent, enclave signing, verified receipts back in Almanak's own parsers. Around it: the CLI, the
Telegram operator bot, the execution console, the MCP server, and two KeeperHub workflows generated from the
strategy config (the scheduled compounder and the guarded exit with a Condition node). Proven on production
on Base Sepolia by a runner every six hours; four gaps filed upstream and built, three merged.

## Now (this week)

| Item | Why | Shape |
|---|---|---|
| The guard on every surface | The exit guard exists as check-and-execute and as a KeeperHub workflow, from the CLI and the phone. An agent over MCP should have the same two tools, with the same confirm and write split. | `guard_exit` and `guard_status` tools in `mcp_server.py`, mirroring `/guard` and `/guard stale`; tests with the recorded node statuses |
| The site as the explanation | The front page is where a judge or a new user lands. It should tell the problem, the fix and the proof in one scroll, with the live numbers animated rather than listed. | `site.html` redesigned: dark hero with the animated pipeline and live counters, the problem in three cards, the guard animated, the proof sections restyled; same data bindings, no build step |
| Publish 1.1.0 to PyPI | The release exists on GitHub with the wheel; PyPI still serves 1.0.0. | `uv publish dist/*` with a fresh token, then revoke it |
| Rotate every secret that passed through a chat | Hygiene after the hackathon. | KeeperHub API key, the Telegram bot token, the PyPI token |

## Next (October)

| Item | Why | Shape |
|---|---|---|
| Whole strategies as KeeperHub workflows | The compounder and the exit guard are the first two node graphs generated from a strategy. The next step is the full tick: read balances and rates, decide with Condition nodes, approve, deposit, exit, all in the builder, run by KeeperHub's scheduler, with nothing to host. | `almanak-keeperhub workflow compile` from `config.json` and the strategy's declared thresholds; deploy, validate with deepCheck, enable; runs listed on the console next to the other two |
| Preflight against carried state | KeeperHub PR #2531 dry-runs each write node against the state the earlier ones produce. When it lands, `compile` uses it before enabling anything, so a graph that reverts on its second node never gets scheduled. | wait for #2531 to merge; then `deploy` calls validate with the sequence preflight and refuses to enable on a failing node |
| Notify from inside the workflow | The bot alerts from the backend today. KeeperHub's own Telegram node (free tier) can send the redeem receipt, or the "stopped at the Condition" verdict, from the workflow itself, with no Almanak process. | a notify node on both branches of the exit guard; credential configured in the app, referenced by the generated graph |
| Auto-pause on anomalies | An operator wants the system to stop itself: three consecutive refusals, a verify mismatch, or a balance that moved outside the strategy should pause the compounder and say so. | `almanak-keeperhub watch`: reads the receipts log, disables the workflow through `PATCH enabled`, posts the reason; runs in the proof runner too |
| Audit report | The console shows evidence per row; a reviewer also wants one page that says every recorded execution was re-checked against KeeperHub and the chain, with any mismatch named. | `almanak-keeperhub verify --all` writing `docs/audit.md`, run by the proof runner; a badge from its result |

## Later

| Item | Why | Shape |
|---|---|---|
| Mainnet | The code path is identical; only funding was withheld. The first mainnet run is the same demo strategy with the Moonwell vault, sponsored where KeeperHub sponsors, and the console gains a chain switch. | fund the org wallet, `doctor --chain base`, the runner on a second schedule |
| Almanak's Safe plus Zodiac Roles mode | Teams that run Almanak behind a Safe want the roles to decide what an agent may call and KeeperHub to decide whether it lands. | a `keeperhub-safe` wallet kind; the roles modifier as the target, KeeperHub as the executor |
| A second chain on the free path | The chain switch exists (`ALMANAK_KEEPERHUB_CHAIN`); a second testnet proves nothing is Base-specific. | deploy the TestVault on Arbitrum Sepolia or Ethereum Sepolia, add the chain to `testnet.py`, run the rehearsal there |
| Agents as operators, by default | The MCP server serves the strategy with the read/write split. The next step is the guard as the default shape of every agent write: an agent asks for an outcome, the package turns it into a check-and-execute or a workflow with a Condition, never a bare write. | `guarded_write` tool; policy for which reads guard which writes |
| The Almanak side | When `almanak.execution_backends` (almanak-co/sdk#3) lands, the gateway subclass goes away and a registry entry of kind `keeperhub` is the whole integration. | delete `gateway.py`'s override once their release ships it |

## Not planned

- Selling execution per call (x402 / MPP): this executes a framework's own transactions; nothing here is a
  paid endpoint.
- A hosted service: the package runs where the strategy runs; KeeperHub is the hosted part.
- Reimplementing Almanak's strategy logic in KeeperHub nodes beyond what the strategy config declares; the
  strategy stays the strategy.
