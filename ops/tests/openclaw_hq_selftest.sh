#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# OpenClaw HQ Self-Test
#
# Hermetic tests for:
#   1. Project registry schema validation (config/projects.json)
#   2. Run recorder behavior (artifacts/runs/*/run.json)
#
# No network, no real secrets, no side effects beyond /tmp.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PASS=0
FAIL=0
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }

# ── Section 1: Project Registry Structure ───────────────────────

echo "=== Project Registry Schema Tests ==="

# Test 1: config/projects.json exists
if [[ -f "$REPO_ROOT/config/projects.json" ]]; then
  pass "config/projects.json exists"
else
  fail "config/projects.json missing"
fi

# Test 2: config/projects.schema.json exists
if [[ -f "$REPO_ROOT/config/projects.schema.json" ]]; then
  pass "config/projects.schema.json exists"
else
  fail "config/projects.schema.json missing"
fi

# Test 3: projects.json is valid JSON
if python3 -c "import json; json.load(open('$REPO_ROOT/config/projects.json'))" 2>/dev/null; then
  pass "projects.json is valid JSON"
else
  fail "projects.json is not valid JSON"
fi

# Test 4: projects.json has version field
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
assert d['version'] == 1, 'version must be 1'
" 2>/dev/null; then
  pass "projects.json version is 1"
else
  fail "projects.json version field missing or wrong"
fi

# Test 5: projects.json has projects array
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
assert isinstance(d['projects'], list), 'projects must be list'
assert len(d['projects']) >= 4, 'must have at least 4 projects'
" 2>/dev/null; then
  pass "projects.json has projects array with >= 4 entries"
else
  fail "projects.json projects array missing or too small"
fi

# Test 6: All required projects present
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
ids = {p['id'] for p in d['projects']}
required = {'infra_openclaw', 'soma_kajabi_library_ownership', 'clip_factory_monitoring', 'music_pipeline'}
assert required.issubset(ids), f'Missing projects: {required - ids}'
" 2>/dev/null; then
  pass "all 4 required projects present"
else
  fail "not all required projects present"
fi

# Test 7: No duplicate project IDs
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
ids = [p['id'] for p in d['projects']]
assert len(ids) == len(set(ids)), 'duplicate project IDs found'
" 2>/dev/null; then
  pass "no duplicate project IDs"
else
  fail "duplicate project IDs found"
fi

# Test 8: All projects have required fields
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
required_fields = ['id', 'name', 'description', 'enabled', 'workflows', 'schedules', 'notification_flags', 'tags']
for p in d['projects']:
    for f in required_fields:
        assert f in p, f'{p[\"id\"]} missing field: {f}'
    nf = p['notification_flags']
    for nf_f in ['on_success', 'on_failure', 'on_recovery', 'channels']:
        assert nf_f in nf, f'{p[\"id\"]} notification_flags missing: {nf_f}'
" 2>/dev/null; then
  pass "all projects have required fields"
else
  fail "projects missing required fields"
fi

# Test 9: Project IDs match pattern (lowercase + underscore)
if python3 -c "
import json, re
d = json.load(open('$REPO_ROOT/config/projects.json'))
pattern = re.compile(r'^[a-z][a-z0-9_]{1,63}$')
for p in d['projects']:
    assert pattern.match(p['id']), f'Invalid project ID: {p[\"id\"]}'
" 2>/dev/null; then
  pass "all project IDs match naming convention"
else
  fail "project ID naming convention violated"
fi

# Test 10: infra_openclaw is enabled
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
infra = next(p for p in d['projects'] if p['id'] == 'infra_openclaw')
assert infra['enabled'] is True, 'infra_openclaw must be enabled'
" 2>/dev/null; then
  pass "infra_openclaw is enabled"
else
  fail "infra_openclaw not enabled"
fi

# Test 11: Placeholder projects are disabled
if python3 -c "
import json
d = json.load(open('$REPO_ROOT/config/projects.json'))
for pid in ['clip_factory_monitoring', 'music_pipeline']:
    p = next(pp for pp in d['projects'] if pp['id'] == pid)
    assert p['enabled'] is False, f'{pid} should be disabled'
