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
  step "before the tick: redeem anything a previous run left in the vault, so this one has its USDC"
  almanak-keeperhub exit -d "$STRATEGY" --chain base_sepolia || true   # "nothing to redeem" is the normal case

  step "tick: Almanak plans, KeeperHub dry-runs, then approve + deposit through KeeperHub"
  almanak-keeperhub run -d "$STRATEGY" --once --fresh 2>&1 \
    | grep -E "KeeperHub simulate|broadcast tx|Status:|executions this run|^  [a-z]+ ->|^    tx|refused|error" || true

  step "exit: KeeperHub re-reads the position and redeems it only if it still holds (check-and-execute)"
  almanak-keeperhub exit -d "$STRATEGY" --chain base_sepolia
fi

step "live conformance: the documented API behaviours, checked against production"
ALMANAK_KEEPERHUB_LIVE=1 ALMANAK_KEEPERHUB_CONFORMANCE_OUT="$ROOT/docs/conformance.json" \
  python -m pytest -q "$ROOT/tests/live" -p no:cacheprovider || echo "!!! conformance failures recorded in docs/conformance.json"

step "reproduce the API findings against production (two of them are this project's merged fixes, awaiting deployment)"
python "$ROOT/scripts/verify_api_notes.py" > "$ROOT/docs/api-notes-verified.md" || echo "!!! api notes failed"

step "ask production which upstream features it has yet (raw calldata, sequence dry run)"
almanak-keeperhub api-features --chain base_sepolia --out "$ROOT/docs/api-features.json" || true

step "merge every receipts log into docs/all-receipts.json"
LOGS=("$STRATEGY/keeperhub-receipts.json" "$ROOT/demos/failure_modes/keeperhub-receipts.json")
[ -f "$ROOT/keeperhub-receipts.json" ] && LOGS+=("$ROOT/keeperhub-receipts.json")   # the benchmark's log, local only
almanak-keeperhub merge-receipts "${LOGS[@]}" --into "$ROOT/docs/all-receipts.json"

step "export the console over all of it (verdicts already frozen are kept)"
almanak-keeperhub console --export "$ROOT/docs" --receipts "$ROOT/docs/all-receipts.json" --docs "$ROOT/docs" \
  --chain base_sepolia --no-open
