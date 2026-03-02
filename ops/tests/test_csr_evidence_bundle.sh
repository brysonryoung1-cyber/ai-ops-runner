#!/usr/bin/env bash
# test_csr_evidence_bundle.sh — Hermetic tests for csr_evidence_bundle.sh
#
# Creates fixture triage.json + log files and asserts:
#   - evidence_bundle.json is created with required keys
#   - tail snippets are present and capped
#   - missing artifacts are recorded
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_SCRIPT="$SCRIPT_DIR/../scripts/csr_evidence_bundle.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1" >&2; }

TMPDIR_BASE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BASE"' EXIT

echo "=== test_csr_evidence_bundle.sh ==="
echo ""

# --- Fixture: triage dir with triage.json and log files ---
TRIAGE_DIR="$TMPDIR_BASE/deploy/run_001"
mkdir -p "$TRIAGE_DIR"

# Create a small log file (< 2KB)
for i in $(seq 1 50); do echo "log line $i: some deployment output here" >> "$TRIAGE_DIR/console_build.log"; done

# Create a bigger log (> 2KB to test cap)
python3 -c "
with open('$TRIAGE_DIR/big.log', 'w') as f:
    for i in range(500):
        f.write(f'big log line {i}: ' + 'x' * 80 + '\n')
"

# Create triage.json
python3 -c "
import json
t = {
    'run_id': 'run_001',
    'attempt': 1,
    'error_class': 'console_build_failed',
    'retryable': False,
    'failing_step': 'deploy_pipeline',
    'recommended_next_action': 'Fix console build',
    'artifact_pointers': {
        'deploy_result': '$TRIAGE_DIR/deploy_result.json',
        'build_logs': '$TRIAGE_DIR/console_build.log',
        'dod_result': None,
        'big_log': '$TRIAGE_DIR/big.log',
        'missing_file': '$TRIAGE_DIR/does_not_exist.log'
    },
    'timestamp': '2026-03-02T00:00:00Z'
}
with open('$TRIAGE_DIR/triage.json', 'w') as f:
    json.dump(t, f, indent=2)
"

# Create deploy_result.json (referenced by triage)
echo '{"overall": "FAILURE", "error_class": "console_build_failed"}' > "$TRIAGE_DIR/deploy_result.json"

# --- Test 1: Bundle created from triage dir ---
echo "--- Test: bundle from triage dir ---"
BUNDLE_PATH=$("$BUNDLE_SCRIPT" "$TRIAGE_DIR" 30 2048 2>/dev/null)
if [ -f "$TRIAGE_DIR/evidence_bundle.json" ]; then
  pass "evidence_bundle.json created"
else
  fail "evidence_bundle.json not created"
fi

# --- Test 2: Required keys present ---
echo "--- Test: required keys ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
required = ['error_class', 'retryable', 'failing_step', 'recommended_next_action', 'artifact_pointers', 'tail_snippets', 'meta']
missing = [k for k in required if k not in b]
if missing:
    print('FAIL: missing keys: ' + ', '.join(missing))
    sys.exit(1)
print('PASS')
" && pass "all required keys present" || fail "missing required keys"

# --- Test 3: error_class matches triage ---
echo "--- Test: error_class matches ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
assert b['error_class'] == 'console_build_failed', f'expected console_build_failed, got {b[\"error_class\"]}'
print('PASS')
" && pass "error_class == console_build_failed" || fail "error_class mismatch"

# --- Test 4: tail snippets present for existing files ---
echo "--- Test: tail snippets present ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
s = b['tail_snippets']
assert 'build_logs' in s, 'build_logs snippet missing'
assert 'deploy_result' in s, 'deploy_result snippet missing'
assert 'big_log' in s, 'big_log snippet missing'
assert len(s['build_logs']) > 0, 'build_logs snippet empty'
print('PASS')
" && pass "tail snippets present for existing files" || fail "tail snippets issue"

# --- Test 5: big log snippet is capped at 2KB ---
echo "--- Test: big log snippet capped ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
snippet = b['tail_snippets']['big_log']
size = len(snippet.encode('utf-8'))
assert size <= 2048, f'big_log snippet too large: {size} bytes (max 2048)'
print(f'PASS ({size} bytes)')
" && pass "big log snippet capped at 2KB" || fail "big log snippet not capped"

# --- Test 6: missing artifacts recorded ---
echo "--- Test: missing artifacts recorded ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
assert 'missing_file' in b.get('missing_artifacts', []), 'missing_file not in missing_artifacts'
print('PASS')
" && pass "missing_file in missing_artifacts list" || fail "missing artifacts not recorded"

# --- Test 7: meta fields ---
echo "--- Test: meta fields ---"
python3 -c "
import json, sys
with open('$TRIAGE_DIR/evidence_bundle.json') as f:
    b = json.load(f)
m = b['meta']
assert 'created_at' in m, 'meta.created_at missing'
assert m['bundle_version'] == '1.0', 'bundle_version wrong'
assert m['tail_lines'] == 30, 'tail_lines wrong'
assert m['max_bytes_per_snippet'] == 2048, 'max_bytes wrong'
print('PASS')
" && pass "meta fields correct" || fail "meta fields issue"

# --- Test 8: Bundle from triage.json path (not dir) ---
echo "--- Test: bundle from triage.json path ---"
rm -f "$TRIAGE_DIR/evidence_bundle.json"
"$BUNDLE_SCRIPT" "$TRIAGE_DIR/triage.json" 30 2048 >/dev/null 2>&1
if [ -f "$TRIAGE_DIR/evidence_bundle.json" ]; then
  pass "bundle from triage.json path works"
else
  fail "bundle from triage.json path failed"
fi

# --- Test 9: Missing triage.json → exit 1 ---
echo "--- Test: missing triage.json → exit 1 ---"
rc=0
"$BUNDLE_SCRIPT" "$TMPDIR_BASE/nonexistent" 30 2048 >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 1 ]; then pass "missing triage.json → exit 1"; else fail "expected exit 1, got $rc"; fi

echo ""
echo "================================"
echo "test_csr_evidence_bundle: $PASS passed, $FAIL failed"
echo "================================"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
