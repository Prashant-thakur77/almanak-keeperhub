# Issue to open on github.com/KeeperHub/keeperhub (before the bounty PR)

Title: `feat: accept raw calldata on POST /api/execute/contract-call`

Labels you cannot set; the maintainers add `accepted`. Wait for it before pushing the PR.

---

### Before filing

- [x] I searched existing issues. Closest: #1792 (raw Solana instruction write, merged), which has no EVM equivalent. Nothing open asks for EVM calldata.
- [x] I checked the behaviour on `staging` at `caa60d103`.
- [x] This is one change: one route, one new field, no sibling routes altered.

### Reason

Every execution framework hands its executor an already-encoded `data` field. Almanak's `Submitter.submit()` receives `SignedTransaction.raw_tx` built from `UnsignedTransaction.data` (`almanak/framework/execution/interfaces.py`); Wayfinder's `send_transaction(transaction, sign_callback)` receives `transaction["data"]` (`wayfinder_paths/core/utils/transaction.py`); Coinbase AgentKit's `EvmWalletProvider.sendTransaction(TransactionRequest)` receives `data` (`typescript/agentkit/src/wallet-providers/evmWalletProvider.ts`). None of them know a function name.

`POST /api/execute/contract-call` requires `functionName` and `functionArgs` (docs/api/direct-execution.md, "Call Smart Contract"), and there is no EVM endpoint that takes calldata. Reproduced on app.keeperhub.com on 12 Sep 2026: a body with `data` and no `functionName` answers `HTTP 400 {"error":"Missing required field","field":"functionName","details":"functionName is required and must be a non-empty string"}`. So every integration re-decodes calldata client-side before it can call KeeperHub. I built exactly that for the Almanak integration (an offline selector index over the ABIs Almanak ships, refusing unknown selectors) and it is the single largest piece of the integration, duplicated by every other framework connector. The expectation that KeeperHub would take calldata comes from the Solana side, where `web3/send-raw-solana-instruction` exists, and from `web3/decode-calldata`, which shows the platform already knows how to turn bytes into a named call.

Cost of the workaround: each integration keeps its own selector table, and a strategy whose connector uses a selector the table lacks is refused or, worse, guessed.

### Scope

- In: `POST /api/execute/contract-call` accepts `data` (hex calldata) in place of `functionName` + `functionArgs`. Decoding uses the `abi` in the body or the explorer-verified ABI the route already fetches when `abi` is omitted. The decoded call then follows the existing read, `simulate` and write paths unchanged, so the stablecoin cap, payable check, spending caps, idempotency and the execution record all see a named function and typed arguments.
- In: docs paragraph under "Call Smart Contract"; unit tests for the decoder and for the route.
- Out: `check-and-execute` (its `action` object could take `data` the same way; separate issue if wanted). Out: 4byte or any signature-database lookup; a selector the ABI does not contain is a 400 on field `data`. Out: contract creation (`to`-less calldata). Out: the workflow `web3/write-contract` node.
- Checked and unaffected: `/api/execute/transfer`, protocol actions, `/api/execute/node`, the MCP `execute_contract_call` tool (it can pass `data` through once the route accepts it, not changed here).

### Plan

1. `app/api/execute/_lib/raw-calldata.ts`: `isRawCalldataRequest(body)`, `selectorOf(data)`, `resolveRawCalldata(data, abi)` returning the canonical function key (`fragment.format("sighash")`, the same spelling `resolveAbiFunction` matches canonically) and a `functionArgs` JSON array string (decimal strings for integers, hex for bytes, nested arrays for tuples and arrays, the shape `reshapeArgsForAbi` and `coerceArgsForAbi` already accept).
2. `app/api/execute/contract-call/route.ts`: when `data` is present and no function key is, validate the rest of the body with the existing schema, resolve the ABI with the existing `resolveAbiForRequest`, decode, and set `functionName`, `functionArgs`, `abi` on the body. Everything after that line is untouched. If both `data` and `functionName` are sent, the typed fields win.
3. Tests: `tests/unit/execute-raw-calldata.test.ts` (decoder: approve, tuple/array/bytes/bool rendering, unknown selector, undecodable calldata, invalid ABI) and `tests/unit/contract-call-raw-calldata.test.ts` (route: write path receives the canonical key and typed args, explorer ABI fallback, `simulate: true` goes through the simulate path, a decoded view function takes the read path, unknown selector is a 400 on `data` before any execution, unresolvable ABI is a 400 on `abi`, body validation still runs first, typed fields win over `data`).
4. Docs: a "Raw calldata" subsection in `docs/api/direct-execution.md`.

The change is written and passing `pnpm check`, `pnpm type-check` and the unit tests on a branch; I will open the PR referencing this issue once it is accepted.
