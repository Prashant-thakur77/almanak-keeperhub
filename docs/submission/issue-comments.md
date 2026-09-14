# Comments to post (updated 14 Sep, after @subheeksh5599 stood down on all three)

## On #2426, with the PR open

Thanks @subheeksh5599, that is a generous way to handle it and I appreciate it. For what it is worth your
claim was properly made; there was nothing on the issue saying I was implementing it until I said so.

PR: #2449 (opened 14 Sep)

Built to @suisuss's shape: `data` validated in `contractCallInputSchema` with `functionName` conditional on
its absence and `functionNameConflict` left intact, decoding against the caller-supplied ABI alone, re-encode
and compare with a 400 on mismatch, and a test asserting no network call on an ABI miss. A body carrying both
`data` and a function key keeps the filed behaviour - the typed fields win - with the route and the schema
applying the same non-empty-string test for "a function key was sent", so they cannot disagree about which
path a body takes.

You offered to take the schema change and the ABI-miss test; both are in the PR, so there is nothing to hand
over there. A review from you would be worth more to me than the split would have been, if you have the time.

## On #2428, with the PR open

PR: <PASTE PR LINK>

The shape you and @suisuss converged on, not the one I filed: the matched frame's `from` on `ExecutedCall`,
no top-level `sender`. You were both right that `sender` as I wrote it names the wrong address under Safe
routing. Tests cover the relayer case (the frame's address, not the transaction's), the delegated case, the
Safe-routed case and lowercasing; docs go under "Sponsored Executions" with the best-effort caveat, and
`specs/api-coverage.json` is regenerated so `pr-checks.yml` does not fail on stale coverage.

One thing I left out deliberately: the `executedCall.*` output fragments in `plugins/field-fragments.ts`.
Exposing `from` there is four lines, but the issue scoped workflows out. Happy to add it if wanted.

## On #2427, with the PR open

PR: <PASTE PR LINK>

Thanks @subheeksh5599 - I did end up taking this one after all, so the offer to run the probe is no longer
needed, but it was a good one.

Built on the measurement rather than the assumption: before writing anything I checked `eth_simulateV1` over
`[approve(spender, 1000), allowance(owner, spender)]` on Base Sepolia and got `0x3e8` back from the second
call, where the same pair through `eth_call` reads zero - the behaviour @suisuss reported. A reverting call
comes back `status: "0x0"` with the revert bytes under `error.data`, and the calls after it still execute, so
one bad call in the middle does not cost the answer for the rest.

Nodes without it replay each call's `debug_traceCall` state diff as an `eth_call` override for the next one,
and a node offering neither reports the later calls as `unavailable` rather than silently answering against
latest state. The mechanism is named in the response and cached per chain.

One open question in the PR body: I applied the stablecoin ceiling per call, since each is its own transaction
at broadcast, and did not add a bundle-level total - the over-cap approval policy lives in `decide()` and a
second summing definition beside it looked more likely to drift than to help. If the summed figure has to be
refused too, say so and I will add it there.

## In Discord, replying to Luca (post after all three PRs are open)

Thanks Luca, that is useful on both counts.

All three PRs are up: #2449 for #2426 (raw calldata), #2450 for #2428 (the acting wallet), and #<N> for #2427
(the call sequence). #2426 and #2428 are built to the shapes suisuss specified in the issues rather than the
ones I filed - the re-encode-and-compare on the first, the traced frame's `from` on the second.

I did take #2427 in the end. Before writing it I checked eth_simulateV1 on Base Sepolia and got the second
call reading the first call's state, so the mechanism suisuss named is measured rather than assumed, and nodes
without it fall back to trace-derived state overrides rather than to per-call estimates.

If it helps: #2450 conflicts with nothing but the generated api-coverage artifact, so it is the cheapest to
take first. #2449 and #2427 both touch the contract-call schema and route, so whichever goes second I will
rebase - happy to do that in whatever order suits you.

Filing the separate bounty BUIDL today, thanks for the heads-up that it stacks. See you at office hours.
