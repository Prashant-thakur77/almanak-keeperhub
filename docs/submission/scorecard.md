# Scorecard against the rubric and the previous winners

Rewritten 16 Sep 2026, two days before the deadline. Scores are my own estimate on the judges'
five criteria, with the evidence a judge finds at repository level. The comparison column is what
the money winners of the previous edition (Meld, ChronicleAI, n8n-nodes-keeperhub) did on the same
criterion, and the last column is what this build took from them and what it added.

| Criterion | This build | Evidence | What the winners did | Taken, and added |
|---|---|---|---|---|
| Integration depth | 9 | KeeperHub sits inside Almanak's own execution interfaces (`Signer`, `Submitter`, `Simulator`) and gateway plugin points; the strategy file is byte-identical except its chain list; the full lifecycle (deposit, keeper, guarded exit) and Almanak's agent CLI run through it; a pluggable-backend proposal is filed on Almanak's own tracker (almanak-co/sdk#3) with a diff that applies to their `main` | n8n shipped inside a live product's own extension mechanism | Same shape as n8n, one level deeper: the execution layer, not an intent translator. Added: the live project's tracker shows the seam |
| Execution through KeeperHub | 10 | 460+ executions on Base Sepolia through app.keeperhub.com, every one with KeeperHub's verdict frozen next to it on a public console; direct execution, check-and-execute, a scheduled workflow run by KeeperHub's engine; the client already uses the two features it got merged upstream and falls back until production deploys them | Meld: 400 executions, hashes in a file; ChronicleAI: 3,000+ transactions and a live app | Volume with a purpose, plus surfaces none of them used (check-and-execute, a proof that regenerates itself every six hours by a GitHub runner) |
| Reliability and observability | 10 | 50/50 impossible calls refused before broadcast; 200/200 landed and verified; 50/50 retries replayed with zero double broadcasts; 10/10 crashed processes resumed from a fresh process; seven deliberate failure modes; 11 conformance tests against production every six hours; three real Claude sessions over MCP recorded unedited; a rehearsal on a fork against both API generations | n8n: 11/11 landed, 20/20 refused, p50 19.2s; Interlock found a double-broadcast bug | The same table at ten times the scale, and the failure modes are the point of the video, not a footnote. Added: the proof keeps itself current |
| Usefulness and originality | 9 | Almanak's own repository documents the duplicate-mint incident this prevents; its private-relay submitter is a stub; nobody else touched the execution layer. Three KeeperHub gaps found, filed, accepted, built to spec, two merged, and then used by the integration itself | Winners solved a pain of the partner's users rather than wrapping an API | Added: the loop closed, from finding a gap to shipping it upstream to consuming it |
| Developer experience and code quality | 9 | `pip install almanak-keeperhub`; one command per mode; 563 unit and property tests (every indexed signature fuzzed), mypy strict, CI green, SECURITY.md with the threat model, MIT; a README that leads with the result and a "what we got wrong first" section with four entries, three of them caught by machines | Every money winner had a merged upstream PR; the sponsor praised entries that documented their own limitations | Two merged PRs, a third with conditional approval, a fourth ready. Added: the mistakes are in the README with what caught them |

What I did not do, on purpose:

- Mainnet. The code path is identical to the testnet one; withholding real money was a choice, and the runner proves the path four times a day instead.
- Marketplace listing, x402 or MPP. This executes a framework's own transactions; nothing is sold per call.
- A hosted web application. The console is a static export of the local page, published by the runner, with nothing behind it to fall over during judging.

Where the previous winners are still stronger:

- ChronicleAI ran on Base mainnet with real value; this ran on Base Sepolia only.
- Meld's test count (902) is higher in absolute terms; this build's 563 include 404 property cases over one module.
- n8n's node was verified by the partner's own team; Almanak has not yet answered issue #3.
