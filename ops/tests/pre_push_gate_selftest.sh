#!/usr/bin/env bash
# pre_push_gate_selftest.sh — Prove the pre-push gate is TRULY unbreakable
#
# Tests run in an isolated temp worktree (bare origin + clone).
# No side effects on the real repo.
#
# Tests:
#   1. Simulated verdict (meta.simulated=true)  → push BLOCKED
#   2. Null codex_cli with simulated=false       → push BLOCKED
#   3. Wrong since_sha                           → push BLOCKED
#   4. Wrong to_sha                              → push BLOCKED
#   5. Correct real verdict                      → push ALLOWED
#   6. No verdict file at all                    → push BLOCKED
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"
PRE_PUSH_HOOK="$ROOT_DIR/.githooks/pre-push"

PASS=0
FAIL=0
TESTS=0

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

echo "=== pre_push_gate_selftest.sh ==="

# --- verify pre-push hook exists ---
if [ ! -f "$PRE_PUSH_HOOK" ]; then
  echo "  FAIL: pre-push hook not found at $PRE_PUSH_HOOK" >&2
  exit 1
fi

# --- setup isolated test repos ---
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

ORIGIN="$TMPDIR/origin.git"
CLONE="$TMPDIR/clone"

git init --bare "$ORIGIN" >/dev/null 2>&1

# Clone and set up initial state
git clone "$ORIGIN" "$CLONE" >/dev/null 2>&1
cd "$CLONE"
git checkout -b main >/dev/null 2>&1 || true

# Initial commit
echo "initial" > README.md
mkdir -p docs
echo "init_placeholder" > docs/LAST_REVIEWED_SHA.txt
git add -A
git commit -m "initial commit" >/dev/null 2>&1
git push -u origin main >/dev/null 2>&1

INIT_SHA="$(git rev-parse HEAD)"

# Install the pre-push hook from the real repo
mkdir -p .git/hooks
cp "$PRE_PUSH_HOOK" .git/hooks/pre-push
chmod +x .git/hooks/pre-push

# Make a change that needs review
echo "change requiring review" >> README.md
git add README.md
git commit -m "change to review" >/dev/null 2>&1

HEAD_SHA="$(git rev-parse HEAD)"
BASE_SHA="$(git merge-base HEAD origin/main)"

echo "  Test repo: $CLONE"
echo "  BASE: ${BASE_SHA:0:12}"
echo "  HEAD: ${HEAD_SHA:0:12}"
echo ""

# --- helper: place a verdict file ---
write_verdict() {
  rm -rf "$CLONE/review_packets" 2>/dev/null || true
  local dir="$CLONE/review_packets/test_$(date +%s)_$RANDOM"
  mkdir -p "$dir"
  echo "$1" > "$dir/CODEX_VERDICT.json"
}

# --- helper: attempt push, return exit code ---
try_push() {
  local ship="${1:-0}"
  local hmac_key="${2:-}"
  local rc=0
  if [ "$ship" = "1" ] && [ -n "$hmac_key" ]; then
    OPENCLAW_SHIP=1 VERDICT_HMAC_KEY="$hmac_key" git push origin main >/dev/null 2>&1 || rc=$?
  else
    git push origin main >/dev/null 2>&1 || rc=$?
  fi
  echo "$rc"
}

# --- helper: write canonical verdict (v2 gate) and commit ---
TEST_HMAC_KEY="pre-push-selftest-key-$(date +%s)"
write_canonical_verdict_and_commit() {
  local head_sha="$1"
  python3 - "$CLONE/docs/LAST_APPROVED_VERDICT.json" "$head_sha" "$TEST_HMAC_KEY" <<'PYEOF'
import json, sys, hmac, hashlib
vfile, head_sha, key = sys.argv[1], sys.argv[2], sys.argv[3]
data = {
    "approved_head_sha": head_sha,
    "range_start_sha": head_sha,
    "range_end_sha": head_sha,
    "simulated": False,
    "engine": "codex_cli",
    "model": "test",
    "created_at": "2026-01-01T00:00:00Z",
    "verdict_artifact_path": "review_packets/test/CODEX_VERDICT.json",
    "signature": ""
}
payload = {k: v for k, v in sorted(data.items()) if k != "signature"}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
data["signature"] = hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()
with open(vfile, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
  git add docs/LAST_APPROVED_VERDICT.json
  git commit -m "chore: canonical verdict" >/dev/null 2>&1
}

# ============================================================
# Test 1: Simulated verdict → push MUST FAIL
# ============================================================
write_verdict '{
  "verdict": "APPROVED",
  "blockers": [],
  "non_blocking": [],
  "tests_run": "simulated",
  "meta": {
    "since_sha": "'"$BASE_SHA"'",
    "to_sha": "'"$HEAD_SHA"'",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": true,
    "codex_cli": null
  }
}'
RC="$(try_push 0)"
assert_eq "simulated verdict (meta.simulated=true) blocks push" "1" "$RC"

