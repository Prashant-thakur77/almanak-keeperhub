# Issue 4 to file on github.com/KeeperHub/keeperhub

Title: `feat: workflow preflight simulates each write node against the state the earlier nodes produced`

File it today. Raise it at office hours (15:30 IST) so triage is fast: "this is the workflow half of #2427,
which suisuss scoped out of that issue; the mechanism is already in #2452."

---

### Before filing

- [x] I searched open and closed issues. #2427 (accepted, PR #2452, merged into `staging` on 15 Sep) is the
  direct-execution half of this and its triage narrowed scope to `contract-call`, leaving the workflow engine open. `docs/agent/mcp-test-workflow.md`
  lists workflow dry-run under "Roadmap". Nothing open covers the preflight simulator.
- [x] I checked the behaviour on `staging` at `f2c9cb8cf` (16 Sep) and reproduced it on app.keeperhub.com on 15 Sep 2026.
- [x] This is one change: one function, one new input to it, no route or response shape altered.

### Reason: what you cannot do today

`POST /api/workflows/{id}/simulate` runs `runWorkflowSimulation` (`lib/workflow/run-simulation.ts`), which
calls `simulateContractCall` once per write node, each against latest chain state. A workflow whose second
write depends on its first - approve then deposit, the shape of nearly every DeFi workflow - warns on the
second every time, because the first has not happened yet.

Reproduced on app.keeperhub.com on 15 Sep 2026 with a two-node workflow, literal arguments, no templates:
`approve(vault, 5000000)` on Base Sepolia USDC, then `deposit(5000000, wallet)` on an ERC-4626 vault
(workflow `a1wee2uy9lqdz0zp4cpry`, deleted after the run). The preflight answered:

```json
{ "simulatedNodeCount": 1,
  "warnings": [ { "code": "SIMULATION_WOULD_REVERT", "nodeId": "deposit",
      "message": "Deposit 5 USDC may revert: Error(ERC20: transfer amount exceeds allowance). This may depend on an earlier step in this workflow." } ] }
```

The warning is wrong, and the platform knows it might be: the sentence "This may depend on an earlier step in
this workflow" is added by `simulationPreflightMessage` whenever a reachable write precedes the node
(`run-simulation.ts:310-322`). The comment above it says why: "Simulation reads current state, not the state
the workflow will have produced by the time this node runs, so the claim is softened the same way a revert
is." The limitation is documented in the code and hedged in the message. Two things follow from it:

1. Every approve-then-spend workflow gets a warning the author has to learn to ignore, which is how real
   warnings get ignored too. 12 of the 43 seeded workflows are that shape (#2367 counted them).
2. "Earlier" is array order, not execution order. The loop at `run-simulation.ts:721` iterates `nodes` as
   stored and uses `reachableNodeIds` only as a filter, so `hasEarlierReachableWrite` is true or false by
   where a node sits in the JSON, not by what runs before it.

The mechanism that fixes the direct-execution version of this is already on `staging`: #2452 (merged) adds
`simulateCallSequence` in `lib/execute/simulate-sequence.ts` (`eth_simulateV1`, with `debug_traceCall` state
diffs replayed as `eth_call` overrides where a node lacks it), which simulates N calls each against the state
the previous one produced. suisuss scoped the workflow engine out of #2427 so it could ship; this is that
follow-up, and it adds no new mechanism.

### Reason: what the workaround costs

The warning is advisory, so nothing is blocked; the cost is that the preflight cannot be trusted on the
workflow shape it is most needed for. An agent building a workflow through MCP sees "may revert" on a
correct deposit and either reworks a correct workflow or learns to ignore the check. I hit both with a
generated compounder (approve then `morpho/vault-deposit`) in the Almanak integration, and the reason the
keeper there runs with template-bound amounts is partly that the preflight could not tell me anything useful
about literal ones.

### Scope: what this touches, and what it does not

In: `runWorkflowSimulation` groups reachable, enabled, template-free `web3/write-contract` nodes that sit on
one linear path and one chain into a sequence, simulates the sequence with `simulateCallSequence`, and
attributes each result back to its node. A node whose earlier steps were applied loses the "may depend on an
earlier step" hedge, because it no longer does. Nodes the grouping cannot place - after a fork, on a different
chain, following a template-bound node - keep today's per-node simulation and today's hedge, so nothing gets
less informative.

Out: protocol nodes (not simulated today; method resolution for them is #2366's seam), template-bound nodes
(the roadmap's pin schemas), `web3/transfer-funds` (native value, no calldata; stays per-node),
`check-and-execute`, the route, the response shape. Warnings stay advisory: `valid` is not computed here.

One change: a workflow with no chainable run is simulated exactly as today.

### Plan: what you propose

- `lib/workflow/run-simulation.ts`: order reachable nodes by following edges from the trigger rather than
  by array index. Collect maximal runs of consecutive chainable nodes (same `network`, single in-edge, single
  out-edge, supported type, no template variables). Each run becomes one `simulateCallSequence` call; the
  per-call `SimulateResult` maps onto the existing `simulateNode` outcome shape so `makeIssue` and the
  counters are untouched. Runs of length one and unplaceable nodes take the existing path.
- `simulationPreflightMessage`: the hedge is added only when the node was not simulated with its earlier
  steps applied. Chained nodes get the plain sentence.
- Deadline and node cap unchanged; a run is one round trip where it was N, so this is cheaper, not dearer.
- Tests in `tests/unit/workflow-run-simulation.test.ts`: approve-then-deposit produces no warning when the
  sequence answers clean and a plain (unhedged) warning when it answers a revert; a fork ends a run and the
  branch nodes simulate per-node with the hedge; a chain change ends a run; a template-bound node ends a run;
  a sequence answering `unavailable` falls back to per-node for that run rather than warning on nothing;
  execution order follows edges when array order differs; the seeded-workflow baseline
  (`validate-workflow-seed-workflows.test.ts`) unchanged.
- Docs: `docs/agent/mcp-test-workflow.md` roadmap line replaced with what is supported; the simulate route's
  section in `docs/api/direct-execution.md` gains a paragraph; `specs/api-coverage.json` regenerated.
- Builds on `simulateCallSequence` from #2452, already on `staging`, so the PR sits directly on `staging`. It is
  written and passing (33 tests in `workflow-run-simulation.test.ts`, lint, type-check, `check:api-docs`); I will
  open it as soon as this is accepted.

### Plan: alternatives you considered

- Keep the hedge and document it: what exists today; the warning stays untrustworthy on the common shape.
- Simulate the whole workflow through the engine with pinned inputs (the roadmap item): the right long-term
  answer and a much larger change; this issue is the part of it that needs no pin schema, because it only
  covers nodes whose arguments are already literal.
- Reconstruct state per node from the previous node's trace, without `eth_simulateV1`: the fallback #2452
  already implements; using the sequence simulator gets both mechanisms for free.

### AI assistance

AI assistance (Claude, Anthropic) was used to write and draft this issue.

### Scope: compatibility

- [ ] Changes an existing response shape, status code, CLI flag, or default.
- [ ] Adds, removes, or upgrades a dependency.
- [ ] Changes database schema or requires a migration.
- [ ] Touches authentication, permissions, validation, or spend limits.
- [ ] Changes pricing, plan limits, or anything a user is charged.
