#!/usr/bin/env bash
# Full rehearsal on a local fork with a stand-in KeeperHub API. Nothing here touches a real network.
#
#   tests/e2e/rehearsal.sh             # fork of Base mainnet: the demo strategy against the real Moonwell vault
#   tests/e2e/rehearsal.sh --testnet   # fork of Base Sepolia: the free path (TestVault + Circle test USDC)
#
# Requirements: foundry (anvil, cast, forge), the package installed in .venv, network access for the fork.
# Env: FORK_URL, ANVIL_BIN, ANVIL_PORT, FAKE_PORT override the defaults.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-mainnet}"
ORG=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266            # anvil account #0, the stand-in's "org wallet"
ORG_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

if [ "$MODE" = "--testnet" ]; then
  FORK_URL="${FORK_URL:-https://sepolia.base.org}"
  CHAIN_ID=84532; CHAIN=base_sepolia; ANVIL_PORT="${ANVIL_PORT:-8549}"; FAKE_PORT="${FAKE_PORT:-8792}"
  USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e          # Circle test USDC on Base Sepolia
  STRATEGY_DIR="$ROOT/demos/metamorpho_base_sepolia"
else
  FORK_URL="${FORK_URL:-https://mainnet.base.org}"
  CHAIN_ID=8453; CHAIN=base; ANVIL_PORT="${ANVIL_PORT:-8547}"; FAKE_PORT="${FAKE_PORT:-8791}"
  USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  VAULT=0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca         # Moonwell Flagship USDC (almanak demo)
  WHALE=0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A         # large USDC holder on Base, impersonated on the fork
  STRATEGY_DIR="$ROOT/demos/metamorpho_base_yield"
fi
RPC="http://127.0.0.1:$ANVIL_PORT"
WORK="$(mktemp -d)"

cleanup() { kill "${ANVIL_PID:-}" "${FAKE_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

echo "== starting anvil fork ($CHAIN, chain id $CHAIN_ID)"
"${ANVIL_BIN:-anvil}" --fork-url "$FORK_URL" --port "$ANVIL_PORT" --chain-id "$CHAIN_ID" --retries 12 --fork-retry-backoff 2000 --silent &
ANVIL_PID=$!
for _ in $(seq 1 30); do cast block-number --rpc-url "$RPC" >/dev/null 2>&1 && break; sleep 1; done

if [ "$MODE" = "--testnet" ]; then
  echo "== deploying contracts/TestVault.sol on the fork (on real Base Sepolia this is the one step that needs faucet ETH)"
  VAULT=$("$ROOT/scripts/deploy_test_vault.sh" --rpc "$RPC" --private-key "$ORG_KEY" | tail -1)
  echo "   TestVault at $VAULT"
  echo "== giving $ORG 200 test USDC (storage write; on real Base Sepolia use https://faucet.circle.com)"
  cast rpc anvil_setStorageAt "$USDC" "$(cast index address "$ORG" 9)" 0x000000000000000000000000000000000000000000000000000000000BEBC200 --rpc-url "$RPC" >/dev/null
  python - "$STRATEGY_DIR/config.json" "$WORK/config.json" "$VAULT" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1])); cfg["vault_address"] = sys.argv[3]
json.dump(cfg, open(sys.argv[2], "w"), indent=4)
EOF
  STRATEGY_CONFIG="$WORK/config.json"
  export ALMANAK_KEEPERHUB_CHAIN=base_sepolia ALMANAK_KEEPERHUB_VAULT="$VAULT"
  export ALMANAK_BASE_SEPOLIA_RPC_URL="$RPC" RPC_URL_BASE="$RPC"
else
  echo "== funding $ORG with 200 USDC"
  cast rpc anvil_impersonateAccount "$WHALE" --rpc-url "$RPC" >/dev/null
  cast rpc anvil_setBalance "$WHALE" 0x1000000000000000000 --rpc-url "$RPC" >/dev/null
  cast send "$USDC" "transfer(address,uint256)" "$ORG" 200000000 --from "$WHALE" --unlocked --rpc-url "$RPC" >/dev/null
  STRATEGY_CONFIG="$STRATEGY_DIR/config.json"
  export ALMANAK_BASE_RPC_URL="$RPC" BASE_RPC_URL="$RPC" RPC_URL_BASE="$RPC"
fi
echo "   org USDC: $(cast call "$USDC" "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)"

echo "== starting the KeeperHub stand-in"
# KEEPERHUB_FAKE_API=legacy rehearses the fallbacks (typed single calls, no check-and-execute).
python "$ROOT/tests/e2e/fake_keeperhub.py" --rpc "$RPC" --port "$FAKE_PORT" --chain-id "$CHAIN_ID" --api "${KEEPERHUB_FAKE_API:-current}" &
FAKE_PID=$!
sleep 2

# Almanak loads the repo's .env by itself, so every KeeperHub variable is pinned here explicitly.
export KEEPERHUB_API_KEY=kh_rehearsal KEEPERHUB_BASE_URL="http://127.0.0.1:$FAKE_PORT" KEEPERHUB_WALLET_ADDRESS="$ORG"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ALMANAK_KEEPERHUB_EXIT_ID 2>/dev/null || true
# Fork receipts must never be mistaken for proof: keep them out of the strategy directory.
export ALMANAK_KEEPERHUB_RECEIPTS="$WORK/keeperhub-receipts.json"
export ALMANAK_KEEPERHUB_DEMO_RECEIPTS="$WORK/demo-receipts.json"
export ALMANAK_KEEPERHUB_KEEPER_STATE="$WORK/keeperhub-keeper.json"
rm -f "$STRATEGY_DIR"/almanak_state.db*

