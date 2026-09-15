# Almanak feedback (almanak 2.28.0 from PyPI)

Found while making KeeperHub an execution backend. Both are reproducible with the
2.28.0 package alone; neither needs KeeperHub.

Checked against `main` on 15 Sep 2026 (commit f98b7896): both are fixed there. Bug 1 by
`87428eaa` "fix(gateway): keep token discovery on owned async paths" (9 Sep), which passes
`skip_gateway=True` from `LivePriceSource._build_token_pair_map`; bug 2 by the runner now
building its `ExecutionContext` with `simulation_enabled=self.config.simulation_enabled`
(`strategy_runner.py`, absent in 2.28.0). Neither is in a PyPI release yet, so this
package keeps its two wrappers until one ships. Item 3 is still open on main and is
filed as an issue with a diff that applies to main (`docs/submission/almanak-issue.md`).

## 1. In-process managed gateway deadlocks for 30 s in `RegisterChains` when a wallet registry plugin is installed

Path: `almanak strat run` (managed gateway in a daemon thread) with
`ALMANAK_GATEWAY_WALLETS` set and any `almanak.wallets` registry plugin installed.

`server.py::_RegisterChainsServicer.RegisterChains` ->
`_register_chains_helpers.reinitialize_market_service` ->
`MarketServiceServicer.reinitialize` -> `_do_initialize()` (synchronous, on the
gateway's event loop) -> `chainlink/gateway/live.py::LivePriceSource.__init__` ->
`_build_token_pair_map` -> `TokenResolver.resolve` -> `_resolve_symbol_via_gateway`,
a **blocking** gRPC call to the same gateway whose loop is currently blocked. The
call times out after 30 s, the runner logs `register_chains() failed:
DEADLINE_EXCEEDED`, falls back to legacy wallet resolution, and aborts with
"cannot resolve deployment_id: local mode requires a resolved execution wallet".

Stack (captured with faulthandler 8 s into the stall) is in this repo's history;
key frames:

```
market_service.py:261 for_chain -> chainlink/gateway/factory.py:23 build
-> chainlink/gateway/live.py:152 __init__ -> live.py:375 _build_token_pair_map
-> framework/data/tokens/resolver.py:952 resolve -> resolver.py:1528 _resolve_symbol_via_gateway
-> grpc/_channel.py:1152 _blocking
```

Why it never shows up without a plugin: `register_chains()` only runs when
`ALMANAK_GATEWAY_WALLETS` is set, and the only registry that exists is the private
`almanak-platform-plugins`, which is deployed with an out-of-process gateway.

Suggested fix: run `_do_initialize` off the loop (`asyncio.to_thread`) or skip the
re-init when `chain in self._price_aggregators` already (the managed gateway starts
with the full stack). This package applies the second as a wrapper.

## 2. The strategy runner never enables the orchestrator's simulate phase on live networks

`ExecutionContext.simulation_enabled` defaults to `False` and
`strategy_runner.py` builds contexts without setting it, so
`ExecutionServiceServicer.Execute` sends `simulation_enabled=False` and the
orchestrator takes the `_maybe_estimate_gas_limits` path (`eth_estimateGas` only).
The configured simulator (Tenderly, Alchemy, Local) is only used when
`settings.network == "anvil"`. `almanak strat run --simulate-tx` does not change
this: the flag feeds `create_execution_orchestrator`, which the gateway path never
calls.

Repro: `almanak strat run --once --simulate-tx` on Base with `SIMULATION_ENABLED=true`
and watch for the `SIMULATING` event. It never fires; you only see
`Gas estimate tx[i]: raw=... buffered=...` lines without `source=`.

This package wraps `orchestrator.execute` to set `simulation_enabled=True` for
KeeperHub-backed orchestrators.

## 3. Proposal: make the execution backend pluggable (patches/almanak-execution-backend.diff)

`_create_signer_from_resolved` only knows the kinds `zodiac`, `direct` and
`squads`, and `_get_orchestrator` hardcodes `PublicMempoolSubmitter` and
`create_simulator`. A registry wallet of any other kind fails closed ("Unknown
wallet kind"). The patch adds an `almanak.execution_backends` entry-point group
keyed by wallet kind so a plugin can provide signer, submitter and simulator
together, which is how this package is wired today via a subclass.
