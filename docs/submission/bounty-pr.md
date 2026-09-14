# PR for issue #2426 - paste-ready

Title (already correct on your screen):

`feat: #2426 accept raw calldata on POST /api/execute/contract-call`

Clear the description box (it still holds the commit message and the template comments) and paste everything
between the lines below. The checkboxes are already ticked. Delete the `## Screenshots` section - the change
renders nothing.

---BEGIN---

## Issue

Closes #2426.

## What this changes

You can now send `data` instead of `functionName` and `functionArgs`. If you already have encoded calldata,
you no longer have to decode it just so we can encode it again.

The decode happens in the route, against the ABI in the body or the explorer ABI we already fetch when `abi`
is omitted. After that it is an ordinary typed call, so the stablecoin cap, the payable check, the daily value
cap, idempotency and the execution record all see a named function with typed arguments, exactly as before.

Three things are worth calling out:

**The schema is what changed, not just the route.** `contractCallInputSchema` bails on a missing
`functionName` before `resolveAbiForRequest` ever runs, so `data` is validated there - 0x-prefixed hex, whole
bytes, at least four - and `functionName` is only required when `data` is absent. The `functionNameConflict`
guard right after it is untouched and still runs on everything.

**The decode has to be lossless.** We broadcast a transaction rebuilt from the decoded arguments, not the bytes
that arrived, so anything the decode does not represent would quietly disappear - trailing bytes, non-minimal
offsets, odd padding. A contract reading raw `msg.data` would then run something other than what was sent. So
the decoded call is re-encoded and compared against `data`, and a mismatch is a 400 rather than a silent fix-up.

**Nothing is guessed.** Only the caller's ABI is used. `decodeCalldata` is not involved, so its 4byte fallback
and its selector-only result cannot put a guessed signature in front of the signer. A selector the ABI does not
have is a 400 on `data`.

If a request carries both `data` and a function key, the typed fields win and `data` is ignored (it is still
shape-checked). `isRawCalldataRequest` uses the same non-empty-string test the schema does, so the two can
never disagree about which path a body takes.

Nothing else moves: `check-and-execute` stays out as scoped on the issue, and `transfer`, protocol actions,
`/api/execute/node`, the workflow `web3/write-contract` node and the MCP tool schema are untouched. No new
dependency, no changed default, no migration. A request without `data` behaves exactly as it did.

## Scope

One change. The schema on its own would accept bodies the route cannot execute; the route on its own would
never be reached, because the schema rejects those bodies first.

`specs/api-coverage.json` is in the diff only because the new docs section shifts three recorded line numbers.
It is the regenerated `pnpm check:api-docs` artifact, which `pr-checks.yml` needs or it fails on stale coverage.

## How it was verified

28 tests across `tests/unit/execute-raw-calldata.test.ts` and `tests/unit/contract-call-raw-calldata.test.ts`.

The decoder ones cover approve, how tuples/arrays/bytes/bools get rendered into the shapes `reshapeArgsForAbi`
already accepts, unknown selectors, undecodable calldata, a bad ABI, trailing bytes and non-canonical bool
padding. One spies on `fetch` and asserts we make no network call when the ABI lacks the selector.

The schema ones check that `functionName` is conditional, that the `functionName`/`abiFunction` conflict guard
still fires, and that malformed `data` is rejected on field `data` either way.

The route ones check that the write core gets the canonical key and typed args, the explorer ABI fallback,
`simulate: true` going through simulate, a view function going to the read path, and that an unknown selector,
an unresolvable ABI and a re-encode mismatch all answer 400 before anything executes.

If you revert the route block, the write, read and simulate tests fail. Revert the schema change and every raw
request fails on the old `functionName` error. Drop the re-encode comparison and the trailing-bytes and padding
cases go through - which is the one that would actually broadcast something other than what was sent.

`pnpm vitest run tests/unit` gives 23,297 passing. Two files fail on my tree -
`workflow-directive-detection` (`glob(...) is not a function`) and `agentic-wallet-rate-limit` (a clock-boundary
assertion) - and both reproduce on a clean `origin/staging` checkout, so they are not from this.
`pnpm check`, `pnpm type-check` and `pnpm check:api-docs` are clean.

For context on where this came from: https://github.com/Prashant-thakur77/almanak-keeperhub, an Almanak
integration where the strategy hands over compiled transactions as calldata. The workaround this removes is a
339-signature offline selector table doing the decoding outside your audited path.

---

- [x] Targets `staging`
- [x] Title carries the issue number, or an exemption applies
- [x] `pnpm check` and `pnpm type-check` pass
- [x] No secrets, `.env` files, or credentials committed

---END---
