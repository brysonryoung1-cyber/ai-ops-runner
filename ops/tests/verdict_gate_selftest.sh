#!/usr/bin/env bash
# verdict_gate_selftest.sh — Prove the v2 pre-push gate is unbreakable
#
# Tests run in an isolated temp worktree (bare origin + clone).
# No side effects on the real repo.
#
# Tests:
#   1. Direct push (no OPENCLAW_SHIP) → BLOCKED
#   2. Missing verdict file → BLOCKED
#   3. simulated=true → BLOCKED
#   4. Wrong range_end_sha → BLOCKED
#   5. Missing VERDICT_HMAC_KEY → BLOCKED (fail-closed)
#   6. Invalid HMAC signature → BLOCKED
#   7. Empty signature → BLOCKED
#   8. engine=none (placeholder) → BLOCKED
#   9. Correct verdict with valid HMAC → ALLOWED
#  10. Verdict with parent SHA + verdict-only diff extension → ALLOWED
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"
PRE_PUSH_HOOK="$ROOT_DIR/.githooks/pre-push"

PASS=0
FAIL=0
TESTS=0

TEST_HMAC_KEY="selftest-hmac-key-$(date +%s)"

assert_eq() {
  TESTS=$((TESTS + 1))
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (expected=$expected, actual=$actual)" >&2
    FAIL=$((FAIL + 1))
  fi
}

echo "=== verdict_gate_selftest.sh ==="

if [ ! -f "$PRE_PUSH_HOOK" ]; then
  echo "  FAIL: pre-push hook not found at $PRE_PUSH_HOOK" >&2
  exit 1
fi

# ── Setup isolated test repos ──
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

ORIGIN="$TMPDIR/origin.git"
CLONE="$TMPDIR/clone"

git init --bare "$ORIGIN" >/dev/null 2>&1

git clone "$ORIGIN" "$CLONE" >/dev/null 2>&1
cd "$CLONE"
git checkout -b main >/dev/null 2>&1 || true

echo "initial" > README.md
mkdir -p docs
echo "init_placeholder" > docs/LAST_REVIEWED_SHA.txt
echo '{}' > docs/LAST_APPROVED_VERDICT.json
git add -A
git commit -m "initial commit" >/dev/null 2>&1
git push -u origin main >/dev/null 2>&1

mkdir -p .git/hooks
cp "$PRE_PUSH_HOOK" .git/hooks/pre-push
chmod +x .git/hooks/pre-push

echo "change requiring review" >> README.md
git add README.md
git commit -m "change to review" >/dev/null 2>&1

HEAD_SHA="$(git rev-parse HEAD)"
BASE_SHA="$(git merge-base HEAD origin/main)"

echo "  Test repo: $CLONE"
echo "  BASE: ${BASE_SHA:0:12}"
echo "  HEAD: ${HEAD_SHA:0:12}"
echo ""

# ── Helper: compute HMAC ──
compute_hmac() {
  local json_file="$1"
  python3 - "$json_file" "$TEST_HMAC_KEY" <<'PYEOF'
import json, sys, hmac, hashlib
with open(sys.argv[1]) as f:
    data = json.load(f)
key = sys.argv[2].encode()
payload = {k: v for k, v in sorted(data.items()) if k != "signature"}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
print(hmac.new(key, canonical, hashlib.sha256).hexdigest())
PYEOF
}

# ── Helper: write verdict ──
write_verdict() {
  local json_content="$1"
  local add_hmac="${2:-0}"
  echo "$json_content" > "$CLONE/docs/LAST_APPROVED_VERDICT.json"
  if [ "$add_hmac" = "1" ]; then
    local sig
    sig="$(VERDICT_HMAC_KEY="$TEST_HMAC_KEY" compute_hmac "$CLONE/docs/LAST_APPROVED_VERDICT.json")"
    python3 - "$CLONE/docs/LAST_APPROVED_VERDICT.json" "$sig" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
data["signature"] = sys.argv[2]
with open(sys.argv[1], "w") as f:
    json.dump(data, f, indent=2)
PYEOF
  fi
}

# ── Helper: attempt push ──
try_push() {
  local ship="${1:-1}"
  local hmac_key="${2:-$TEST_HMAC_KEY}"
  local rc=0
  OPENCLAW_SHIP="$ship" VERDICT_HMAC_KEY="$hmac_key" git push origin main >/dev/null 2>&1 || rc=$?
  echo "$rc"
}

# ============================================================
# Test 1: Direct push without OPENCLAW_SHIP → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": ""
}' 1
RC="$(try_push 0)"
assert_eq "direct push without OPENCLAW_SHIP blocked" "1" "$RC"

# ============================================================
# Test 2: Missing verdict file → BLOCKED
# ============================================================
rm -f "$CLONE/docs/LAST_APPROVED_VERDICT.json"
RC="$(try_push)"
assert_eq "missing verdict file blocks push" "1" "$RC"

