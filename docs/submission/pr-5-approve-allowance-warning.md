# PR 5 - approve with no upstream allowance check (#2367)

Branch `feat/approve-without-allowance-check` in /home/prashant/KeeperHub/keeperhub, one commit `c03c9eb7c`, off
`staging` 978b916aa. LOCAL ONLY until you say push.

Order of operations:
1. Post the "taking this" comment on #2367 (drafted earlier; cgair's route 2 is what this builds, the raw half).
2. Say push. Open the PR with the body below. Title:
   `feat: #2367 warn on an approve with no upstream allowance check`. Target `staging`.

## Claim comment (post on #2367 first)

Taking this on route 2, as cgair set out above: the raw `web3/approve-token` and `web3/write-contract` half, protocol nodes deferred to after #2366. dtopenclaw, if you had started on it say so and I will step back; nothing on the thread since the 15th suggested you had. Branch is ready, PR up shortly.

## PR body

## Issue

Closes #2367.

## What this changes

Route 2 from the thread: the raw half. Protocol nodes wait for #2366.

A new warning, `approve-without-allowance-check`. It fires on a `web3/approve-token` node, a `web3/write-contract` whose method is `approve`, and each `approve` call inside a `web3/batch-write-contract`, when there is no `web3/check-allowance` upstream and no earlier approve of the same token to the same spender.

Two things suppress it. An upstream allowance read, using the same reachability rule the spend-side warning already has (a check on a parallel branch or after the approve does not count; a workflow with no edges falls back to presence). And an earlier approve on the path with the same token and spender, which is the clause from the plan. That second one only applies when both addresses are literal on both nodes. A template reference, or a platform-listed token given by id rather than address, never suppresses, so leaving information out never hides the warning.

The message does not claim redundancy. It says the approve runs without reading the current allowance and that adding a check lets the workflow skip it when the allowance already covers the amount, then says outright that an exact-amount approve is consumed by the spend after it and is needed every run. Given `lib/scan/factory/validate.ts` blocks MaxUint256, that is the normal case, and a warning that called it pointless would be wrong on most of the workflows it fires on.

cgair's note on the thread about `readNodeActionConfig` was right: it only carried `actionType`, `abiFunction` and `calls`. It now also reads `contractAddress`, `args`, `tokenConfig`, `tokenAddress` and `spenderAddress`, which is what the token/spender comparison needs. Nothing from the protocol registry is imported, so the file keeps its no-DB, no-network, safe-from-tests property.

The two allowance checks now share one gate. `buildAllowanceGate` still runs once per validation; the approve pass reuses its memoised ancestor sets rather than walking the graph again. On the 50-node perf fixture that is 8.4 ms per 100 validations on `staging` and 9.6 ms with this change, against the 50 ms budget.

The spend-side check, its method list and its message are untouched. `docs/agent/mcp-validate-workflow.md` gets a row and a short paragraph on the extra suppressor.

One limit worth knowing: an ERC-721 `approve(to, tokenId)` on a write-contract node has the same signature as the ERC-20 one and cannot be told apart without an ABI, so it gets the hint too. It is advisory and does not block anything, and `web3/approve-token` is ERC-20 by construction, so the ambiguity is confined to hand-written write-contract nodes. `setApprovalForAll`, `permit` and `increaseAllowance` are not matched.

## Scope

One change: a warning code, its detector, tests and a docs row. It depends on nothing outside `validate-workflow.ts` and nothing depends on it.

## How it was verified

`tests/unit/validate-workflow-approve-gate.test.ts`, 24 cases: approve-token, write-contract approve (bare name and full signature), batch calls with one warning per approve call pointing at that call, read-contract ignored, non-approve methods ignored, check-allowance upstream / on a parallel branch / downstream, an earlier same-grant approve (matched case-insensitively across node kinds), a different spender, an earlier approve on a parallel branch, a template spender, a platform-listed token id, and the no-edges fallback.

The spend-side suite (`validate-workflow-allowance.test.ts`, 45) is unchanged and passes. `validate-workflow-seed-workflows.test.ts` still asserts no seed raises `missing-allowance-preflight`, and now pins the number of seeds that raise the new hint at 12, which is every seed with an Approve Token node and no Check Allowance node. That matches the 12 of 43 in the issue. The pin is deliberate: a seed gaining or losing an approve, or the detector changing shape, moves the number and should be looked at.

`pnpm check`, `pnpm type-check` and `pnpm check:api-docs` clean.

## Screenshots

n/a.

---

- [x] Targets `staging`
- [x] Title carries the issue number, or an exemption applies
- [x] `pnpm check` and `pnpm type-check` pass
- [x] No secrets, `.env` files, or credentials committed
