# Main track submission kit (DoraHacks, Best Integration into a Live Project)

Deadline: 18 Sep 2026, 12:00 CEST (15:30 IST). Submit by 17 Sep 23:00 IST.
Required: source link, demo video, a link to a transaction executed through KeeperHub.

## Title (max 8 words)

Almanak strategies, executed by KeeperHub

## Tagline

Almanak decides. KeeperHub lands it. No strategy code changes.

## Form answers

**Which project did you integrate with, and what does the integration do?**

Almanak, the open-source DeFi strategy framework (PyPI `almanak` 2.28, Apache-2.0, 46 protocol connectors, 20 packaged strategies). Almanak compiles a strategy's intent into transactions and hands them to three abstract classes: `Signer`, `Submitter` and `Simulator`. Its only shipped submitter is the public mempool; its private-relay submitter is a stub that rejects everything. I implemented all three against KeeperHub's Direct Execution API and wired them in through Almanak's own gateway plugin points (the `almanak.wallets` entry-point group and the execution servicer). Every Almanak strategy, unchanged, now dry-runs through KeeperHub, broadcasts with one idempotency key per piece of work, gets nonce and gas handled by KeeperHub, and receives a verified receipt back into Almanak's own receipt parsers. The demo runs Almanak's packaged `metamorpho_base_yield` strategy, byte-identical to the one in the package except for its chain list, on Base Sepolia through the hosted app: approve and deposit into an ERC-4626 vault, then the exit as a KeeperHub-guarded redeem. Almanak's agent CLI (`almanak ax`) runs through the same backend on a fork: the agent decides, KeeperHub executes the compiled swap. A scheduled KeeperHub workflow generated from the strategy config compounds idle balances with no Almanak process running. In numbers, all from KeeperHub execution records: 200 of 200 real executions landed and verified, 50 of 50 impossible deposits refused before broadcast, 50 of 50 retries of landed work replayed by idempotency key with zero double broadcasts, 10 of 10 processes killed after broadcast and settled by a fresh process, seven deliberate failure modes, and a public console over every execution this project ever produced, refreshed by a workflow that runs a real tick every six hours. Along the way three gaps in KeeperHub were filed as issues, accepted, built to the maintainers' spec and two are merged; the client here already uses both.

**Which KeeperHub surfaces did you use?**

