#!/usr/bin/env bash
# Track A: the free path on Base Sepolia, start to finish.
#
#   scripts/track_a.sh            # run everything; pauses when it needs a faucet
#   scripts/track_a.sh --status   # just print what is funded and what is missing
#
# Needs .env with KEEPERHUB_API_KEY (organization key, scope mcp:write). Everything else it
# creates or asks for: a throwaway deployer key (written to .env), Sepolia ETH for one deploy,
# test USDC on the org wallet. KeeperHub sponsors the org wallet's gas on Base Sepolia.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${KEEPERHUB_API_KEY:?set KEEPERHUB_API_KEY in .env (Settings > API keys, scope mcp:write)}"

export ALMANAK_BASE_SEPOLIA_RPC_URL="${ALMANAK_BASE_SEPOLIA_RPC_URL:-https://sepolia.base.org}"
export RPC_URL_BASE="${RPC_URL_BASE:-$ALMANAK_BASE_SEPOLIA_RPC_URL}"
export ALMANAK_KEEPERHUB_CHAIN=base_sepolia
RPC="$ALMANAK_BASE_SEPOLIA_RPC_URL"
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
STRATEGY="$ROOT/demos/metamorpho_base_sepolia"
CONFIG="$STRATEGY/config.json"
STATUS_ONLY="${1:-}"

step() { echo; echo "================ $1"; echo; }
usdc_of() { cast call "$USDC" "balanceOf(address)(uint256)" "$1" --rpc-url "$RPC" | cut -d' ' -f1; }
eth_of() { cast balance "$1" --rpc-url "$RPC"; }
wait_for() {  # wait_for <label> <command that prints a number> <minimum>
  local label="$1" cmd="$2" min="$3" v
  while :; do
    v=$($cmd 2>/dev/null || echo 0)
    if [ "${v:-0}" -ge "$min" ]; then echo "   $label: $v (ok)"; return 0; fi
    echo "   $label: ${v:-0}, waiting for the faucet... (Ctrl+C to stop; rerun the script to resume)"
    sleep 15
  done
}

step "1/8 org wallet"
ORG=$(almanak-keeperhub doctor --chain base_sepolia 2>/dev/null | sed -n 's/^org wallet *: *//p' | head -1 || true)
if [ -z "$ORG" ]; then almanak-keeperhub doctor --chain base_sepolia; echo "could not read the org wallet; fix the key first"; exit 1; fi
echo "   org wallet (KeeperHub, Turnkey): $ORG"

step "2/8 deployer key for the one contract deploy"
if [ -z "${TESTVAULT_DEPLOYER_KEY:-}" ]; then
  TESTVAULT_DEPLOYER_KEY=$(cast wallet new 2>/dev/null | sed -n 's/^Private key: *//p' | head -1)
  printf '\n# throwaway key that deploys contracts/TestVault.sol once; never holds anything else\nTESTVAULT_DEPLOYER_KEY=%s\n' "$TESTVAULT_DEPLOYER_KEY" >> .env
  echo "   generated a throwaway deployer key and saved it to .env"
fi
DEPLOYER=$(cast wallet address --private-key "$TESTVAULT_DEPLOYER_KEY")
echo "   deployer address: $DEPLOYER"

step "3/8 what to fund (both free)"
echo "   a) Sepolia ETH to the DEPLOYER $DEPLOYER (0.001 is enough):"
echo "      https://portal.cdp.coinbase.com/products/faucet   or   https://www.alchemy.com/faucets/base-sepolia"
echo "   b) test USDC to the ORG WALLET $ORG (network: Base Sepolia, 20 USDC is plenty):"
echo "      https://faucet.circle.com"
echo "   c) nothing else: KeeperHub sponsors the org wallet's gas on Base Sepolia."
echo
echo "   deployer ETH now : $(eth_of "$DEPLOYER")"
echo "   org USDC now     : $(usdc_of "$ORG") (raw, 6 decimals)"
if [ "$STATUS_ONLY" = "--status" ]; then exit 0; fi

step "4/8 waiting for the faucets"
wait_for "deployer ETH (wei)" "cast balance $DEPLOYER --rpc-url $RPC" 300000000000000     # 0.0003 ETH
wait_for "org USDC (raw)" "usdc_of $ORG" 6000000                                             # 6 USDC

step "5/8 test vault"
VAULT=$(python -c "import json; print(json.load(open('$CONFIG')).get('vault_address',''))")
CODE=$(cast code "$VAULT" --rpc-url "$RPC" 2>/dev/null || echo 0x)
if [ "$CODE" = "0x" ] || [ -z "$CODE" ]; then
  VAULT=$(scripts/deploy_test_vault.sh --rpc "$RPC" --private-key "$TESTVAULT_DEPLOYER_KEY" | tail -1)
  python scripts/testnet_config.py "$VAULT"
else
  echo "   TestVault already deployed at $VAULT"
fi
export ALMANAK_KEEPERHUB_VAULT="$VAULT"

step "6/8 the strategy through KeeperHub (console: open a second terminal, cd $STRATEGY && almanak-keeperhub console)"
cd "$STRATEGY"
almanak-keeperhub run --once --fresh --simulate-only 2>&1 | grep -E "KeeperHub simulate|Status:|dry runs this run|executions this run" || true
almanak-keeperhub run --once --fresh 2>&1 | grep -E "KeeperHub simulate|broadcast tx|Status:|executions this run|^  [a-z]+ ->|^    tx" || true
almanak-keeperhub keeper deploy && almanak-keeperhub keeper enable && almanak-keeperhub keeper status || echo "!!! keeper deploy failed; paste the error into Discord (see TOMORROW.md)"
LAST_HASH=$(python -c "import json; d=[e for e in json.load(open('keeperhub-receipts.json')) if e.get('type')!='simulation']; print(d[-1]['tx_hash'])" 2>/dev/null || true)
[ -n "$LAST_HASH" ] && almanak-keeperhub verify "$LAST_HASH" --chain base_sepolia || echo "no execution to verify yet"
cd "$ROOT"

step "7/8 failure modes, benchmark, API notes"
( cd demos/failure_modes && for s in unknown_selector_refused revert_caught_by_dry_run cap_refused duplicate_blocked_by_idempotency rpc_outage crash_and_resume; do
    echo "--- $s"; python "$s.py" || echo "!!! $s failed; read the output above"
  done )
python scripts/benchmark.py --refusals "${BENCH_REFUSALS:-20}" --simulations "${BENCH_SIMULATIONS:-10}" --executions "${BENCH_EXECUTIONS:-5}"
python scripts/verify_api_notes.py | tee docs/api-notes-verified.md

step "8/8 proof to commit"
cat <<EOF
Vault:        $VAULT   (https://sepolia.basescan.org/address/$VAULT)
Strategy:     $STRATEGY/keeperhub-receipts.json and keeperhub-keeper.json
Demos:        docs/receipts.json
Benchmark:    docs/benchmark.md, docs/benchmark.json
API notes:    docs/api-notes-verified.md

  git add demos/metamorpho_base_sepolia/keeperhub-receipts.json demos/metamorpho_base_sepolia/keeperhub-keeper.json \\
          demos/metamorpho_base_sepolia/config.json docs/receipts.json docs/benchmark.md docs/benchmark.json docs/api-notes-verified.md
  git commit -m "Add Base Sepolia receipts"

Then paste one transaction link from the summary above into README.md under "Proof".
EOF
