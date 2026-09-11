#!/usr/bin/env bash
# Local rehearsal of the whole integration without a KeeperHub account.
#
#   Anvil fork of Base  <-  tests/e2e/fake_keeperhub.py (stand-in, documented API shapes)
#                       <-  almanak-keeperhub run --once  (unmodified Almanak demo strategy)
#
# This proves the wiring (registry -> signer -> simulate -> submit -> receipts) end to end.
# It is NOT evidence of execution through KeeperHub; the real proof links come from
# app.keeperhub.com and live in docs/receipts.json.
#
# Requirements: foundry (anvil, cast), the package installed in .venv, network access for the fork.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORK_URL="${FORK_URL:-https://mainnet.base.org}"
RPC="http://127.0.0.1:${ANVIL_PORT:-8547}"
FAKE_PORT="${FAKE_PORT:-8790}"
ORG=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266            # anvil account #0, the stand-in's "org wallet"
USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
VAULT=0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca           # Moonwell Flagship USDC (almanak demo)
WHALE=0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A           # large USDC holder on Base, impersonated on the fork

cleanup() { kill "${ANVIL_PID:-}" "${FAKE_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

echo "== starting anvil fork of Base"
anvil --fork-url "$FORK_URL" --port "${ANVIL_PORT:-8547}" --chain-id 8453 --retries 12 --fork-retry-backoff 2000 --silent &
ANVIL_PID=$!
for _ in $(seq 1 30); do cast block-number --rpc-url "$RPC" >/dev/null 2>&1 && break; sleep 1; done

echo "== funding $ORG with 200 USDC"
cast rpc anvil_impersonateAccount "$WHALE" --rpc-url "$RPC" >/dev/null
cast rpc anvil_setBalance "$WHALE" 0x1000000000000000000 --rpc-url "$RPC" >/dev/null
cast send "$USDC" "transfer(address,uint256)" "$ORG" 200000000 --from "$WHALE" --unlocked --rpc-url "$RPC" >/dev/null

echo "== starting the KeeperHub stand-in"
python "$ROOT/tests/e2e/fake_keeperhub.py" --rpc "$RPC" --port "$FAKE_PORT" &
FAKE_PID=$!
sleep 2

export KEEPERHUB_API_KEY=kh_rehearsal KEEPERHUB_BASE_URL="http://127.0.0.1:$FAKE_PORT"
export ALMANAK_BASE_RPC_URL="$RPC" BASE_RPC_URL="$RPC" RPC_URL_BASE="$RPC"
# Fork receipts must never be mistaken for proof: keep them out of the strategy directory.
export ALMANAK_KEEPERHUB_RECEIPTS="$(mktemp -d)/keeperhub-receipts.json"

echo "== doctor"
almanak-keeperhub doctor --chain base

echo "== failure modes"
( cd "$ROOT/demos/failure_modes" && for s in unknown_selector_refused revert_caught_by_dry_run cap_refused duplicate_blocked_by_idempotency; do python "$s.py"; done )

echo "== simulate-only: KeeperHub dry-runs the compiled bundle, nothing broadcast"
rm -f "$ROOT/demos/metamorpho_base_yield/almanak_state.db"*
( cd "$ROOT/demos/metamorpho_base_yield" && almanak-keeperhub run --once --fresh --simulate-only 2>&1 | grep -E "KeeperHub simulate|Status:|executions this run" )

echo "== unmodified almanak demo strategy, executed through the backend"
( cd "$ROOT/demos/metamorpho_base_yield" && almanak-keeperhub run --once --fresh 2>&1 | grep -E "KeeperHub simulate|KeeperHub execution|Status:|Gas estimate tx" )

SHARES=$(cast call "$VAULT" "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)
LEFT=$(cast call "$USDC" "balanceOf(address)(uint256)" "$ORG" --rpc-url "$RPC" | cut -d' ' -f1)
echo "== vault shares: $SHARES, USDC left: $LEFT"
[ "$SHARES" != "0" ] && [ "$LEFT" = "150000000" ] && echo "REHEARSAL OK" || { echo "REHEARSAL FAILED"; exit 1; }
