# Local rehearsal on an Anvil fork of Base (2026-09-11)

Stand-in KeeperHub (tests/e2e/fake_keeperhub.py), not the hosted app. Kept as evidence that the wiring works; not proof of execution through KeeperHub.

```
24:2026-09-11T20:29:35.553570Z [info     ] Wallet registry plugin loaded: KeeperHubWalletRegistry [almanak.gateway._server_start_helpers]
25:2026-09-11T20:29:35.553667Z [info     ] Wallet config: chain=base address=0xf39Fd6e5... type=keeperhub [almanak.gateway._server_start_helpers]
40:2026-09-11T20:29:39.504902Z [info     ] Using KeeperHubSigner for 0xf39Fd6e5 on base [almanak_keeperhub.gateway]
45:2026-09-11T20:29:39.516385Z [info     ] KeeperHub execution backend active for chain=base wallet=0xf39Fd6e5 [almanak_keeperhub.gateway]
62:Gateway wallet registry: uniform wallet 0xf39Fd6e51a... on 1 chain(s)
144:2026-09-11T20:29:49.363129Z [info     ] Dispatching VAULT_DEPOSIT (2 tx) to execution orchestrator (intent=06f10076..., chain=base) [almanak.framework.runner.strategy_runner] correlation_id=deploy
145:2026-09-11T20:29:53.437954Z [info     ] KeeperHub simulate ok: 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.approve gas=55819 from=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 [almanak_keeperhub.simul
146:2026-09-11T20:29:53.438158Z [info     ] Gas estimate tx[0]: raw=55,819 buffered=83,728 (x1.5) source=keeperhub [almanak.framework.execution.orchestrator]
148:2026-09-11T20:29:53.438407Z [info     ] Gas estimate tx[1]: raw=675,000 buffered=1,012,500 (x1.5) source=keeperhub [almanak.framework.execution.orchestrator]
149:2026-09-11T20:29:57.972364Z [info     ] KeeperHub execution d89d70c709e948e08c309 broadcast tx 0x773e96c5f97cbac79b2dcd2002ed902b336c9145b1e4f4095e6cc213f57c98bc (completed) [almanak_keeperhub.sub
150:2026-09-11T20:30:22.827071Z [info     ] KeeperHub execution 107075001f4e4ed3a390b broadcast tx 0x12ade62e3ef8c71592e333ac0e6940711122822b259fdcf3abe7f00940d06f67 (completed) [almanak_keeperhub.sub
182:Status: SUCCESS | Intent: VAULT_DEPOSIT | Gas used: 404354 | Duration: 51186ms

fork state after the run: USDC 200 -> 150, vault shares 45974074222997175146 (49.999999 USDC of assets)
```
