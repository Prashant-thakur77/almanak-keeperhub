KeeperHub: https://app.keeperhub.com

| # | Finding | Expected | Actual | Verdict |
|---|---|---|---|---|
| 1 | no raw-calldata write on contract-call | before KeeperHub#2449 deploys: 400 functionName required; after: 200, the calldata dry-runs | HTTP 200: {"success":true,"status":"simulated","from":"0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9","to":"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913","value":"0","gasEstim | FIXED UPSTREAM BY THIS PROJECT, LIVE ON PRODUCTION |
| 2 | no dry run of a call sequence against carried state | before KeeperHub#2452 deploys: 400 contractAddress required; after: results[] with one entry per call | HTTP 200: {"success":true,"status":"simulated","from":"0xe7dbacbdd4cb2ddff5681dcd9e56fcf488e36ac9","atomic":false,"mechanism":"eth_simulateV1","results":[{"success":true, | FIXED UPSTREAM BY THIS PROJECT, LIVE ON PRODUCTION |
| 5 | MCP guide URL in the brief redirects | 308 -> /agent/mcp-server | HTTP 308 -> /agent/mcp-server | REPRODUCED |
| 6 | network vs chainId precedence on contract-call | read runs on Sepolia (network wins): error or 6 from a different contract, not Base USDC's 6 | HTTP 400: {"error":"Contract call failed: Contract returned no data, but the ABI you supplied declares 1 output (uint8) for decima | REPRODUCED |
| + | spend-cap endpoint reachable with an API key | 200 with effectiveDailyCapWei | HTTP 200: {"dailyCapWei":null,"dailyUsedWei":"0","dailySolanaCapLamports":null,"dailySolanaUsedLamports":"0","effectiveDailyCapWei | OK |