# ============================================================
# Test 3: simulated=true → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": true,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": ""
}' 1
RC="$(try_push)"
assert_eq "simulated=true blocks push" "1" "$RC"

# ============================================================
# Test 4: Wrong range_end_sha → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "0000000000000000000000000000000000000bad",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "0000000000000000000000000000000000000bad",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": ""
}' 1
RC="$(try_push)"
assert_eq "wrong range_end_sha blocks push" "1" "$RC"

# ============================================================
# Test 5: Missing VERDICT_HMAC_KEY → BLOCKED (fail-closed)
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": "somesig"
}'
RC="$(try_push 1 "")"
assert_eq "missing VERDICT_HMAC_KEY fails closed" "1" "$RC"

# ============================================================
# Test 6: Invalid HMAC signature → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": "badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad"
}'
RC="$(try_push)"
assert_eq "invalid HMAC signature blocks push" "1" "$RC"

# ============================================================
# Test 7: Empty signature → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": ""
}'
RC="$(try_push)"
assert_eq "empty signature blocks push" "1" "$RC"

# ============================================================
# Test 8: engine=none (placeholder) → BLOCKED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "none",
  "model": "test",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "test",
  "signature": ""
}' 1
RC="$(try_push)"
assert_eq "engine=none (placeholder) blocks push" "1" "$RC"

# ============================================================
# Test 9: Correct verdict with valid HMAC → ALLOWED
# ============================================================
write_verdict '{
  "approved_head_sha": "'"$HEAD_SHA"'",
  "range_start_sha": "'"$BASE_SHA"'",
  "range_end_sha": "'"$HEAD_SHA"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test-1.0",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "review_packets/test/CODEX_VERDICT.json",
  "signature": ""
}' 1
RC="$(try_push)"
assert_eq "correct verdict with valid HMAC allows push" "0" "$RC"

# ============================================================
# Test 10: Verdict-only extension (parent SHA + verdict diff) → ALLOWED
# ============================================================
# After test 9, origin/main is at HEAD_SHA. Make a new code change.
echo "more code" >> README.md
git add README.md
git commit -m "new code" >/dev/null 2>&1
CODE_HEAD="$(git rev-parse HEAD)"
CODE_BASE="$(git merge-base HEAD origin/main)"

# Write verdict pointing to CODE_HEAD
write_verdict '{
  "approved_head_sha": "'"$CODE_HEAD"'",
  "range_start_sha": "'"$CODE_BASE"'",
  "range_end_sha": "'"$CODE_HEAD"'",
  "simulated": false,
  "engine": "codex_cli",
  "model": "test-1.0",
  "created_at": "2026-01-01T00:00:00Z",
  "verdict_artifact_path": "review_packets/test/CODEX_VERDICT.json",
  "signature": ""
}' 1

# Commit the verdict (simulating what ship.sh does — verdict commit on top of code)
echo "$CODE_HEAD" > "$CLONE/docs/LAST_REVIEWED_SHA.txt"
git add -- docs/LAST_APPROVED_VERDICT.json docs/LAST_REVIEWED_SHA.txt
git commit -m "chore: verdict commit" >/dev/null 2>&1
# Now HEAD != CODE_HEAD, but diff is only verdict/baseline files
RC="$(try_push)"
assert_eq "verdict-only extension allows push" "0" "$RC"

# ============================================================
# Test 11: Verdict extension with non-verdict files → BLOCKED
# ============================================================
echo "sneaky code" >> README.md
git add README.md
SNEAKY_HEAD="$(git rev-parse HEAD)"
git commit -m "sneaky extra code" >/dev/null 2>&1

# Verdict still points to CODE_HEAD (stale)
# The diff between CODE_HEAD and new HEAD includes README.md
RC="$(try_push)"
assert_eq "extension with non-verdict files blocks push" "1" "$RC"

# ============================================================
# Test 12: ship.sh must NEVER modify branch protection
# ============================================================
SHIP_SH="$OPS_DIR/ship.sh"
if [ -f "$SHIP_SH" ]; then
  # No line may use gh api to change protection (PUT/PATCH/POST/DELETE on protection)
  BAD_LINES="$(grep -n 'gh api' "$SHIP_SH" 2>/dev/null | grep -i protection | grep -E '\-X\s+(PUT|PATCH|POST|DELETE)' || true)"
  if [ -n "$BAD_LINES" ]; then
    echo "  FAIL: ship.sh must not call gh api to change branch protection (found: $BAD_LINES)" >&2
    FAIL=$((FAIL + 1))
    TESTS=$((TESTS + 1))
  else
    echo "  PASS: ship.sh does not modify branch protection"
    PASS=$((PASS + 1))
    TESTS=$((TESTS + 1))
  fi
else
  echo "  SKIP: ship.sh not found"
fi

# ── Summary ──
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
