# Comments to post, after each PR is open

## On #2426 (post after opening the raw-calldata PR)

@subheeksh5599 apologies for the collision - we posted within six minutes of each other and I did not see your
claim until after. The branch has been up at `Prashant-thakur77/keeperhub:feat/raw-calldata-contract-call`
since 12 Sep and I said in the issue I would open the PR once it was accepted, and Luca asked me in Discord to
open it now rather than wait, so here it is.

PR: <PASTE PR LINK>

Built to @suisuss's shape: `data` validated in `contractCallInputSchema` with `functionName` conditional on its
absence and `functionNameConflict` left intact, decoding against the caller-supplied ABI alone, re-encode and
compare with a 400 on mismatch, and a test asserting no network call on an ABI miss. A body carrying both
`data` and a function key keeps the filed behaviour - the typed fields win - with the route and the schema
applying the same test for "a function key was sent" so they cannot disagree about which path a body takes.

Review it as you would anyone's. If you would rather take it from here, say so and I will close mine.

## On #2428 (post after opening the second PR)

@subheeksh5599 Luca asked me in Discord to send this one as its own PR, so I have - sorry to cross over you on
it again. PR: <PASTE PR LINK>

It is the shape you and @suisuss converged on, not the one I filed: the matched frame's `from` on
`ExecutedCall`, no top-level `sender`. You were both right that `sender` as I wrote it names the wrong address
under Safe routing. Tests cover the relayer, delegated and Safe-routed cases plus lowercasing; docs go under
"Sponsored Executions" with the best-effort caveat, and `specs/api-coverage.json` is regenerated.

Same offer as on #2426: if you would rather own this one, say so and I will close mine.

## On #2427 (post any time)

@subheeksh5599 this one is yours - I am not working on it and will not open a PR against it. Measuring
`eth_simulateV1` per chain before writing the code is the right call.

One datapoint from filing it, in case it saves you time: the case that made me write the issue is
approve-then-deposit on Base Sepolia, where the deposit's dry run answers "ERC20: transfer amount exceeds
allowance" whenever it is simulated before the approve lands. Test vault
0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04, test USDC 0x036CbD53842c5426634e7929541eC2318f3dCF7e - a two-call
sequence there reproduces it in one request. Happy to answer anything about the reported behaviour.

## In Discord, replying to Luca

Thanks - both PRs are up: #2426 (raw calldata, built to the re-encode-and-compare shape suisuss asked for) and
#2428 (the traced frame's `from`). #2427 I have left to @subheeksh5599, who claimed all three this morning;
they have the eth_simulateV1 measurement plan and it is the right one.

Filing the separate bounty BUIDL today - thanks for the heads-up that it stacks. See you at office hours.
