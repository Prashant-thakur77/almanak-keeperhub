# KeeperHub issues to file (besides the raw-calldata one in bounty-issue.md)

Each follows ISSUES.md: Reason, Scope, Plan. File them from your account; the sponsor's
previous wrap-up rewarded exactly this kind of structured feedback.

## 1. `simulate` cannot chain the calls of a bundle

Title: `feat: simulate a sequence of calls against the state the earlier calls produce`

**Reason.** `POST /api/execute/contract-call` with `simulate: true` runs `estimateGas` and
`call` against live state (docs/api/direct-execution.md, "Dry-Run Simulation"). A
strategy bundle is usually `approve` then `deposit`; the deposit reverts on allowance
until the approve lands, so only the first call of any bundle can be dry-run. Almanak's
own LocalSimulator has the same limit and falls back to compiler gas for later calls.
What told me to expect otherwise: the hackathon brief says "you dry run it without
touching the chain" and `validate_workflow` with `deepCheck` reads as a whole-workflow
check. Cost: a revert in the second call is only discovered at broadcast, with the hash.

**Scope.** In: `contract-call` and `check-and-execute` simulate paths accepting an
ordered array of calls, each simulated against the state overrides the previous one
produced (or an `eth_call` with `stateDiff`), returning one result per call. Out:
protocol actions (no simulate today), Solana, workflows.

**Plan.** Accept `calls: [{contractAddress, functionName, functionArgs, abi, value}]`
alongside `simulate: true`; run them sequentially on a forked state (Anvil-style
`eth_call` with state overrides from the prior call's trace, or a bundle simulator);
return `results[]` with `gasEstimate` and `wouldRevert` per call. Response shape mirrors
the single-call one so existing clients are unaffected.

## 2. Sponsored executions need a `sender` field the caller can trust

Title: `feat: return the acting wallet on sponsored executions`

**Reason.** With gas sponsorship the explorer shows the relayer as `from`
(docs/wallet-management/onchain-appearance.md). A framework that reads receipts to
track positions (Almanak's receipt parsers key on the acting wallet) has to fall back to
event arguments to know who acted. The status body carries `sponsored: true` but no
sender. Cost: every integration re-derives the actor from logs, and a reviewer cannot
tell from the explorer which org wallet moved the funds.

**Scope.** In: `GET /api/execute/{id}/status` and the 202 envelope of the three write
routes. Out: workflow executions, Solana.

**Plan.** Add `sender` (the organization wallet that is `msg.sender` for the call) next
to `sponsored`; document it in direct-execution.md under "Sponsored Executions".

## 3. Docs link in the hackathon brief redirects

Title: `docs: the MCP server guide URL in the DoraHacks brief redirects`

Not an issue for the repo; tell the team in Discord that
`https://docs.keeperhub.com/ai-tools/mcp-server` 308s to `/agent/mcp-server` and the
brief still links the old path.
