#!/usr/bin/env bash
# ship_key_autoload_selftest.sh — Asserts ship.sh contains Keychain lookup + fallback logic.
# Does NOT require a real key.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SHIP="$ROOT_DIR/ops/ship.sh"

[ ! -f "$SHIP" ] && { echo "ship.sh not found" >&2; exit 1; }

# Assert Keychain lookup present
grep -q 'security find-generic-password' "$SHIP" || { echo "FAIL: Keychain lookup missing" >&2; exit 1; }
grep -q 'ai-ops-runner:VERDICT_HMAC_KEY' "$SHIP" || { echo "FAIL: Keychain service name missing" >&2; exit 1; }

# Assert fallback file logic present
grep -q 'verdict_hmac_key' "$SHIP" || { echo "FAIL: Fallback file path missing" >&2; exit 1; }
grep -q '\.config/ai-ops-runner' "$SHIP" || { echo "FAIL: Config dir path missing" >&2; exit 1; }

# Assert one-time setup command in error message
grep -q 'security add-generic-password' "$SHIP" || { echo "FAIL: One-time setup command missing" >&2; exit 1; }
grep -q "key stored" "$SHIP" || { echo "FAIL: 'key stored' instruction missing" >&2; exit 1; }

echo "ship_key_autoload_selftest: PASS"
