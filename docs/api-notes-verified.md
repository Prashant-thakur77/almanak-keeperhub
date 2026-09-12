KeeperHub: https://app.keeperhub.com

| # | Finding | Expected | Actual | Verdict |
|---|---|---|---|---|
| 1 | no raw-calldata write on contract-call | 4xx: functionName is required (raw calldata not accepted) | HTTP 400: {"error":"Missing required field","field":"functionName","details":"functionName is required and must be a non-empty string"} | REPRODUCED |
| 5 | MCP guide URL in the brief redirects | 308 -> /agent/mcp-server | HTTP 308 -> /agent/mcp-server | REPRODUCED |
| 6 | network vs chainId precedence on contract-call | read runs on Sepolia (network wins): error or 6 from a different contract, not Base USDC's 6 | HTTP 400: {"error":"Contract call failed: Contract returned no data, but the ABI you supplied declares 1 output (uint8) for decima | REPRODUCED |
| + | spend-cap endpoint reachable with an API key | 200 with effectiveDailyCapWei | HTTP 200: {"dailyCapWei":null,"dailyUsedWei":"0","dailySolanaCapLamports":null,"dailySolanaUsedLamports":"0","effectiveDailyCapWei | OK |
