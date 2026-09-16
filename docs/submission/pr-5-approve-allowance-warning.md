# PR 5 - approve with no upstream allowance check (#2367)

Branch `feat/approve-without-allowance-check` in /home/prashant/KeeperHub/keeperhub, one commit `c03c9eb7c`, off
`staging` 978b916aa. LOCAL ONLY until you say push.

Order of operations:
1. Post the "taking this" comment on #2367 (drafted earlier; cgair's route 2 is what this builds, the raw half).
2. Say push. Open the PR with the body below. Title:
   `feat: #2367 warn on an approve with no upstream allowance check`. Target `staging`.

## Body

## Issue

Closes #2367.

## What this changes

The allowance seam had one side. `missing-allowance-preflight` fires on a `write-contract` that spends an
allowance (`transferFrom`, `redeem`, `withdrawFrom`) with no `web3/check-allowance` upstream. The grant
side, an approve issued without reading the current allowance first, raised nothing. This adds it, on the
raw route cgair laid out: a warning, not an error, and not a redundancy claim.

**Detection.** A `web3/approve-token` node (token from `tokenConfig.customToken.address` or the legacy
`tokenAddress`, spender from `spenderAddress`), a `web3/write-contract` whose `abiFunction` is `approve`
(token from `contractAddress`, spender from `args[0]`), and each `approve` call inside a
`web3/batch-write-contract`. Protocol nodes are out of scope; their method is not in the config this
module reads.

**Two suppressors.** An upstream allowance read, by the same reachability rule the spend-side warning
uses (a check on a parallel branch or after the approve does not count; no edges falls back to
presence). And an upstream approve of the same token to the same spender, since the second approve then
has a grant it can be compared against. That second gate needs both addresses literal on both nodes: a
template reference or a platform-listed token id never suppresses, so leaving information out never hides
a warning.

**Wording.** `nodes[i].config approves spender 0x... without reading the current allowance first ... This is
a hint, not a redundancy claim: an exact-amount approve is consumed by the spend that follows it and is
needed on every run.` The platform blocks MaxUint256 in `lib/scan/factory/validate.ts`, so the approve it
pushes people toward is exactly the one that is not redundant; the message says so rather than implying
the node can be removed.

Both checks now share one gate. `buildAllowanceGate` ran once before; it still runs once, and the approve
pass reuses its memoised ancestor sets. On the 50-node perf fixture: 8.4 ms per 100 validations on
`staging`, 9.6 ms with this change, against a 50 ms budget.

The spend-side check, its method list and its message are untouched. New code: `APPROVE_WITHOUT_ALLOWANCE_CHECK`
in `validate-workflow-codes.ts`, a row and a paragraph in `docs/agent/mcp-validate-workflow.md`.

## Scope

One change: a warning code, its detector, its tests and its documentation row. Nothing else depends on
it and it depends on nothing outside `validate-workflow.ts`.

## How it was verified

`tests/unit/validate-workflow-approve-gate.test.ts`, 24 cases: approve-token, write-contract approve
(bare and full signature), batch calls with one warning per approve call pointing at that call, read-contract
ignored, non-approve methods ignored, check-allowance upstream / parallel / downstream, earlier same-grant
approve (case-insensitive across node kinds), different spender, parallel earlier approve, template
spender, platform-listed token id, no-edges fallback. The spend-side suite (45) is unchanged and passes.

`tests/unit/validate-workflow-seed-workflows.test.ts` pins the count of shipped seed workflows that raise
the hint at 12: every seed that has an Approve Token node, none of which has a Check Allowance node. The
existing assertion that no seed raises `missing-allowance-preflight` still holds. The pin is deliberate:
a seed gaining or losing an approve, or the detector changing shape, moves the number.

`pnpm check`, `pnpm type-check`, `pnpm check:api-docs` (no drift), `pnpm vitest run tests/unit/validate-workflow`
(193 pass; the 50 ms perf test fails on this machine on untouched `staging` too, under a load average of
30 from unrelated jobs, hence the min-of-40 numbers above).