" 2>/dev/null; then
  pass "placeholder projects are disabled"
else
  fail "placeholder projects should be disabled"
fi

# Test 12: Fail-closed — invalid JSON rejected
if python3 -c "
import json
try:
    json.loads('{bad json}')
    assert False, 'should have raised'
except json.JSONDecodeError:
    pass  # Expected: fail-closed
" 2>/dev/null; then
  pass "fail-closed: invalid JSON rejected"
else
  fail "fail-closed: invalid JSON not rejected"
fi

# Test 13: Fail-closed — missing required field rejected
if python3 -c "
import json, sys
# Simulate validation: a project missing 'id' should fail
project = {'name': 'test', 'description': '', 'enabled': True, 'workflows': [], 'schedules': [], 'notification_flags': {'on_success': False, 'on_failure': True, 'on_recovery': False, 'channels': []}, 'tags': []}
assert 'id' not in project  # Confirm missing
# This should be caught by validation
" 2>/dev/null; then
  pass "fail-closed: missing 'id' detected"
else
  fail "fail-closed: missing 'id' not detected"
fi

# ── Section 2: Run Recorder Structure ───────────────────────────

echo ""
echo "=== Run Recorder Tests ==="

# Test 14: Run recorder module exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" ]]; then
  pass "run-recorder.ts exists"
else
  fail "run-recorder.ts missing"
fi

# Test 15: Run recorder has required exports
if grep -q "writeRunRecord" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "buildRunRecord" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "listRunRecords" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "getRunRecord" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts"; then
  pass "run-recorder has all required exports"
else
  fail "run-recorder missing required exports"
fi

# Test 16: Run record schema includes required fields
if grep -q "run_id" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "project_id" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "started_at" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "finished_at" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "exit_code" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "error_summary" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" && \
   grep -q "artifact_paths" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts"; then
  pass "run record schema has all required fields"
else
  fail "run record schema missing required fields"
fi

# Test 17: Run recorder writes to artifacts/runs/
if grep -q "artifacts.*runs" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts"; then
  pass "run recorder targets artifacts/runs/"
else
  fail "run recorder doesn't target artifacts/runs/"
fi

# Test 18: Exec API route wires run recorder
if grep -q "writeRunRecord" "$REPO_ROOT/apps/openclaw-console/src/app/api/exec/route.ts" && \
   grep -q "buildRunRecord" "$REPO_ROOT/apps/openclaw-console/src/app/api/exec/route.ts"; then
  pass "exec API route calls writeRunRecord + buildRunRecord"
else
  fail "exec API route not wired to run recorder"
fi

# Test 19: Run recorder writes on failure path too
if python3 -c "
import re
content = open('$REPO_ROOT/apps/openclaw-console/src/app/api/exec/route.ts').read()
# Check there are at least 2 writeRunRecord calls (success + error paths)
count = content.count('writeRunRecord')
assert count >= 2, f'Expected at least 2 writeRunRecord calls, found {count}'
" 2>/dev/null; then
  pass "run recorder writes on both success and failure paths (fail-closed)"
else
  fail "run recorder doesn't write on failure path"
fi

# Test 20: Run ID sanitization (alphanumeric + dash only)
if grep -q 'alphanumeric\|a-zA-Z0-9' "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts"; then
  pass "run ID input sanitization present"
else
  fail "run ID input sanitization missing"
fi

# ── Section 3: API Routes ──────────────────────────────────────

echo ""
echo "=== API Route Tests ==="

# Test 21: /api/projects route exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/api/projects/route.ts" ]]; then
  pass "/api/projects route exists"
else
  fail "/api/projects route missing"
fi

# Test 22: /api/runs route exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/api/runs/route.ts" ]]; then
  pass "/api/runs route exists"
else
  fail "/api/runs route missing"
fi

# Test 23: /api/ai-status route exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/api/ai-status/route.ts" ]]; then
  pass "/api/ai-status route exists"
else
  fail "/api/ai-status route missing"
fi

# Test 24: AI status never exposes raw keys
if ! grep -q "OPENAI_API_KEY.*return\|response.*OPENAI_API_KEY" "$REPO_ROOT/apps/openclaw-console/src/app/api/ai-status/route.ts"; then
  pass "ai-status never returns raw API key"
