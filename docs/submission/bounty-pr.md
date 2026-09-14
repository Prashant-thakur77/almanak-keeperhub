# Pull request for issue #2426 (accepted 14 Sep 2026)

Branch: `feat/raw-calldata-contract-call`, one commit `d1bb2ea9a`, rebased on `staging` 28233554f, pushed to
https://github.com/Prashant-thakur77/keeperhub. Base: `KeeperHub/keeperhub:staging`.

Open it at:
https://github.com/Prashant-thakur77/keeperhub/pull/new/feat/raw-calldata-contract-call

Title (the pr-title-check workflow enforces conventional commits and the issue-link check needs the number):

`feat: #2426 accept raw calldata on POST /api/execute/contract-call`

## Body (follows .github/PULL_REQUEST_TEMPLATE.md)

**Issue**

Closes #2426. Built to the shape triage accepted, not to the shape filed: the three differences are the schema
change, the re-encode comparison, and refusing a body that carries both `data` and a function key.

**What this changes**

`POST /api/execute/contract-call` accepts `data` (hex calldata) in place of `functionName` and `functionArgs`.

`contractCallInputSchema` is where it lands, as you said it had to be: `data` is validated there (0x-prefixed
hex, whole bytes, four bytes or more), and `hasFunctionNameInput` becomes conditional on its absence. The
`functionNameConflict` guard that sits directly after it is untouched and still runs on every request.

The route then decodes `data` against the ABI in the body, or the explorer-verified ABI it already fetches when
`abi` is omitted, into the canonical function key (`fragment.format("sighash")`) and a typed `functionArgs`
array, and takes the existing read, `simulate` and write paths unchanged. Every guard that keys on the function
and its arguments - the stablecoin ceiling, the payable check, the daily value cap, idempotency, the execution
record - keeps reading a named function with typed arguments.

**The decode is lossless or it is refused.** The broadcast is rebuilt from the function key and the decoded
arguments (`lib/web3/chain-adapter/evm.ts`), not from the bytes that arrived, so the decoded call is re-encoded
and compared against the submitted `data`; trailing bytes past the arguments (an ERC-2771 appended sender,
for instance), non-minimal offsets and non-canonical padding answer `400` on field `data` instead of being
dropped silently.

**Nothing is guessed.** Decoding runs against the caller-supplied ABI alone. `decodeCalldata` is not used and
is not reachable from this path, so no 4byte.directory read and no selector-only result can put a guessed
signature on the signing path. A test asserts that no network call is made when the ABI lacks the selector.

One departure from the issue as filed: a body carrying both `data` and `functionName` (or `abiFunction`) is now
`400` on field `data` rather than letting the typed fields win. It describes the same call twice, and the
decode would otherwise quietly overrule the name that was typed - the same reasoning as the existing
`functionNameConflict` guard. Say the word and I will make the typed fields win instead; it is a four-line change.

**Scope**

- `app/api/execute/_lib/schemas.ts`: `data` validation, `functionName` conditional on its absence, both-keys conflict.
- `app/api/execute/_lib/raw-calldata.ts` (new): selector check, ABI decode, re-encode comparison, argument rendering
  (decimal strings for integers, hex for bytes, arrays for arrays, objects keyed by component name for tuples - the
  shapes `reshapeArgsForAbi` and `coerceArgsForAbi` already accept).
- `app/api/execute/contract-call/route.ts`: one block after validation; the rest of the route is untouched.
- `docs/api/direct-execution.md`: "Raw calldata" subsection, and `specs/api-coverage.json` regenerated with
  `pnpm check:api-docs` so `pr-checks.yml` does not fail on stale coverage.
- Not changed: `check-and-execute` (out of scope, as agreed), `transfer`, protocol actions, `/api/execute/node`,
  the workflow `web3/write-contract` node, the MCP tool schema.

**How verified**

- `pnpm vitest run tests/unit/execute-raw-calldata.test.ts tests/unit/contract-call-raw-calldata.test.ts`: 26 tests.
  They cover the decoder (approve, tuple/array/bytes/bool rendering, unknown selector, undecodable calldata, invalid
  ABI, trailing bytes, non-canonical padding, no network call on an ABI miss), the schema (functionName conditional,
  conflict guard intact, malformed `data`, both keys present) and the route (write path receives the canonical key
  and typed args, explorer ABI fallback, `simulate: true` takes the simulate path, a decoded view function takes the
  read path, unknown selector is a 400 before any execution, unresolvable ABI is a 400 on `abi`, body validation runs
  first, re-encode mismatch is a 400 before any execution).
- `pnpm vitest run tests/unit`: 23,297 pass. The two failures on this tree - `workflow-directive-detection`
  (`glob(...) is not a function`) and `agentic-wallet-rate-limit` (a clock-boundary assertion) - reproduce on a clean
  `origin/staging` checkout and are not from this change.
- `pnpm check`, `pnpm type-check`, `pnpm check:api-docs`: clean.
- Motivating integration: https://github.com/Prashant-thakur77/almanak-keeperhub, where Almanak's compiled
  transactions reach KeeperHub as calldata and the workaround this removes is a 339-signature offline selector table.

**Checklist**

- [x] Targets `staging`
- [x] `pnpm check` passes
- [x] `pnpm type-check` passes
- [x] Tests added
- [x] Docs updated
