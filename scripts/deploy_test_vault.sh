#!/usr/bin/env bash
# Deploy contracts/TestVault.sol (ERC-4626 over Base Sepolia test USDC) and print its address.
#
#   scripts/deploy_test_vault.sh --rpc https://sepolia.base.org --private-key 0x...   # needs ~0.0005 Sepolia ETH
#   scripts/deploy_test_vault.sh --rpc http://127.0.0.1:8549 --private-key <anvil key> # on a local fork
#
# The deployer key is only used for this one deploy; the vault has no owner and no admin.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USDC="${USDC:-0x036CbD53842c5426634e7929541eC2318f3dCF7e}"
RPC="" KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rpc) RPC="$2"; shift 2 ;;
    --private-key) KEY="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
: "${RPC:?--rpc is required}"; : "${KEY:?--private-key is required}"
OUT=$(forge create "$ROOT/contracts/TestVault.sol:TestVault" --rpc-url "$RPC" --private-key "$KEY" --broadcast \
  --constructor-args "$USDC" "Almanak KeeperHub Test Vault" "akUSDC" 2>&1)
ADDR=$(echo "$OUT" | sed -n 's/^Deployed to: \(0x[0-9a-fA-F]*\).*/\1/p' | head -1)
if [ -z "$ADDR" ]; then echo "$OUT"; echo "deploy failed"; exit 1; fi
echo "TestVault deployed at $ADDR (asset $USDC)"
echo "$ADDR"