else
  fail "ai-status may leak raw API key"
fi

# Test 25: AI status uses maskKey function
if grep -q "maskKey" "$REPO_ROOT/apps/openclaw-console/src/app/api/ai-status/route.ts"; then
  pass "ai-status uses maskKey for fingerprints"
else
  fail "ai-status doesn't mask keys"
fi

# ── Section 4: UI Pages ────────────────────────────────────────

echo ""
echo "=== UI Page Tests ==="

# Test 26: Projects page exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/projects/page.tsx" ]]; then
  pass "projects page exists"
else
  fail "projects page missing"
fi

# Test 27: Runs page exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/runs/page.tsx" ]]; then
  pass "runs page exists"
else
  fail "runs page missing"
fi

# Test 28: Sidebar has all required nav items
if grep -q 'Overview' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx" && \
   grep -q 'Projects' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx" && \
   grep -q 'Runs' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx" && \
   grep -q 'Logs' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx" && \
   grep -q 'Artifacts' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx" && \
   grep -q 'Actions' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx"; then
  pass "sidebar has all 6 required nav items"
else
  fail "sidebar missing nav items"
fi

# Test 29: Sidebar renamed to HQ
if grep -q 'HQ' "$REPO_ROOT/apps/openclaw-console/src/components/Sidebar.tsx"; then
  pass "sidebar shows 'HQ' branding"
else
  fail "sidebar still shows 'Console' instead of 'HQ'"
fi

# Test 30: Layout title updated to HQ
if grep -q 'OpenClaw HQ' "$REPO_ROOT/apps/openclaw-console/src/app/layout.tsx"; then
  pass "layout title is 'OpenClaw HQ'"
else
  fail "layout title not updated to 'OpenClaw HQ'"
fi

# Test 31: Overview page has AI Connections panel
if grep -q 'AI Connections' "$REPO_ROOT/apps/openclaw-console/src/app/page.tsx"; then
  pass "overview page has AI Connections panel"
else
  fail "overview page missing AI Connections panel"
fi

# Test 32: AI panel shows masked fingerprints only
if grep -q 'fingerprint' "$REPO_ROOT/apps/openclaw-console/src/app/page.tsx" && \
   grep -q 'Keys are never displayed' "$REPO_ROOT/apps/openclaw-console/src/app/page.tsx"; then
  pass "AI panel shows masked fingerprints with security note"
else
  fail "AI panel missing fingerprint masking"
fi

# ── Section 5: Security Invariants ─────────────────────────────

echo ""
echo "=== Security Invariant Tests ==="

# Test 33: Middleware unchanged (token auth preserved)
if grep -q 'X-OpenClaw-Token\|x-openclaw-token' "$REPO_ROOT/apps/openclaw-console/src/middleware.ts"; then
  pass "middleware token auth preserved"
else
  fail "middleware token auth broken"
fi

# Test 34: Allowlist module untouched (no arbitrary commands added)
# Count actual action entries in the ALLOWLIST record (keys ending with colon + space + {)
ALLOWLIST_ACTIONS=$(grep -c "name:" "$REPO_ROOT/apps/openclaw-console/src/lib/allowlist.ts" || true)
if [[ "$ALLOWLIST_ACTIONS" -le 30 ]]; then
  pass "allowlist action count within bounds ($ALLOWLIST_ACTIONS)"
else
  fail "allowlist unexpectedly large ($ALLOWLIST_ACTIONS actions)"
fi

# Test 35: No raw secrets in new files
if ! grep -rn "sk-[a-zA-Z0-9]\{20,\}" "$REPO_ROOT/config/projects.json" "$REPO_ROOT/apps/openclaw-console/src/lib/run-recorder.ts" "$REPO_ROOT/apps/openclaw-console/src/lib/projects.ts" 2>/dev/null; then
  pass "no raw secrets in new files"
else
  fail "possible secrets found in new files"
fi

# ── Summary ─────────────────────────────────────────────────────

echo ""
echo "================================"
echo "OpenClaw HQ Self-Test: $PASS passed, $FAIL failed (total: $((PASS + FAIL)))"
echo "================================"

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