Direct execution REST: `POST /api/execute/contract-call` with `simulate: true` for the dry run and with an `Idempotency-Key` for the broadcast, `GET /api/execute/{id}/status` honouring `X-Poll-Interval-Hint`, `GET /api/user` for the organization wallet. Check and execute: the vault exit goes out as `POST /api/execute/check-and-execute`, so KeeperHub reads the position itself right before the redeem and answers `executed: false` to a stale decision instead of broadcasting a revert. Agent-authored workflows: `almanak-keeperhub keeper deploy` generates a Schedule-triggered workflow from the strategy config and creates it through the workflows API; KeeperHub's scheduler ran it with no Almanak process. The audit trail: every execution id, hash, verified flag and link is recorded automatically next to the strategy, published on a static console (GitHub Pages) with KeeperHub's verdict frozen per row, and reachable from a phone through the Telegram bot. MCP, the other way round: `almanak-keeperhub mcp` serves the strategy's proof and controls to any MCP client with the same read/write split as KeeperHub's own keys; three real Claude sessions over it are recorded unedited in `docs/agent-session.md`. And the two features this project contributed to KeeperHub itself, raw calldata on `contract-call` (#2449, merged) and sequence dry runs (#2452, merged): the client already sends both shapes and latches to the old ones from the API's own answer, so the day production deploys them the fallbacks stop by themselves; `almanak-keeperhub api-features` records which are live and the console footer shows it. Not used: x402 and MPP, deliberately; this executes a framework's own transactions and nothing is sold per call.

**Testnet or mainnet?**

Testnet, and the proof is live rather than a snapshot. Almanak ships no testnet chain (its sepolia mode keeps the mainnet chain id in compiled transactions), so this package registers `base_sepolia` as a first-class Almanak chain and the unmodified strategy runs on Base Sepolia through KeeperHub with sponsored gas against a dependency-free ERC-4626 test vault. A GitHub workflow runs a full lifecycle every six hours on a bare runner (Almanak plans, KeeperHub dry-runs, approve and deposit land, the guarded exit redeems), runs an 11-test conformance suite against production, and commits the result, so the console judges open carries executions from that day made by a machine. Mainnet venues (the rate-driven exit, the agent swap) are rehearsed on a Base mainnet fork. KeeperHub sponsors gas on Base Sepolia through Turnkey's Gas Station, so the explorer shows the paymaster as sender; the receipt's `verified` flag, the vault's `Deposit` event `owner`, and `executedCall.from` (PR #2450, the third upstream change) identify the organization wallet.

**What still breaks or is unfinished?**

- Production KeeperHub has not yet deployed the two merged changes, so today the client's raw-calldata and sequence paths fall back to the typed single-call shapes on every run. The fallbacks are the pre-existing behaviour, `doctor` reports which path is live, and nothing needs changing when the deployment catches up.
- Two Almanak bugs in the 2.28.0 release need contained workarounds in `gateway.py` (a 30 s gateway deadlock during `RegisterChains` with any wallet registry plugin installed; the simulate phase never enabled on live networks). Both are fixed on Almanak's `main` since 9 Sep and not yet released. The third finding, a pluggable execution backend, is still open and filed as almanak-co/sdk#3 with a diff that applies to their `main`.
- Almanak's Safe plus Zodiac Roles mode is not covered; the demo runs Almanak's EOA mode with the KeeperHub org wallet as the EOA.
- Bundles are submitted one transaction at a time with confirmation in between, slower than Almanak's parallel public submitter, and they are not atomic; the sequence dry run says so.
- A strategy that crashes before persisting its state and decides again produces a new intent id, which is new work by construction; neither KeeperHub nor this package can tell that apart from a genuinely new decision.
- Almanak's public repository is a one-way mirror of a private monorepo with no pull requests, so the upstream change is an issue carrying a diff rather than a merged PR.
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

## Live pitch: twelve hard questions

1. Why not call KeeperHub's MCP from an Almanak agent? Because the intent would be reinterpreted at execution time. Hooking the Submitter means the exact compiled transaction is what KeeperHub simulates and sends. The other direction exists too: `almanak-keeperhub mcp` serves the strategy to any agent, with the same read/write split as KeeperHub's own keys.
2. What breaks without KeeperHub? Almanak falls back to the public mempool with no idempotency. The duplicate-mint incident in `nonce_recovery.py` is what that looks like, and KeeperHub issue #2374, filed by another builder during this hackathon, is the same failure from the other side. Measured here: 50 of 50 retries of landed work replayed, 10 of 10 crashed processes resumed, zero double broadcasts.
3. How do you handle calldata? Send it raw to KeeperHub and let it decode against the verified ABI, which is the change I got merged (#2449). Until production deploys it, the client falls back to decoding against the ABIs Almanak ships and refuses unknown selectors; `doctor` and the console footer say which path is live.
4. Nonce conflict between Almanak's counter and KeeperHub? KeeperHub owns the real nonce. Almanak assigns a fresh nonce per attempt, so the idempotency key deliberately excludes it: the key is the intent id plus the transaction fields, so a retry of the same intent replays and a new intent with identical calldata is new work. I got this wrong once for the guarded exit, where a key made of (vault, wallet, shares) replayed an old redeem; the first scheduled run caught it and the key now carries the decision id.
5. The paymaster is the sender on the explorer. How do you prove the org wallet acted? Three ways: `receipts[].verified`, the `Deposit` event's `owner`, and `executedCall.from` from my third PR (#2450), which names the wallet whose call frame hit the target. On Base Sepolia the sponsored transaction goes paymaster -> Turnkey Gas Station -> org EOA under EIP-7702 -> target; I traced it, and the trace is in the PR.
6. Multi-transaction bundles? Dry-run as one sequence, each call against the state the previous one produced, through `calls[]` in #2452 (merged); before that only the first call could be simulated against live state. Broadcast stays sequential, one transaction per request, non-atomic, and the response says so. A crash between transactions is covered: the next process resumes from the receipts log and never resends.
7. What is the guarded exit? The redeem goes out as KeeperHub `check-and-execute`: KeeperHub reads `balanceOf(wallet)` itself right before the write and only redeems if it still covers the request. A stale decision comes back `executed: false` with the observed balance. Both cases are on the console and in the video, from a phone.
8. How do I know the proof is current? A GitHub workflow runs a full lifecycle through KeeperHub every six hours on a bare runner, runs eleven conformance tests against production, and commits the result; the console you are looking at was produced by a machine with nobody present, and the commit that made it is signed `proof-tick`.
9. What about the other Almanak entries? The public ones translate a handful of intents outside Almanak. This runs any Almanak strategy unchanged through Almanak's own execution interfaces and gateway plugin points, found two Almanak bugs (both since fixed on their main), and filed the pluggable-backend proposal as almanak-co/sdk#3 with a diff that applies to their main.
10. What did you find broken in KeeperHub? Three things, all filed, accepted and built to the maintainers' spec: no raw calldata write, no chained simulation, no acting wallet on sponsored executions. Two merged, one in review. A fourth, the workflow-preflight half of the sequence work, is filed as #2519 with the PR ready.
11. What is the worst thing that happened while building? Almanak's CLI loads the repo's `.env` on its own; a fork rehearsal picked up the production wallet address from it and compiled a deposit whose receiver was a wallet the fork did not control. The package now refuses an explicit wallet that disagrees with the one the API signs as. It is in the README under "What we got wrong first", with the other two.
12. What would you do next? The mainnet run (the code path is identical; only funding was withheld), Almanak's Safe plus Zodiac Roles mode, and, once #2519 lands, workflows whose later nodes are dry-run against the state the earlier ones produce.
