# Second PR, for issue #2428 (accepted 14 Sep; Luca in Discord: "the easiest yes of the three, send it as its own PR")

Branch: `feat/executed-call-sender`, one commit `aff90e002`, off `staging` 28233554f, pushed to
https://github.com/Prashant-thakur77/keeperhub. Base: `KeeperHub/keeperhub:staging`.

Open it at:
https://github.com/Prashant-thakur77/keeperhub/pull/new/feat/executed-call-sender

Title:

`feat: #2428 return the acting wallet on sponsored executions`

## Body (follows .github/PULL_REQUEST_TEMPLATE.md)

**Issue**

Closes #2428. Built to the shape triage described rather than the one filed: the traced frame's `from`, not a
top-level `sender`.

**What this changes**

`ExecutedCall` carries the matched frame's `from`. That is one field and one line in `resolveExecutedCall`:
`decodeExecutedCall` already finds the frame that actually hit the target and parses its `from`
(`lib/web3/trace-decode.ts:43`), then drops it, keeping only `topLevelTo`.

It is the acting address under every routing mode with no second definition:

- direct send: the organization's EOA
- sponsored: still that EOA, where the transaction's own `from` is the relayer and names the wrong wallet
- Safe and Safe-role: the Safe, where the EOA signs the outer transaction but `msg.sender` at the target is the
  Safe (`lib/safe/signer-resolver.ts`) - which is why the top-level `sender` field I originally proposed was wrong

It travels with the `executedCall` already returned under `result` on `GET /api/execute/{id}/status`, so no
route, response type or persistence changes. It is best-effort with it: both are absent when the transaction
cannot be traced, and the docs now say so.

**Scope**

- `lib/web3/trace-decode.ts`: `from` on the `ExecutedCall` type and on what `resolveExecutedCall` returns.
- `docs/api/direct-execution.md`: a "Who acted" subsection under "Sponsored Executions", with the
  best-effort caveat; `specs/api-coverage.json` regenerated with `pnpm check:api-docs` so `pr-checks.yml`
  does not fail on stale coverage.
- Not changed: routes, response types, persistence, workflow executions, Solana.
- Deliberately out: the `executedCall.*` workflow output fragments in `plugins/field-fragments.ts`. Exposing
  `from` there is a four-line addition if you want it, but the issue scoped workflows out.

**How verified**

- `pnpm vitest run tests/unit/trace-decode.test.ts`: 22 tests, four new - the relayer case (the frame's
  address, not the transaction's), the delegated case (the org EOA while a relayer paid), the Safe-routed case
  (the Safe, not the signing EOA), and lowercasing.
- `pnpm vitest run tests/unit/write-contract-core.test.ts tests/unit/approve-token.test.ts tests/unit/transfer-token-core.test.ts`:
  pass, with an added assertion that the field reaches the write result and so the status response.
- `pnpm check`, `pnpm type-check`, `pnpm check:api-docs`: clean.
- Motivating integration: https://github.com/Prashant-thakur77/almanak-keeperhub, which currently recovers the
  same address by decoding Transfer/Approval/Deposit/Withdraw arguments per event signature. That is the
  workaround this removes.

**Checklist**

- [x] Targets `staging`
- [x] `pnpm check` passes
- [x] `pnpm type-check` passes
- [x] Tests added
- [x] Docs updated
