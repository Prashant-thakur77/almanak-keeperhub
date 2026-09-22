# Changelog

All notable changes to `almanak-keeperhub`. Versions follow semver; the proof on the site is regenerated
independently of releases, by the runner, every six hours.

## 1.1.0 — 2026-09-23

The guarded exit as a KeeperHub workflow, and the operator surfaces around it.

- `almanak-keeperhub exit-guard deploy|run|status|show`: the exit decision expressed in KeeperHub's own
  builder. A Manual trigger carries the decision, a `web3/check-token-balance` node reads the wallet's vault
  shares, a Condition compares them with the decision, and a Morpho `vault-redeem` sits behind the true
  branch. `deploy` creates and validates it through the workflows API; `run` triggers it with the decision as
  input, follows the execution, and prints the node trace. `--stale` asks for one share more than the
  position holds, so the Condition stops the run and nothing is broadcast; `--expect-shares N` sends any
  decision. Runs are remembered in `keeperhub-exit-guard.json` next to the strategy.
- Telegram: `/guard` (armed, then `/confirm`) runs that workflow; `/guard stale` runs the stale decision with
  no confirmation, since nothing can move. Replies show the node trace and the steps completed.
- `keeper.run_now` accepts workflow input; the returned status carries `node_statuses` and `progress`.
- Console: the keeper card lists the guarded-exit workflow next to the compounder, with every manual run,
  the shares decided, the shares KeeperHub observed, the verdict and the transaction.
- Proof runner: passes both workflow state files to the console export, and writes shields endpoint badges
  (`docs/badges/`) from the same state the console shows.
- `almanak-keeperhub --version` reports the package version (it said 0.1.0 before); a test keeps it equal to
  `pyproject.toml`.
- The finalist panel deck under `docs/panel/` and the diagrams in `docs/img/`.

Upstream state at this release: KeeperHub PRs #2449, #2452 and #2450 merged and live on production; #2531,
#2532, #2533 and #2534 in review; almanak-co/sdk#3 proposed.

## 1.0.0 — 2026-09-16

The hackathon submission, on PyPI.

- Almanak's `Signer`, `Simulator` and `Submitter` implemented against KeeperHub's direct execution API:
  dry run of the exact compiled calldata, one idempotency key per intent, verified receipts handed back as
  Almanak `TransactionReceipt`s, no private key on the machine.
- The wallet registry plugin (`almanak.wallets`) and the gateway servicer that installs the backend; the
  `almanak.execution_backends` entry point proposed upstream.
- `base_sepolia` as a first-class Almanak chain, so the unmodified demo strategy runs on the free path.
- The guarded exit as `check-and-execute`; the scheduled compounder workflow generated from the strategy
  config; the Telegram operator bot; the MCP server; the execution console and its static export; seven
  failure-mode demos; the benchmark; the proof runner.
