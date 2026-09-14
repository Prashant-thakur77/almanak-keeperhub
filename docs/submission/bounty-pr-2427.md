# Third PR, for issue #2427

Branch: `feat/simulate-call-sequence`, one commit `ea6935cb4`, off `staging` 28233554f, pushed to
https://github.com/Prashant-thakur77/keeperhub.

Open it at:
https://github.com/Prashant-thakur77/keeperhub/pull/new/feat/simulate-call-sequence

Base **`staging`**. Title:

`feat: #2427 simulate a sequence of calls against the state the earlier calls produce`

## Description - clear the box and paste from here down

## Issue

Closes #2427.

## What this changes

`POST /api/execute/contract-call` takes `calls` alongside `simulate: true` and answers with one result per
call, each simulated against the state the call before it produced. Approve-then-deposit is the case: today the
deposit's dry run says "ERC20: transfer amount exceeds allowance" every time, because the approve has not
landed and latest state is all a dry run sees.

**`eth_simulateV1` is the primary path**, as you said. It carries state across calls in one request, so the
sequence needs no reconstruction. I confirmed the behaviour you reported before writing anything: over
`[approve(spender, 1000), allowance(owner, spender)]` on Base Sepolia the second call returns `0x3e8`, and the
same pair through `eth_call` reads zero. A reverting call in the middle comes back as `status: "0x0"` with the
revert bytes under `error.data`, and the calls after it still execute, so one bad call does not cost the answer
for the rest.

**Nodes without it take the second mechanism**, not per-call estimates: each call's `debug_traceCall` state
diff (`prestateTracer`, `diffMode`) is replayed as an `eth_call` override for the next one. If a node offers
neither, the calls after the first report `failureKind: "unavailable"` rather than quietly answering against
latest state, which is the failure this issue exists to prevent. The mechanism is named in the response and
cached per chain, so a chain that has answered once is not probed again.

**The response says what a sequence is.** `atomic: false` is a field, not an implication: these are N separate
transactions sent from the wallet in that order, and nothing stops another transaction landing between them.
`calls` without `simulate: true` is a `400` for the same reason - this endpoint broadcasts one transaction per
request and never sends a sequence as a unit.

**The stablecoin ceiling is applied per call**, as the single-call path applies it. Each call is its own
transaction at broadcast, so the per-transaction ceiling is the one that governs it, and a call over it must
not dry-run clean. I did not add a bundle-level total: the ceiling's own policy (an over-cap approval to a
known protocol spender is allowed, to anything else is not) lives in `decide()`, and a second summing
definition beside it seemed more likely to drift than to help. If you meant the summed figure has to be
refused too, say so and I will add it there rather than reimplement the policy here.

`check-and-execute` is untouched, as you scoped it.

## Scope

One change. The schema alone would accept a body the route cannot answer; the route alone would never see it,
because the schema rejects a body with no top-level `functionName` first.

`prepareSimulationCall` is lifted out of `simulateContractCall` rather than copied: a sequence has to resolve
the ABI, coerce the arguments and encode exactly as a single call does, or the same call would encode
differently depending on which shape it arrived in. `simulateContractCall`'s behaviour is unchanged - the 42
existing tests in `execute-simulate.test.ts` and `execute-simulate-scope.test.ts` pass untouched.

`specs/api-coverage.json` is the regenerated `pnpm check:api-docs` artifact for the shifted docs line numbers.

**Overlap with my other two PRs:** #2449 (raw calldata) also touches `schemas.ts`, `contract-call/route.ts`,
`direct-execution.md` and `api-coverage.json`; #2450 touches the last two. Whichever merges first, I will
rebase the others and rerun `pnpm check:api-docs` - the coverage artifact is generated, so those conflicts are
mechanical. Happy to reorder them if one is easier to take first.

## How it was verified

`pnpm vitest run tests/unit/execute-simulate-sequence.test.ts` - 11 tests against a stubbed node: the request
shape (one `eth_simulateV1`, both calls, `validation: false`), gas and decoded return per call, a decoded
revert with the calls around it still reported, a short answer from the node marked unavailable instead of
invented, the fallback replaying call 1's state diff as call 2's override, the per-chain mechanism cache not
re-probing, a node with neither mechanism refusing to answer as if the earlier call never ran, a transport
error staying unavailable without falling back, an unencodable call reaching no node at all, and the stablecoin
ceiling refusing before anything is sent.

`pnpm vitest run tests/unit/contract-call-sequence.test.ts` - 9 tests against the route: per-call ABI
resolution (body, top-level fallback, explorer), an unresolvable ABI reported on `calls[N].abi`, a sequence
without `simulate` refused, 400 on a revert and 503 on an unavailable node, every malformed-`calls` shape
rejected before anything runs, and a single-call request still taking the ordinary path.

Two bugs these tests caught while I was writing them, both fixed: JSON-RPC quantities were being encoded with
`toBeHex`, which pads to even length and produces `0x03` where the spec wants `0x3`; and the override
accumulator was passed to the node by reference while it was still being written to, which `executeWithFailover`
could re-read on a retry. It now sends a snapshot per call.

`pnpm vitest run tests/unit` - 23,452 pass. The two failures on this tree, `workflow-directive-detection`
(`glob(...) is not a function`) and `agentic-wallet-rate-limit` (a clock-boundary assertion), reproduce on a
clean `origin/staging` checkout. `pnpm check`, `pnpm type-check` and `pnpm check:api-docs` are clean.

---

- [x] Targets `staging`
- [x] Title carries the issue number, or an exemption applies
- [x] `pnpm check` and `pnpm type-check` pass
- [x] No secrets, `.env` files, or credentials committed

(Delete the `## Screenshots` section: the change renders nothing.)
