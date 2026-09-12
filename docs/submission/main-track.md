# Main track submission kit (DoraHacks, Best Integration into a Live Project)

Deadline: 18 Sep 2026, 12:00 CEST (15:30 IST). Submit by 17 Sep 23:00 IST.
Required: source link, demo video, a link to a transaction executed through KeeperHub.

## Title (max 8 words)

Almanak strategies, executed by KeeperHub

## Tagline

Almanak decides. KeeperHub lands it. No strategy code changes.

## Form answers

**Which project did you integrate with, and what does the integration do?**

Almanak, the open-source DeFi strategy framework (PyPI `almanak` 2.28, Apache-2.0, 46 protocol connectors, 20 packaged strategies). Almanak compiles a strategy's intent into transactions and hands them to three abstract classes: `Signer`, `Submitter` and `Simulator`. Its only shipped submitter is the public mempool; its private-relay submitter is a stub that rejects everything. I implemented all three against KeeperHub's Direct Execution API and wired them in through Almanak's own gateway plugin points (the `almanak.wallets` entry-point group and the execution servicer). Every Almanak strategy, unchanged, now dry-runs through KeeperHub, broadcasts with one idempotency key per piece of work, gets nonce and gas handled by KeeperHub, and receives a verified receipt back into Almanak's own receipt parsers. The demo runs Almanak's packaged `metamorpho_base_yield` strategy on Base: 5 USDC into the Moonwell Flagship USDC vault, two transactions (approve, deposit), with the strategy file byte-identical to the one in the package. Almanak's agent CLI (`almanak ax`, structured or natural language) runs through the same backend: the agent decides, KeeperHub executes the compiled Uniswap swap. The full lifecycle runs through KeeperHub (deposit, then an exit tick that redeems). And a scheduled KeeperHub workflow generated from the strategy config keeps small idle balances compounding with no Almanak process running; Almanak still decides entry, exit and anything above the keeper's window.

**Which KeeperHub surfaces did you use?**

Direct execution REST: `POST /api/execute/contract-call` with `simulate: true` for the dry run and with an `Idempotency-Key` for the broadcast, `GET /api/execute/{id}/status` honouring `X-Poll-Interval-Hint`, `GET /api/user` for the organization wallet. The audit trail: every execution id, hash, verified flag and link is recorded automatically in `keeperhub-receipts.json` next to the strategy, printed at the end of each run, shown live in the execution console, and available from a phone through the Telegram operator bot (status, executions, verify, a dry-run tick, a confirmed real tick). Agent-authored workflows: `almanak-keeperhub keeper deploy` generates a Schedule-triggered workflow from the strategy config and creates it through the workflows API, validated with KeeperHub's deep check; KeeperHub's scheduler runs it. Agent-authored execution in the Almanak sense too: its `ax` agent's decisions are executed by KeeperHub. Not used: MCP, CLI, x402, MPP, deliberately (this executes a framework's own transactions; nothing is sold per call). The bounty submission adds a raw-calldata input to the same endpoint.

**Testnet or mainnet?**

Both. Almanak ships no testnet chain (its sepolia mode keeps the mainnet chain id in compiled transactions), so this package registers `base_sepolia` as a first-class Almanak chain and the unmodified strategy runs on Base Sepolia through KeeperHub with sponsored gas against a dependency-free ERC-4626 test vault. The full lifecycle (exit tick) and the agent swap need mainnet venues, so those are rehearsed on a Base mainnet fork and remain mainnet-only. KeeperHub sponsors gas on Base, so the explorer shows the relayer as sender; the receipt's `verified` flag and the vault's `Deposit` event `owner` identify the organization wallet.

**What still breaks or is unfinished?**

- KeeperHub has no raw-calldata write on EVM, so I decode Almanak's calldata against an offline selector index (339 signatures: the ABIs shipped inside the `almanak` package plus a curated list) and refuse anything unknown. Connectors with a missing selector do not run yet; adding the signature is one line. The bounty PR adds the same decoding server-side.
- KeeperHub simulate cannot chain calls, so only the first transaction of a bundle is dry-run against live state; the rest use Almanak's compiler gas limit, which is also what Almanak's own simulator does. A revert in a later transaction is caught at broadcast with the hash.
- Two Almanak bugs needed contained workarounds: the in-process gateway deadlocks for 30 s in `RegisterChains` when any wallet registry plugin is installed, and the strategy runner never enables the orchestrator's simulate phase on live networks. Both are documented with stack traces in `docs/almanak-feedback.md` and wrapped in `gateway.py`.
- Almanak's Safe plus Zodiac Roles mode is not covered; the demo runs Almanak's EOA mode with the KeeperHub org wallet as the EOA.
- Bundles are submitted one transaction at a time with confirmation in between, slower than Almanak's parallel public submitter.
- Almanak's public repository is a one-way mirror of a private monorepo with no pull requests, so the upstream change is a patch file plus a filed issue rather than a merged PR.
- Tuple arguments are rendered as name-keyed objects (KeeperHub's own reshaping rule); exercised on a fork with a Uniswap v3 swap, not yet on the hosted app.
- Almanak's runner needs three testnet shims (a chain descriptor, token registry entries, the vault connector's chain set) because Almanak ships no testnet; they live in `testnet.py`, not in the strategy.

