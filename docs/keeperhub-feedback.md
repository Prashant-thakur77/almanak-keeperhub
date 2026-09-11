# KeeperHub feedback from building the Almanak backend

Collected while integrating KeeperHub as Almanak's `Signer` / `Submitter` / `Simulator`.
Each item has a repro. Items marked "filed" have an issue number; the rest are
ready to file once we have confirmed them against the hosted app.

## 1. No raw-calldata write for EVM (biggest gap for framework integrations)

`POST /api/execute/contract-call` needs `functionName`, `functionArgs` and an `abi`
(or an explorer-verified contract). Every strategy framework (Almanak, Wayfinder,
AgentKit) hands its executor an already-encoded `data` field, so each integration
has to re-decode calldata before it can call KeeperHub. This package ships a
selector index for that, and refuses unknown selectors.

Repro: build any tx with `data=0x6e553f65...` (ERC-4626 `deposit`) and try to submit
it without naming the function. There is no endpoint that accepts it.

Ask: a `POST /api/execute/raw-call` (`to`, `data`, `value`, `chainId`, `simulate`)
that runs the same caps and simulate path and decodes `data` server-side for the
audit trail (the `web3/decode-calldata` action already exists). Solana already has a
raw-instruction action (PR #1792); EVM does not.

## 2. Whole-workflow dry run is documented as roadmap

`docs/agent/mcp-test-workflow.md` says workflow dry-run "is on the roadmap", while
the hackathon brief says "you dry run it without touching the chain". Direct
execution `simulate` covers single calls only, and protocol actions ignore
`simulate` (documented; issues #2004, #1959, #1929). We rely on per-call simulate
and say so in the README.

## 3. Bundle simulation cannot chain calls

`simulate: true` runs `estimateGas` + `call` against live state. An approve
followed by a deposit therefore cannot be simulated as a bundle: the deposit
reverts on allowance until the approve lands. Almanak's own LocalSimulator has the
same limit and estimates only the first tx of a bundle; this package mirrors that
and reports the rest as warnings.

Ask: accept an array of calls in one simulate request and apply them sequentially
with state overrides (Tenderly-style), or expose `eth_call` state overrides.

## 4. Sponsored transactions show the relayer as `from`

Documented in `wallet-management/onchain-appearance.md`. For a strategy framework
that reads receipts to track positions, the `from` field is wrong; we rely on the
event's `owner` argument instead. A `sponsored` boolean is already in the status
body, which is enough, but a `sender` field carrying the org wallet next to
`transactionHash` in the 202 envelope would remove the ambiguity.

## 5. Docs link in the hackathon brief redirects

The brief links `https://docs.keeperhub.com/ai-tools/mcp-server`, which 308s to
`/agent/mcp-server`. Minor.

## 6. `network` vs `chainId` precedence differs per route

Documented in direct-execution.md: `contract-call` prefers `network`, `transfer`
prefers `chainId`. This package only ever sends `chainId`.

## Items to confirm on the hosted app (not yet reproduced)

- Whether `GET /api/user` returns `walletAddress` for an org API key without a
  step-up session (docs say profile reads accept `kh_` keys).
- Whether tuple arguments in `functionArgs` are accepted as nested JSON arrays.
- Whether the free plan's `$1/month` gas credit sponsors a full demo run on Base.
