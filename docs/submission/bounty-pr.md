# PR for issue #2426 - paste-ready

Title (already correct on your screen):

`feat: #2426 accept raw calldata on POST /api/execute/contract-call`

Clear the description box completely (it currently holds the commit message and the template comments) and
paste everything between the lines below. Then tick all four checkboxes.

---BEGIN---

## Issue

Closes #2426.

## What this changes

`POST /api/execute/contract-call` accepts `data` (hex calldata) in place of `functionName` and `functionArgs`,
so callers that already hold encoded bytes - execution frameworks, transaction builders, replayed transactions -
do not have to decode them only for the route to re-encode.

`contractCallInputSchema` is where it lands. It short-circuits on a missing `functionName` before
`resolveAbiForRequest` is ever reached, so `data` is validated there - 0x-prefixed hex, whole bytes, four bytes
or more - and `hasFunctionNameInput` becomes conditional on its absence. The `functionNameConflict` guard that
sits directly after it is untouched and still runs on every request.

The route then decodes `data` against the ABI in the body, or the explorer-verified ABI it already fetches when
`abi` is omitted, into the canonical function key (`fragment.format("sighash")`) and a typed `functionArgs`
array, and takes the existing read, `simulate` and write paths unchanged. Every guard that keys on the function
and its arguments - the stablecoin ceiling, the payable check, the daily value cap, idempotency, the execution
record - keeps reading a named function with typed arguments rather than opaque bytes.

**The decode is lossless or it is refused.** The broadcast is rebuilt from the function key and the decoded
arguments (`lib/web3/chain-adapter/evm.ts`), not from the bytes that arrived, so the decoded call is re-encoded
and compared against the submitted `data`. Trailing bytes past the arguments (an ERC-2771 appended sender, for
instance), non-minimal offsets and non-canonical padding answer `400` on field `data` instead of being dropped
silently and executing something other than what was sent.

**Nothing is guessed.** Decoding runs against the caller-supplied ABI alone. `decodeCalldata` is not used and is
not reachable from this path, so neither its 4byte.directory fallback nor its selector-only result can put a
guessed signature on the signing path. A selector the ABI does not contain is a `400` on field `data`.

A body carrying both `data` and a function key is a typed request: the typed fields win and `data` is ignored,
though it is still shape-checked. `isRawCalldataRequest` applies the same non-empty-string test as
`hasFunctionNameInput`, so the route and the schema cannot disagree about which path a body takes.

Not touched: `check-and-execute` (out of scope, as agreed on the issue), `/api/execute/transfer`, protocol
actions, `/api/execute/node`, the workflow `web3/write-contract` node, the MCP tool schema. No new dependency,
no changed default, no migration. A request that omits `data` is byte-for-byte what it was before.

## Scope

One change. The schema change alone would accept a body the route cannot execute; the route change alone would
never be reached, because the schema rejects the body first. They cannot ship separately.

`specs/api-coverage.json` moves only because the new docs subsection shifts three recorded line numbers; it is
the regenerated artifact from `pnpm check:api-docs`, needed or `pr-checks.yml` fails on stale coverage.

## How it was verified

`pnpm vitest run tests/unit/execute-raw-calldata.test.ts tests/unit/contract-call-raw-calldata.test.ts` - 28 tests.

Decoder: approve, tuple/array/bytes/bool rendering into the shapes `reshapeArgsForAbi` and `coerceArgsForAbi`
accept, unknown selector, undecodable calldata, invalid ABI, trailing bytes, non-canonical bool padding, and a
`fetch` spy asserting no network call when the ABI lacks the selector.

Schema: `functionName` required only when `data` is absent, the `functionName`/`abiFunction` conflict guard
still firing, malformed `data` rejected on field `data`, and `data` still shape-checked when the typed fields
are the ones used.

Route: the write core receives the canonical key and typed args, explorer ABI fallback, `simulate: true` takes
the simulate path, a decoded view function takes the read path, an unknown selector is a `400` before any
execution, an unresolvable ABI is a `400` on `abi`, body validation still runs first, a re-encode mismatch is a
`400` before any execution, the typed fields win when both are sent, and an empty function key still takes the
raw path.

What they would catch: reverting the route block fails the write, read and simulate tests; reverting the schema
change fails every raw-calldata request with the old `functionName` error; removing the re-encode comparison
lets the trailing-bytes and padding cases through, which is the case that would broadcast something other than
what was sent.

Also run: `pnpm vitest run tests/unit` - 23,297 pass. Two failures on this tree, `workflow-directive-detection`
(`glob(...) is not a function`) and `agentic-wallet-rate-limit` (a clock-boundary assertion), reproduce on a
clean `origin/staging` checkout and are not from this change. `pnpm check`, `pnpm type-check` and
`pnpm check:api-docs` are clean.

The integration this came out of is https://github.com/Prashant-thakur77/almanak-keeperhub, where Almanak's
compiled transactions reach KeeperHub as calldata; the workaround it removes is a 339-signature offline
selector table sitting outside KeeperHub's audited path.

---

- [x] Targets `staging`
- [x] Title carries the issue number, or an exemption applies
- [x] `pnpm check` and `pnpm type-check` pass
- [x] No secrets, `.env` files, or credentials committed

---END---

Delete the `## Screenshots` section: the change renders nothing, and the template says to.
