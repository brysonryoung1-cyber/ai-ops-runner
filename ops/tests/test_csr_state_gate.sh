#!/usr/bin/env bash
# test_csr_state_gate.sh — Hermetic tests for csr_state_gate.sh
#
# Creates temp fixture RESULT.json files and asserts exit codes:
#   0 = recent PASS
#   1 = stale PASS or FAIL
#   2 = no RESULT.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GATE_SCRIPT="$SCRIPT_DIR/../scripts/csr_state_gate.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1" >&2; }

TMPDIR_BASE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BASE"' EXIT

echo "=== test_csr_state_gate.sh ==="
echo ""

# --- Test 1: No RESULT.json → exit 2 ---
echo "--- Test: no RESULT.json → exit 2 ---"
EMPTY_DIR="$TMPDIR_BASE/empty/post_deploy"
mkdir -p "$EMPTY_DIR"
rc=0
CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/empty" "$GATE_SCRIPT" 15 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 2 ]; then pass "no RESULT.json → exit 2"; else fail "expected exit 2, got $rc"; fi

# --- Test 2: Recent PASS → exit 0 ---
echo "--- Test: recent PASS → exit 0 ---"
RECENT_DIR="$TMPDIR_BASE/recent/post_deploy/proof_recent"
mkdir -p "$RECENT_DIR"
python3 -c "
import json
from datetime import datetime, timezone
d = {'overall': 'PASS', 'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': 'test'}
with open('$RECENT_DIR/RESULT.json', 'w') as f:
    json.dump(d, f)
"
rc=0
CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/recent" "$GATE_SCRIPT" 15 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then pass "recent PASS → exit 0"; else fail "expected exit 0, got $rc"; fi

# --- Test 3: Stale PASS (> threshold) → exit 1 ---
echo "--- Test: stale PASS → exit 1 ---"
STALE_DIR="$TMPDIR_BASE/stale/post_deploy/proof_stale"
mkdir -p "$STALE_DIR"
python3 -c "
import json
from datetime import datetime, timezone
d = {'overall': 'PASS', 'timestamp': '2025-01-01T00:00:00Z', 'run_id': 'stale'}
with open('$STALE_DIR/RESULT.json', 'w') as f:
    json.dump(d, f)
"
# Backdate mtime to make it stale (> 1 min ago, test with threshold=0)
touch -t 202501010000 "$STALE_DIR/RESULT.json" 2>/dev/null || true
rc=0
CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/stale" "$GATE_SCRIPT" 0 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 1 ]; then pass "stale PASS (threshold=0) → exit 1"; else fail "expected exit 1, got $rc"; fi

# --- Test 4: Recent FAIL → exit 1 ---
echo "--- Test: recent FAIL → exit 1 ---"
FAIL_DIR="$TMPDIR_BASE/failing/post_deploy/proof_fail"
mkdir -p "$FAIL_DIR"
python3 -c "
import json
from datetime import datetime, timezone
d = {'overall': 'FAILURE', 'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': 'fail'}
with open('$FAIL_DIR/RESULT.json', 'w') as f:
    json.dump(d, f)
"
rc=0
CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/failing" "$GATE_SCRIPT" 15 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 1 ]; then pass "recent FAIL → exit 1"; else fail "expected exit 1, got $rc"; fi

# --- Test 5: post_deploy dir missing entirely → exit 2 ---
echo "--- Test: post_deploy dir missing → exit 2 ---"
rc=0
CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/nonexistent" "$GATE_SCRIPT" 15 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 2 ]; then pass "missing post_deploy dir → exit 2"; else fail "expected exit 2, got $rc"; fi

# --- Test 6: Single-line output ---
echo "--- Test: output is single line ---"
RECENT2_DIR="$TMPDIR_BASE/recent2/post_deploy/proof_r2"
mkdir -p "$RECENT2_DIR"
python3 -c "
import json
from datetime import datetime, timezone
d = {'overall': 'PASS', 'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': 'r2'}
with open('$RECENT2_DIR/RESULT.json', 'w') as f:
    json.dump(d, f)
"
OUTPUT=$(CSR_ARTIFACTS_ROOT="$TMPDIR_BASE/recent2" "$GATE_SCRIPT" 15 2>/dev/null)
LINE_COUNT=$(echo "$OUTPUT" | wc -l | tr -d ' ')
if [ "$LINE_COUNT" -eq 1 ]; then pass "output is single line"; else fail "expected 1 line, got $LINE_COUNT"; fi

echo ""
echo "================================"
echo "test_csr_state_gate: $PASS passed, $FAIL failed"
echo "================================"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
