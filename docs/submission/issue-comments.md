### Comment to post on issue #2426 (after opening the PR)

@subheeksh5599 apologies for the collision - we posted within six minutes of each other and I did not see
your claim until after. I had this written before the label landed: the branch has been up at
Prashant-thakur77/keeperhub:feat/raw-calldata-contract-call since 12 Sep and I said in the issue I would open
the PR once it was accepted, so I have done that rather than leave the work sitting.

PR: <PASTE PR LINK>

It is built to @suisuss's shape, not the one I filed: `data` validated in `contractCallInputSchema` with
`functionName` conditional on its absence and `functionNameConflict` left intact, decoding against the
caller-supplied ABI alone, re-encode-and-compare with a 400 on mismatch, and a test asserting no network call
on an ABI miss. One judgement call I would flag: a body with both `data` and `functionName` is now a 400
rather than the typed fields winning, because it describes the same call twice.

Review it as you would anyone's - if you would rather take it from here, say so and I will close mine.
I am not taking #2427 or #2428; both are yours.

---

### Comment to post on issue #2427

@subheeksh5599 this one is yours - I am not working on it and will not open a PR against it. Measuring
`eth_simulateV1` per chain before writing the code is the right call.

One datapoint from filing it, in case it saves you time: the case that made me write the issue is
approve-then-deposit on Base Sepolia, where the deposit's dry run answers "ERC20: transfer amount exceeds
allowance" whenever it is simulated before the approve lands. Test vault
0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04, test USDC 0x036CbD53842c5426634e7929541eC2318f3dCF7e - a
two-call sequence there reproduces it in one request. Happy to answer anything about the reported behaviour.

---

### Comment to post on issue #2428

@subheeksh5599 yours too - I am not opening a PR against this one.

For what it is worth I agree with both of you against my own filing: `sender` as I wrote it names the wrong
address under Safe routing, and carrying the matched frame's `from` through `decodeExecutedCall` is the right
shape. It is the field the Almanak integration wants; it currently recovers the same address by decoding
Transfer/Approval/Deposit/Withdraw arguments per event signature, which is exactly the workaround that would go away.
