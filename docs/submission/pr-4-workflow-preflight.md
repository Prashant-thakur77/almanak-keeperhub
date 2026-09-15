# PR 4 - workflow preflight against carried state

Branch `feat/workflow-preflight-sequence` in /home/prashant/KeeperHub/keeperhub, one commit `f0a8f80`, off
`staging` 5194c9864 (which already contains #2452, so nothing is stacked). LOCAL ONLY until you say push.

Order of operations:
1. File the issue from `issue-4-workflow-preflight.md` (title: `feat: workflow preflight simulates each write node
   against the state the earlier nodes produced`). Mention it at office hours.
2. When it has `accepted` and a number N: tell me, I amend the commit title to `feat: #N ...`, then you push and
   open the PR with the body below. Title: `feat: #N workflow preflight simulates each write node against the
   state the earlier nodes produced`.

## Body

## Issue

Closes #N.

## What this changes

`POST /api/workflows/{id}/simulate` simulated every write node on its own against latest state, so the second
call of an approve-then-deposit pair warned on allowance every time, and the message hedged that it "may depend
on an earlier step". The comment above `simulationPreflightMessage` says exactly that: the simulation reads
current state, not the state the workflow will have produced. This is the workflow half of #2427, which you
scoped to `contract-call` so it could ship; the mechanism it needs is the `simulateCallSequence` that landed
in #2452.

**Runs.** Consecutive `web3/write-contract` nodes on one chain along one linear path - each with one in-edge,
one out-edge, and the edge between them - are collected into a run and simulated with one
`simulateCallSequence` call, each against the state the previous one produced. Every per-call
`SimulateResult` maps onto the existing outcome shape through the same code the single-node path uses, so
`makeIssue`, the parameter paths and the counters are untouched.

**Order.** Nodes are walked in the order the edges connect them, from the trigger, rather than the order they
were stored: `hasEarlierReachableWrite` was previously decided by array position, which is not what runs first.
Nodes the edges do not reach keep their array position at the end; with no edges the array order is the only
order there is, as before.

**The hedge.** A node simulated within a run has had its earlier steps applied, so its warning is the plain
"would revert" form. The first node of a run, and every node that could not join one, keeps the hedge, because
for them it is still true.

**What ends a run, and stays exactly as today:** a fork (out-degree above one, or a node with more than one
in-edge), a chain change, a node whose inputs carry a runtime template, a transfer node, and any node
`prepareNode` refuses (Solana, a Safe signer, an invalid network). Runs of length one take the single-node
path. Protocol nodes are not simulated today and are not touched here; method resolution for them is #2366's
seam.

The response shape, the advisory-only contract, the 50-node cap and the 15-second deadline are unchanged. A run
is one node round trip where it was N, so this is cheaper on the deadline, not dearer.

## Scope

One change. The refactor that splits `simulateNode` into `prepareNode` and `outcomeFromResult` exists so the
sequence path and the single path share the eligibility checks and the result mapping; neither half is useful
alone.

No route, schema, docs-coverage or persistence change. `docs/api/direct-execution.md` rewrites the paragraph
that said each write is simulated independently, and `docs/agent/mcp-test-workflow.md` narrows its roadmap
line to what is still missing: template-bound nodes, which need the pin schemas. Both edits sit below every
line `specs/api-coverage.json` records, so the artifact did not move (`pnpm check:api-docs` clean).

## How it was verified

`pnpm vitest run tests/unit/workflow-run-simulation.test.ts` - 33 tests, 24 existing untouched and 9 new: an
approve-then-deposit pair reaches `simulateCallSequence` once and `simulateContractCall` never; a revert in
the second call is reported plainly on the right node and parameter path with no hedge; nodes stored
deposit-first are simulated approve-first because the edges say so; a fork, a chain change, a template-bound
node and a transfer node each end a run and fall back to per-node simulation; a sequence the node cannot
answer marks every node of the run unavailable rather than inventing a result; a single write stays on the
single-call path.

`tests/unit/validate-workflow-seed-workflows.test.ts` (the seeded-workflow baseline) and
`tests/unit/execute-simulate-sequence.test.ts` pass unchanged. `pnpm check`, `pnpm type-check` and
`pnpm check:api-docs` are clean.

Reproduced before the change on app.keeperhub.com on 15 Sep 2026 with a two-node literal approve-then-deposit
workflow (`a1wee2uy9lqdz0zp4cpry`, deleted after): `simulatedNodeCount: 1` and a `SIMULATION_WOULD_REVERT`
on the deposit reading "ERC20: transfer amount exceeds allowance. This may depend on an earlier step in this
workflow."

---

- [x] Targets `staging`
- [x] Title carries the issue number, or an exemption applies
- [x] `pnpm check` and `pnpm type-check` pass
- [x] No secrets, `.env` files, or credentials committed
