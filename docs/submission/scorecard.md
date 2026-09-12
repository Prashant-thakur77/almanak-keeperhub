# Scorecard against the rubric and the previous winners

Written 2026-09-12, before the hosted run. Scores are my own estimate on the judges'
five criteria, with the evidence a judge would find at repository level. The comparison
column is what the money winners of the previous edition (Agents Onchain, Aug 2026) and
of ETHGlobal OpenAgents (May 2026) did on the same criterion.

| Criterion | This build | Evidence | What winners did | Adopted |
|---|---|---|---|---|
| Integration depth | 9 | KeeperHub sits inside Almanak's own execution interfaces (`Signer`, `Submitter`, `Simulator`) and gateway plugin points; unmodified strategies, the full deposit-to-redeem lifecycle, and Almanak's agent CLI (`ax`) all execute through it; a KeeperHub-scheduled keeper generated from the strategy config runs alongside | n8n (3rd) shipped a node inside a live product with users and got it verified by n8n; three public Almanak entries integrate one level up (intent translators) | Same shape as n8n: inside the partner's own extension mechanism, not beside it |
| Execution through KeeperHub | 8 before the hosted run, 9 after | Every write is `POST /api/execute/contract-call` with simulate then `Idempotency-Key`; receipts verified by KeeperHub; every execution recorded with id, hash, link | Meld (1st): "400 on-chain executions, 1,092 transactions", all hashes in `docs/receipts.json`; ChronicleAI (2nd): "3,000+ verified transactions" | `keeperhub-receipts.json` written automatically per run, `scripts/benchmark.py` produces counts and hashes at volume |
| Reliability and observability | 9 | Five deliberate failure modes; same-key retries on transport and 5xx; resilient polling; bundle truncation visible; KeeperHub verdict outranks the chain receipt; independent review with eight findings fixed | n8n: "11/11 affordable transfers landed, 20/20 impossible transfers refused before submission, p50 19.2s"; Interlock (6th) found a double-broadcast bug that got a same-day fix | Benchmark table with refused/landed counts and p50/p95 latency; two Almanak bugs found with stack traces |
| Usefulness and originality | 8 | Almanak's own repo documents the duplicate-mint incident this prevents; Almanak's private-relay submitter is a stub; nobody else touches the execution layer or the agent CLI | Winners solved a pain of the partner's users rather than a generic wrapper | Incident quoted from Almanak's source; `ax` mode ties the theme (agent decides, KeeperHub executes) to a live product's own agent |
| Developer experience and code quality | 8 | One command per mode, `doctor`, 84 unit tests, lint, CI, MIT, README with known gaps, upstream patch, wheel builds | Every money winner had a merged upstream PR to KeeperHub; LIFELINE filed 7 issues; the sponsor praised entries that "documented their own limitations, occasionally retracting a claim" | Bounty PR ready; three KeeperHub issue drafts; a "What we got wrong first" section |

What I did not adopt, on purpose:

- Marketplace listing and x402 or MPP payments (ChronicleAI, LIFELINE). This integration executes a framework's own transactions; selling them per call is not what Almanak users need. Listed honestly in the surfaces table.
- Volume for its own sake (Meld). Each benchmark execution is a real approval and costs gas; the benchmark is sized so the numbers are meaningful without burning the wallet.
- A browser extension on KeeperHub's pages (Meld) or a hosted web app (ChronicleAI). Instead: `almanak-keeperhub console`, a local proof page that updates live beside the terminal, in the same proof-first style (status strip, audit timeline, verdict segments), with no build step and no hosting to break during the finalist call.

Where the previous winners were stronger than this build, still:

- Proof at volume: until the hosted run happens, this repo has fork receipts only. The first mainnet run and `scripts/benchmark.py --executions 5` close that gap.
- A merged upstream PR: the bounty PR is ready but gated on KeeperHub accepting the issue. File it on day 1.
- Surfaces breadth: REST direct execution, agent-authored workflows (the keeper), and the audit trail. No x402 or MPP, deliberately, stated as such.
