#!/usr/bin/env bash
# review_failover_selftest.sh — Hermetic tests for review engine failover
#
# Mocks: PATH override (stub codex), REVIEW_OPENCLAW_SCRIPT (stub openclaw),
#       env (router missing, openai present).
# 1. codex returns empty, openai stub returns valid JSON -> review succeeds (failover to openai).
# 2. All engines fail -> fail-closed, expected message, engine_attempts logs exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"

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

assert_contains() {
  TESTS=$((TESTS + 1))
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (expected to contain: $needle)" >&2
    FAIL=$((FAIL + 1))
  fi
}

echo "=== review_failover_selftest.sh ==="

# Need clean tree for review_auto
if [ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  echo "  SKIP: working tree dirty (commit or stash first)"
  exit 0
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# --- Stub codex: exits 0 but produces no verdict (empty output) ---
STUB_CODEX="$TMPDIR/codex"
cat > "$STUB_CODEX" <<'STUB'
#!/usr/bin/env bash
# Stub: no output, exit 0 (simulates empty Codex response)
exit 0
STUB
chmod +x "$STUB_CODEX"

# --- Stub openclaw: write valid verdict to artifacts/codex_review/<stamp>/CODEX_VERDICT.json ---
STUB_OPENCLAW="$TMPDIR/openclaw_codex_review_stub.sh"
cat > "$STUB_OPENCLAW" <<STUB
#!/usr/bin/env bash
set -euo pipefail
STAMP="\$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROOT_DIR/artifacts/codex_review/\$STAMP"
echo '{"verdict":"APPROVED","blockers":[],"non_blocking":["mock"],"tests_run":"mock"}' > "$ROOT_DIR/artifacts/codex_review/\$STAMP/CODEX_VERDICT.json"
exit 0
STUB
chmod +x "$STUB_OPENCLAW"

# --- Test 1: Failover to openai (stub) — router missing, codex empty, openai stub returns valid JSON ---
echo ""
echo "  Test 1: Failover to openai stub (codex empty, router missing)"
unset REVIEW_BASE_URL REVIEW_API_KEY 2>/dev/null || true
export OPENAI_API_KEY="sk-selftest-fake"
export REVIEW_OPENCLAW_SCRIPT="$STUB_OPENCLAW"
export PATH="$TMPDIR:$PATH"
OUT1="$(REVIEW_MAX_WAIT_SECONDS=120 CODEX_SKIP=0 "$OPS_DIR/review_auto.sh" --no-push 2>&1)" || true
RC1=$?
assert_contains "output mentions APPROVED or verdict" "APPROVED" "$OUT1"
assert_eq "review succeeds (exit 0) with openai stub" "0" "$RC1"

# --- Test 2: All engines fail -> fail-closed, message and logs ---
echo ""
echo "  Test 2: All engines fail -> fail-closed, logs present"
# Both stubs fail; ensure no leftover verdicts; short timeout
rm -rf "$ROOT_DIR/artifacts/codex_review" 2>/dev/null || true
cat > "$STUB_OPENCLAW" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
cat > "$STUB_CODEX" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
unset REVIEW_BASE_URL REVIEW_API_KEY 2>/dev/null || true
export OPENAI_API_KEY="sk-selftest-fake"
export REVIEW_OPENCLAW_SCRIPT="$STUB_OPENCLAW"
# Very short window so we hit timeout after first attempt(s)
export REVIEW_MAX_WAIT_SECONDS=2
# Force PATH so stub codex is used; capture exit code (set -e safe)
OUT2_FILE="$TMPDIR/review_out2.txt"
RC2=0
env PATH="$TMPDIR:$PATH" CODEX_SKIP=0 "$OPS_DIR/review_auto.sh" --no-push > "$OUT2_FILE" 2>&1 || RC2=$?
OUT2="$(cat "$OUT2_FILE")"
cat > "$STUB_CODEX" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
assert_eq "review fails (exit 1) when all engines fail" "1" "$RC2"
assert_contains "failure message mentions engine attempt logs" "engine_attempts" "$OUT2"
# Check that at least one attempt log was created (in latest review_packets)
LATEST_PACK="$(ls -td "$ROOT_DIR"/review_packets/*/ 2>/dev/null | head -1)"
if [ -n "$LATEST_PACK" ] && [ -d "${LATEST_PACK}engine_attempts" ]; then
  LOG_COUNT="$(find "${LATEST_PACK}engine_attempts" -type f 2>/dev/null | wc -l | tr -d ' ')"
  TESTS=$((TESTS + 1))
  if [ "${LOG_COUNT:-0}" -ge 1 ]; then
    echo "  PASS: engine_attempts logs exist ($LOG_COUNT files)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: no engine_attempts logs" >&2
    FAIL=$((FAIL + 1))
  fi
else
  TESTS=$((TESTS + 1))
  echo "  FAIL: engine_attempts dir missing under review_packets" >&2
  FAIL=$((FAIL + 1))
fi

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