echo "== doctor"
almanak-keeperhub doctor --chain "$CHAIN"

echo "== failure modes"
( cd "$ROOT/demos/failure_modes" && for s in unknown_selector_refused revert_caught_by_dry_run cap_refused duplicate_blocked_by_idempotency crash_and_resume; do python "$s.py"; done )

echo "== verify: KeeperHub's verdict plus on-chain evidence for the last recorded hash"
LAST_HASH=$(python -c "import json,os; d=[e for e in json.load(open(os.environ['ALMANAK_KEEPERHUB_RECEIPTS'])) if e.get('type')!='simulation']; print(d[-1]['tx_hash'])")
( cd "$ROOT" && almanak-keeperhub verify "$LAST_HASH" --chain "$CHAIN" )

echo "== simulate-only: KeeperHub dry-runs the compiled bundle, nothing broadcast"
( cd "$STRATEGY_DIR" && almanak-keeperhub run --once --fresh --simulate-only -c "$STRATEGY_CONFIG" 2>&1 | grep -E "KeeperHub simulate|Status:|dry runs this run|executions this run" )

echo "== unmodified almanak demo strategy, executed through the backend"
( cd "$STRATEGY_DIR" && almanak-keeperhub run --once --fresh -c "$STRATEGY_CONFIG" 2>&1 | grep -E "KeeperHub simulate|KeeperHub execution|Status:|Gas estimate tx|^  [a-z]+ ->" )

echo "== keeper: the scheduled compounder workflow, created in KeeperHub and enabled"
( cd "$STRATEGY_DIR" && almanak-keeperhub keeper deploy --vault "$VAULT" --token "$USDC" --symbol USDC && almanak-keeperhub keeper enable && almanak-keeperhub keeper status )

if [ "$MODE" != "--testnet" ]; then
  echo "== almanak's agent CLI (ax): dry-run swap, then a real 1 USDC swap through the backend"
  ( cd "$ROOT" && almanak-keeperhub ax --chain base swap USDC WETH 1 --dry-run 2>&1 | grep -E "Simulation:|amount_out" )
  ( cd "$ROOT" && almanak-keeperhub ax --chain base swap USDC WETH 1 --yes 2>&1 | grep -E "Swap:|broadcast tx|executions this run|^  [a-z]+ ->" )

  echo "== authority separation: Almanak's own policy refuses before KeeperHub is called"
  ( cd "$ROOT" && almanak-keeperhub ax --chain base --max-trade-usd 0.5 swap USDC WETH 1 --yes 2>&1 | grep -E "Policy denied|executions this run" | head -2 ) || true  # the refusal is the point

  echo "== exit tick: the strategy decides to leave (APY floor raised), redeem goes through KeeperHub"
  ( cd "$STRATEGY_DIR" && almanak-keeperhub run --once -c config.exit.json 2>&1 | grep -E "EXIT:|KeeperHub simulate|broadcast tx|Status:|^  [a-z]+ ->" )
fi

echo "== benchmark (small): refusals, dry runs, broadcasts, replay, one crash cycle"
( cd "$ROOT" && python scripts/benchmark.py --refusals 3 --simulations 3 --executions 2 --retries 2 --crashes 1 | grep -E "^\|" && rm -f docs/benchmark.md docs/benchmark.json )

echo "== api features: what this KeeperHub accepts (the stand-in answers like production will once the merged changes deploy)"
( cd "$ROOT" && almanak-keeperhub api-features --chain "$CHAIN" )

if [ "$MODE" = "--testnet" ] && [ "${KEEPERHUB_FAKE_API:-current}" = "current" ]; then
  echo "== guarded exit: KeeperHub re-reads the position, redeems it, then refuses the same decision replayed stale"
  ( cd "$STRATEGY_DIR" && almanak-keeperhub exit --simulate --chain "$CHAIN" && almanak-keeperhub exit --chain "$CHAIN" )
  ( cd "$ROOT/demos/failure_modes" && python stale_exit_not_executed.py )
  echo "== and back in: one more tick so the final balances read as before"
  ( cd "$STRATEGY_DIR" && almanak-keeperhub run --once --fresh -c "$STRATEGY_CONFIG" 2>&1 | grep -E "KeeperHub simulate|Status:|^  [a-z]+ ->" )
fi

SHARES=$(cast call "$VAULT" "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)
LEFT=$(cast call "$USDC" "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)
if [ "$MODE" = "--testnet" ]; then
  echo "== vault shares: $SHARES (expected 5000000: the 1:1 TestVault holds the 5 USDC deposit), USDC left: $LEFT (expected 195000000)"
  [ "$SHARES" = "5000000" ] && [ "$LEFT" = "195000000" ] && echo "REHEARSAL OK (testnet)" || { echo "REHEARSAL FAILED"; exit 1; }
else
  WETH=$(cast call 0x4200000000000000000000000000000000000006 "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)
  echo "== vault shares: $SHARES (expected dust after the exit), USDC left: $LEFT (expected about 198999999: 1 USDC swapped, deposit redeemed), WETH: $WETH"
  # "redeem all" leaves rounding dust in the vault (share/asset conversion); dust is below 1e13 shares.
  [ "$SHARES" -lt 10000000000000 ] && [ "$LEFT" -ge 198990000 ] && [ "$LEFT" -le 199000000 ] && [ "$WETH" != "0" ] && echo "REHEARSAL OK" || { echo "REHEARSAL FAILED"; exit 1; }
fi