**Transaction executed through KeeperHub**

https://sepolia.basescan.org/tx/0x70b453be43f4b8c4d40837baa7bd6f16fa3cc909038a590978b831b6605a7a87 (KeeperHub execution `au5z8vtzv8s9xm811z93j`: the demo strategy's 5 USDC deposit on Base Sepolia, sponsored gas, verified receipt). Full list in the README's Proof section and in `keeperhub-receipts.json`.

**Contact**

Email: prashant101007@gmail.com. X / Discord: <fill in>.

## Demo video script (2:30)

Three panes: terminal left, `almanak-keeperhub console` right, phone with the Telegram bot in a corner. Everything on Base Sepolia through app.keeperhub.com; nothing costs anything.

- 0:00 Terminal in `demos/metamorpho_base_sepolia`. "This is Almanak's own packaged strategy. One line changed: it declares the testnet chain, because Almanak ships none." Show `diff ../metamorpho_base_yield/strategy.py strategy.py`: one line.
- 0:15 `almanak-keeperhub doctor --chain base_sepolia`: org wallet, chain enabled (testnet), spend caps, 339 selectors, result OK.
- 0:30 `almanak-keeperhub run --once --fresh --simulate-only`: `Wallet registry plugin loaded: KeeperHubWalletRegistry`, `KeeperHub execution backend active`, `KeeperHub simulate ok: ...approve gas=...`, `KeeperHub executions this run: none`. The console's Dry runs table gains a row; Executions stays empty. Say: "the exact compiled bundle, dry-run by KeeperHub, nothing touched the chain."
- 0:50 `almanak-keeperhub run --once --fresh`: `KeeperHub execution <id> broadcast tx 0x... (completed)` for approve and deposit, `Status: SUCCESS | Intent: VAULT_DEPOSIT`, the proof summary with `[sponsored]`. The phone buzzes: the bot posts the broadcast and settlement alerts. In the console click Inspect on the deposit: KeeperHub's verdict, the relayer as sender (sponsored gas), the Deposit event naming the org wallet.
- 1:15 `almanak-keeperhub keeper status` (or `/keeper` on the phone): the scheduled compounder KeeperHub created from the strategy config, validated by KeeperHub, enabled, running every six hours with no Almanak process. If it has fired by recording day, show its execution.
- 1:30 On the phone: `/demo cap`. KeeperHub refuses a 150 USDC transfer before anything is signed; the reply and the alert arrive. Then `/demo duplicate`: the same intent submitted twice, one transaction, second answer `replay=True`. Say: "this is the nonce incident from Almanak's own repo, prevented."
- 1:55 `python demos/failure_modes/crash_and_resume.py` in the terminal: the child dies after broadcast; a fresh process settles the hash from the receipts log with no second broadcast.
- 2:10 `docs/benchmark.md`: 20 of 20 impossible deposits refused before broadcast, 5 of 5 approvals landed and verified, median 6.9 s. Then `pytest -q` (120 passed) and the README's known gaps, including what stays mainnet-only and why.

## Live pitch: eight hard questions

1. Why not call KeeperHub's MCP from an Almanak agent? Because the intent would be reinterpreted at execution time. Hooking the Submitter means the exact compiled transaction is what KeeperHub simulates and sends.
2. What breaks without KeeperHub? Almanak falls back to the public mempool with no idempotency. The duplicate-mint incident in `nonce_recovery.py` is what that looks like.
3. How do you handle calldata? Decode against the ABIs Almanak ships, fail closed on unknown selectors, and the bounty PR moves that decoding into KeeperHub itself.
4. Nonce conflict between Almanak's counter and KeeperHub? KeeperHub owns the real nonce. Almanak assigns a fresh nonce per attempt, so the idempotency key deliberately excludes it: the key is the intent id plus the transaction fields, so a retry of the same intent replays and a new intent with identical calldata is new work.
5. The relayer is the sender on the explorer. How do you prove the org wallet acted? `receipts[].verified` plus the `Deposit` event's `owner` argument, both shown in the run log.
6. Multi-transaction bundles? Sequential with confirmation in between, and only the first is dry-run against live state. Same rule as Almanak's own LocalSimulator; documented as a gap. A crash between transactions is covered: the next process resumes from the receipts log and never resends.
7. What about the other Almanak entries? The public ones translate a handful of intents outside Almanak. This runs any Almanak strategy unchanged through Almanak's own execution interfaces and gateway plugin points, and I had to fix two Almanak bugs to get there.
8. What did you find broken? Two Almanak bugs with stack traces, and for KeeperHub: no raw calldata write (now a PR), no chained simulation, relayer-as-sender ambiguity. All in `docs/`.
