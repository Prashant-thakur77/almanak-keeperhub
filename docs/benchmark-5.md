# Benchmark (bench-1789194432)

KeeperHub: https://app.keeperhub.com  chain: base_sepolia (84532)  wallet: 0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9  started: 2026-09-12T06:27:12.047155+00:00

| Measure | Result |
|---|---|
| Impossible deposits refused before broadcast | 20/20 |
| Valid approve dry runs succeeded | 10/10 (median gas estimate 38680) |
| Real approvals landed and verified | 5/5 |
| Broadcast + verified receipt latency | p50 6.88s, p95 9.59s |
| Simulate latency | p50 0.36s, p95 0.41s |
| Retry of already-landed work replayed, not resent | True (0.3s) |

Transaction hashes:

- 0x2f5c558aef9eef123e10075db9538173bdb92e39cbe5203dfe7cae9c826184fe
- 0xbb12525911dcabacdc596b93128f5d02f93c1a07af9ca0eeca5f1cb2179fda8e
- 0xdc272cbf01f3e6be5c13c6212bb20a2776b2de6787af12fd0f9bf8d0aa2dc8d6
- 0x4df347ccca4a6736dde0b5a7a02b4b1509dab34d9d86707f85d44db757a91a28
- 0x2f4d047fa871d92cc87c53e7330b7b624af635f187f4cd2e68db1ff99f2ad064
