#!/usr/bin/env bash
# The hosted-app sequence, in the order the demo video shows it.
# Needs .env with KEEPERHUB_API_KEY (mcp:write) and a Base RPC URL, and the
# organization wallet funded on Base (at least 6 USDC and a little ETH).
#
#   scripts/first_run.sh            # doctor, dry run, simulate-only, real run, failure modes
#   scripts/first_run.sh --skip-real
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
: "${KEEPERHUB_API_KEY:?set KEEPERHUB_API_KEY in .env}"
export RPC_URL_BASE="${RPC_URL_BASE:-${ALMANAK_BASE_RPC_URL:-https://mainnet.base.org}}"
export ALMANAK_BASE_RPC_URL="${ALMANAK_BASE_RPC_URL:-$RPC_URL_BASE}"
export BASE_RPC_URL="${BASE_RPC_URL:-$RPC_URL_BASE}"
STRATEGY="$ROOT/demos/metamorpho_base_yield"

step() { echo; echo "================ $1"; echo; }

step "1/7 doctor"
almanak-keeperhub doctor --chain base

step "2/7 Almanak plans (--dry-run): nothing reaches the gateway"
almanak-keeperhub run -d "$STRATEGY" --once --fresh --dry-run 2>&1 | grep -E "Wallet registry plugin loaded|Using KeeperHubSigner|KeeperHub execution backend active|Would execute|Status:" || true

step "3/7 KeeperHub dry run of the compiled bundle (--simulate-only): nothing broadcast"
almanak-keeperhub run -d "$STRATEGY" --once --fresh --simulate-only 2>&1 | grep -E "KeeperHub simulate|Gas estimate tx|Status:|executions this run" || true

if [ "${1:-}" != "--skip-real" ]; then
  step "4/7 real run: approve + deposit through KeeperHub"
  almanak-keeperhub run -d "$STRATEGY" --once 2>&1 | grep -E "KeeperHub simulate|broadcast tx|Status:|executions this run|^  [a-z]+ ->|^    tx" || true
  echo
  step "4b/7 exit tick: the strategy leaves, the redeem goes through KeeperHub"
  almanak-keeperhub run -d "$STRATEGY" --once -c "$STRATEGY/config.exit.json" 2>&1 | grep -E "EXIT:|KeeperHub simulate|broadcast tx|Status:|^  [a-z]+ ->" || true
  step "4c/7 keeper: the scheduled compounder, created and enabled in KeeperHub"
  ( cd "$STRATEGY" && almanak-keeperhub keeper deploy && almanak-keeperhub keeper status && echo "keeper created DISABLED on mainnet; enable it deliberately with: almanak-keeperhub keeper enable" ) || echo "!!! keeper deploy failed; see the README section on the keeper"
  echo
  echo "receipts: $STRATEGY/keeperhub-receipts.json and keeperhub-keeper.json (commit both; they are the proof)"
fi

step "5/7 failure modes (each script explains what it proves)"
( cd "$ROOT/demos/failure_modes" && for s in unknown_selector_refused revert_caught_by_dry_run cap_refused duplicate_blocked_by_idempotency rpc_outage crash_and_resume stale_exit_not_executed; do
    echo "--- $s"; python "$s.py" || echo "!!! $s failed; read the output above"
  done )
echo
echo "demo receipts: $ROOT/docs/receipts.json"

step "6/7 verify the last strategy execution and reproduce the API notes"
LAST_HASH=$(python -c "import json; d=json.load(open('$STRATEGY/keeperhub-receipts.json')); print(d[-1]['tx_hash'])" 2>/dev/null || true)
[ -n "$LAST_HASH" ] && ( cd "$STRATEGY" && almanak-keeperhub verify "$LAST_HASH" --chain base ) || echo "no strategy receipt yet"
python scripts/verify_api_notes.py | tee docs/api-notes-verified.md

step "7/7 next"
cat <<'EOF'
- Open the transaction links above on Basescan and the executions in the KeeperHub app (Runs).
- Paste one transaction link into the DoraHacks form and the README "Proof" section.
- git add demos/metamorpho_base_yield/keeperhub-receipts.json docs/receipts.json && git commit
EOF
