# Bounty BUIDL (DoraHacks, Best KeeperHub Feature) - separate submission

The rules say a BUIDL applies to one track, so this is its own BUIDL, distinct from the main-track one.
The bounty is judged on mergeability, value to the platform, code quality and tests, scope and
completeness. Every claim below links to a pull request the maintainers reviewed.

## Title (max 8 words)

Seven KeeperHub pull requests, three merged

## Tagline

Gaps found while wiring Almanak to KeeperHub, filed as issues, built to the maintainers' spec, then three
more from their own backlog.

## Description

Building almanak-keeperhub (the main-track entry) meant running hundreds of real executions through
KeeperHub's direct execution API. Four things were missing, and each became an issue, a maintainer
review of the issue, and a pull request built to what the review settled on. Two are merged and were
deployed to app.keeperhub.com on 16 September; the integration's own client detected the change from the
API's answers and switched to them without a code change.

After those four, three of the maintainers' own accepted issues were taken from the backlog, so the
work is not only what one integration needed.

| Issue | Pull request | State | What it does |
|---|---|---|---|
| #2426 (mine) | #2449 | merged, on production | `POST /api/execute/contract-call` accepts raw `data`; decoded losslessly against the ABI, re-encoded and compared, so nothing unrepresented reaches the signer |
| #2427 (mine) | #2452 | merged, on production | `calls[]` simulated as a sequence, each against the state the previous produced: `eth_simulateV1` where the node has it, trace-derived `eth_call` overrides where it does not |
| #2428 (mine) | #2450 | merged | `executedCall.from`: the wallet that acted, from the traced frame, correct under direct, sponsored and Safe routing with one definition |
| #2519 (mine) | #2531 | in review | The workflow preflight simulates consecutive write nodes as one sequence, walks nodes in edge order, caps runs at the sequence limit and falls back per node when a sequence cannot answer |
| #2367 (joelorzet) | #2533 | in review | An approve with no upstream allowance check gets a hint whose wording depends on the amount: unlimited approves re-grant what is in place, exact ones are consumed every run |
| #2497 (suisuss) | #2532 | in review | A contributor runbook for adding an EVM chain, a seed that fails instead of warning on a chain with no explorer, and one shared token-list set instead of two hand-synced copies |
| #2496 (suisuss) | #2534 | in review | `math/aggregate` no longer truncates a fraction next to a wei amount: fixed point through the post-operation, magnitude-aware division, division by zero reported rather than thrown |

Tests added across the seven: 24 (approve gate), 78 (chain seed coverage), 25 (aggregate), 18
(preflight sequence), plus the suites in the three merged PRs. Lint, type-check and the docs coverage check
are clean on every branch.

## What still breaks or is unfinished

Three defects in the state-override fallback of the merged sequence simulator (#2452) were found by
other contributors within two days of the merge: #2517, #2541, #2542. All three are accepted and under
fix by the people who found them. The `eth_simulateV1` path is not affected. #2496 is confirmed but not
yet labelled accepted, so its pull request's issue-link check is red until the maintainers label it.

## Links

- Pull requests: https://github.com/KeeperHub/keeperhub/pulls?q=is%3Apr+author%3APrashant-thakur77
- Issues: https://github.com/KeeperHub/keeperhub/issues?q=is%3Aissue+author%3APrashant-thakur77
- The integration that needed them: https://github.com/Prashant-thakur77/almanak-keeperhub
- Console showing the merged features in use on production: https://prashant-thakur77.github.io/almanak-keeperhub/

## Transaction executed through KeeperHub

The runner's most recent lifecycle on the console, which used raw calldata and a sequence dry run on
production: https://prashant-thakur77.github.io/almanak-keeperhub/console/

## Contact

prashant101007@gmail.com, GitHub Prashant-thakur77
