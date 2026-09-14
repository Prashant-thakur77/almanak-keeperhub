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

## On #2427

Thanks @subheeksh5599 - and yes please to that offer. If you run the per-chain `eth_simulateV1` probe and post
the results here, that is the measurement the implementation turns on, whoever writes it.

I am not going to claim this one before the hackathon deadline on Friday: it is the largest of the three and I
would rather leave it open for someone who can do it properly than hold it and ship a sequence that quietly
falls back to per-call estimates. If it is still open after Friday I will pick it up.

## In Discord, replying to Luca

Thanks - both PRs are up: #2449 for #2426 (raw calldata, with the re-encode-and-compare that suisuss asked for) and
#2428 (the traced frame's `from`). #2427 I have left open rather than hold it; it needs the per-chain
eth_simulateV1 measurement first and I would rather not rush it before Friday.

Filing the bounty BUIDL separately today - thanks for the heads-up that it stacks. See you at office hours.