# ============================================================
# Test 2: Null codex_cli with simulated=false → push MUST FAIL
# ============================================================
write_verdict '{
  "verdict": "APPROVED",
  "blockers": [],
  "non_blocking": [],
  "tests_run": "test",
  "meta": {
    "since_sha": "'"$BASE_SHA"'",
    "to_sha": "'"$HEAD_SHA"'",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": false,
    "codex_cli": null
  }
}'
RC="$(try_push 0)"
assert_eq "null codex_cli with simulated=false blocks push" "1" "$RC"

# ============================================================
# Test 3: Wrong since_sha → push MUST FAIL
# ============================================================
write_verdict '{
  "verdict": "APPROVED",
  "blockers": [],
  "non_blocking": [],
  "tests_run": "test",
  "meta": {
    "since_sha": "0000000000000000000000000000000000000bad",
    "to_sha": "'"$HEAD_SHA"'",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": false,
    "codex_cli": {"version": "1.0.0", "command": "codex exec"}
  }
}'
RC="$(try_push 0)"
assert_eq "wrong since_sha blocks push" "1" "$RC"

# ============================================================
# Test 4: Wrong to_sha → push MUST FAIL
# ============================================================
write_verdict '{
  "verdict": "APPROVED",
  "blockers": [],
  "non_blocking": [],
  "tests_run": "test",
  "meta": {
    "since_sha": "'"$BASE_SHA"'",
    "to_sha": "0000000000000000000000000000000000000bad",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": false,
    "codex_cli": {"version": "1.0.0", "command": "codex exec"}
  }
}'
RC="$(try_push 0)"
assert_eq "wrong to_sha blocks push" "1" "$RC"

# ============================================================
# Test 5: Correct real verdict (v2 gate) → push MUST SUCCEED
# ============================================================
# v2 gate requires OPENCLAW_SHIP=1 and docs/LAST_APPROVED_VERDICT.json with valid HMAC
mkdir -p "$CLONE/docs"
write_canonical_verdict_and_commit "$HEAD_SHA"
RC="$(try_push 1 "$TEST_HMAC_KEY")"
assert_eq "correct real verdict allows push" "0" "$RC"

# ============================================================
# Test 6: No verdict file → push MUST FAIL
# ============================================================
# After test 5 succeeded, origin/main is now at HEAD_SHA.
# Make a new commit so there's something to push.
echo "another change" >> README.md
git add README.md
git commit -m "another change" >/dev/null 2>&1
rm -rf "$CLONE/review_packets"
RC="$(try_push 0)"
assert_eq "no verdict file blocks push" "1" "$RC"

# ============================================================
# Test 7: BLOCKED verdict → push MUST FAIL
# ============================================================
NEW_HEAD="$(git rev-parse HEAD)"
NEW_BASE="$(git merge-base HEAD origin/main)"
write_verdict '{
  "verdict": "BLOCKED",
  "blockers": ["test blocker"],
  "non_blocking": [],
  "tests_run": "test",
  "meta": {
    "since_sha": "'"$NEW_BASE"'",
    "to_sha": "'"$NEW_HEAD"'",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": false,
    "codex_cli": {"version": "1.0.0", "command": "codex exec"}
  }
}'
RC="$(try_push 0)"
assert_eq "BLOCKED verdict blocks push" "1" "$RC"

# ============================================================
# Test 8: Empty codex_cli.version → push MUST FAIL
# ============================================================
write_verdict '{
  "verdict": "APPROVED",
  "blockers": [],
  "non_blocking": [],
  "tests_run": "test",
  "meta": {
    "since_sha": "'"$NEW_BASE"'",
    "to_sha": "'"$NEW_HEAD"'",
    "generated_at": "2026-01-01T00:00:00Z",
    "review_mode": "bundle",
    "simulated": false,
    "codex_cli": {"version": "", "command": "codex exec"}
  }
}'
RC="$(try_push 0)"
assert_eq "empty codex_cli.version blocks push" "1" "$RC"

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
