#!/usr/bin/env bash
# One full strategy lifecycle on Base Sepolia through KeeperHub, then republish the proof.
#
# Runs the packaged demo strategy for one tick (approve + deposit 5 test USDC into the
# TestVault), redeems the position through KeeperHub so the USDC comes back, merges every
# receipts log into docs/all-receipts.json, and re-exports the static console into docs/.
# The scheduled workflow in .github/workflows/proof.yml runs exactly this on a bare runner
# and commits the result, so the public console keeps gaining executions nobody typed in.
#
#   scripts/proof_tick.sh             # needs KEEPERHUB_API_KEY (mcp:write)
#   scripts/proof_tick.sh --no-tick   # only merge and export
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
: "${KEEPERHUB_API_KEY:?set KEEPERHUB_API_KEY}"
export ALMANAK_KEEPERHUB_CHAIN=base_sepolia
export ALMANAK_BASE_SEPOLIA_RPC_URL="${ALMANAK_BASE_SEPOLIA_RPC_URL:-https://sepolia.base.org}"
export RPC_URL_BASE="${RPC_URL_BASE:-$ALMANAK_BASE_SEPOLIA_RPC_URL}"
STRATEGY="$ROOT/demos/metamorpho_base_sepolia"

step() { echo; echo "================ $1"; echo; }

if [ "${1:-}" != "--no-tick" ]; then
  step "tick: Almanak plans, KeeperHub dry-runs, then approve + deposit through KeeperHub"
  almanak-keeperhub run -d "$STRATEGY" --once --fresh 2>&1 \
    | grep -E "KeeperHub simulate|broadcast tx|Status:|executions this run|^  [a-z]+ ->|^    tx|refused|error" || true

  step "exit: redeem the whole position through KeeperHub (the test USDC comes back)"
  ( cd "$STRATEGY" && python "$ROOT/scripts/redeem_all.py" )
fi

step "live conformance: the documented API behaviours, checked against production"
ALMANAK_KEEPERHUB_LIVE=1 ALMANAK_KEEPERHUB_CONFORMANCE_OUT="$ROOT/docs/conformance.json" \
  python -m pytest -q "$ROOT/tests/live" -p no:cacheprovider || echo "!!! conformance failures recorded in docs/conformance.json"

step "ask production which upstream features it has yet (raw calldata, sequence dry run)"
almanak-keeperhub api-features --chain base_sepolia --out "$ROOT/docs/api-features.json" || true

step "merge every receipts log into docs/all-receipts.json"
almanak-keeperhub merge-receipts \
  "$STRATEGY/keeperhub-receipts.json" \
  "$ROOT/demos/failure_modes/keeperhub-receipts.json" \
  --into "$ROOT/docs/all-receipts.json"

step "export the console over all of it (verdicts already frozen are kept)"
almanak-keeperhub console --export "$ROOT/docs" --receipts "$ROOT/docs/all-receipts.json" --docs "$ROOT/docs" \
  --chain base_sepolia --no-open
