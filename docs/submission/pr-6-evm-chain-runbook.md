# PR 6 - EVM chain runbook and explorer-config guard (#2497)

Branch `docs/add-evm-chain-runbook` in /home/prashant/KeeperHub/keeperhub, one commit `594ccb9df`, off
`staging` 978b916aa. LOCAL ONLY until you say push.

Order of operations:
1. Post the "taking this" comment on #2497 (drafted earlier). suisuss opened it "on ourselves", so say you are
   picking it up and that the guard replaces the warn as the plan asks.
2. Say push. Open the PR with the body below. Title:
   `docs: #2497 add the EVM chain runbook and fail the seed on a chain with no explorer config`.
   Target `staging`. The type is `docs` but the title still references the issue, so `check-issue-link`
   needs `accepted` on #2497.

## Body

## Issue

Closes #2497.

## What this changes

Four chain requests each re-derived the procedure and each missed a different step, because the procedure
lived in a maintainer-only slash command that itself omitted six touchpoints. This is the plan from the
issue, all three parts.

**The runbook.** `specs/adding-an-evm-chain.md`, contributor-facing and linked from `CONTRIBUTING.md`
(new "Adding a Chain" section) and `ISSUES.md` (a new chain now appears in the list of changes that need an
issue). Nine steps, each naming the file, what the entry is for, and what fails when it is missing:
`rpc-config.ts` (and why `getRpcUrlByChainId` throwing makes it load-bearing), the three places in
`seed-chains.ts`, the WSS decision and what a null one costs (the event tracker silently refuses Event
triggers), `plugins/blockscout/chains.ts`, `gas-strategy.ts` overrides, the independent token list,
`seed-tokens.ts`, name aliases in `network-utils.ts` plus `docs/api/chains.md` with the
`check:api-docs` coupling, and the display-name / scanner constants that usually need nothing. It states
the keeperhub-first-then-chain-config ordering and that the chain-config half is maintainer-only, so a
contributor knows where their pull request ends. It closes with a verification block, a checklist for the
PR description, and the maintainer steps after merge. `.claude/commands/add-chain.md` now points at it and
carries the steps it omitted.

**The guard.** `seed-chains.ts` exports `DEFAULT_CHAINS`, `EXPLORER_CONFIG_TEMPLATES`, the name-to-id map
(hoisted to module level as `CHAIN_TO_DEFAULT_ID`) and a pure `buildExplorerConfigs`. A chain with no map
entry or no template now throws instead of `console.warn` and exit zero. `seedChains()` runs only under
`require.main === module` (the idiom `check-api-docs-routes.ts` documents), so the test can import the
catalogue without a database. `tests/unit/seed-chains-explorer-coverage.test.ts` (78 cases) walks every
`DEFAULT_CHAINS` name into the map, every map value into a template and into `CHAIN_CONFIG`, asserts no
orphan map rows, and asserts the two throw paths and that the config is keyed by the chain's resolved id
rather than the default one.

**The dedup.** `INDEPENDENT_TOKEN_LIST_CHAIN_IDS` was two hand-synced copies (`components/overlays/wallet/
chain-utils.ts` and `app/api/supported-tokens/route.ts`). It is now one exported set in `lib/chain-utils.ts`,
read by both; the component module re-exports `hasIndependentTokenList` so its existing importer is
unchanged. The set's contents are the same three ids.

No chain is added or changed. The seed's behaviour on today's catalogue is identical: all 24 chains
resolve, so the throw path is unreachable until someone adds a chain and misses the map.

## Scope

One change. The runbook describes the guard and the single set by name, and the guard is what makes the
runbook's step 2 true, so the three parts are not separable without leaving the documentation wrong.

## How it was verified

- `pnpm vitest run tests/unit/seed-chains-explorer-coverage.test.ts`: 78 pass on the current catalogue.
  Removing any `CHAIN_TO_DEFAULT_ID` row fails the matching case.
- `DATABASE_URL=<unreachable> pnpm tsx scripts/seed/seed-chains.ts` still executes the seed when run
  directly (reaches the first query and fails on the connection), so the main guard does not disable it.
- `pnpm vitest run tests/unit/agentic-wallet-workflow-binding.test.ts tests/unit/chain-display.test.ts
  tests/unit/chain-service.test.ts`: 66 pass.
- `pnpm check`, `pnpm type-check`, `pnpm check:api-docs` (no drift; `docs/api/chains.md` is not edited).
