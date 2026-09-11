# Pull request (open after the issue has the `accepted` label)

Branch: `feat/raw-calldata-contract-call` in `/home/prashant/KeeperHub/keeperhub` (one commit `d3cc7a316`).
Base: `staging`. Push to your fork, then open the PR against `KeeperHub/keeperhub:staging`.

Title (the pr-title-check workflow enforces conventional commits and the issue link check needs the number):

`feat: #<ISSUE> accept raw calldata on POST /api/execute/contract-call`

Before pushing, amend the commit title to the same string:

```bash
cd /home/prashant/KeeperHub/keeperhub
git commit --amend -m "feat: #<ISSUE> accept raw calldata on POST /api/execute/contract-call" --no-edit
```

## Body (follows .github/PULL_REQUEST_TEMPLATE.md)

**Issue**

Closes #<ISSUE>.

**What this changes**

`POST /api/execute/contract-call` accepts `data` (hex calldata) in place of `functionName` and `functionArgs`. The route decodes it against the ABI in the body, or the explorer-verified ABI it already fetches when `abi` is omitted, into the canonical function key and a typed `functionArgs` array, and then takes the existing read, `simulate` and write paths unchanged. Every guard that keys on the function and arguments (stablecoin cap, payable check, spending caps, idempotency, the execution record) keeps working because it still sees a named function.

Nothing is inferred: a selector the ABI does not contain returns `400` on field `data`. No signature database is consulted. When `functionName` or `abiFunction` is present alongside `data`, the typed fields win.

**Scope**

- `app/api/execute/_lib/raw-calldata.ts` (new): selector check, ABI decode, argument rendering.
- `app/api/execute/contract-call/route.ts`: one block before validation; the rest of the route is untouched.
- `docs/api/direct-execution.md`: "Raw calldata" subsection.
- Not changed: `check-and-execute`, `transfer`, protocol actions, `/api/execute/node`, the workflow `web3/write-contract` node, the MCP tool schema.

**How verified**

- `pnpm vitest run tests/unit/execute-raw-calldata.test.ts tests/unit/contract-call-raw-calldata.test.ts`: 16 tests. Reverting the route change makes 6 of the 8 route tests fail, so they test the wiring, not the mocks.
- `pnpm vitest run tests/unit/contract-call-ambiguous-key.test.ts tests/unit/execute-simulate.test.ts tests/unit/execute-simulate-scope.test.ts`: neighbouring route tests still pass.
- `pnpm check`: clean. `pnpm type-check`: clean after `pnpm discover-plugins`.
- Motivating integration: github.com/<you>/almanak-keeperhub, where Almanak's compiled transactions reach KeeperHub as calldata.

**Checklist**

- [x] Targets `staging`
- [x] `pnpm check` passes
- [x] `pnpm type-check` passes
- [x] Tests added
- [x] Docs updated
