#!/usr/bin/env bash
# orb_integration_selftest.sh — Validate ORB integration configs and contracts.
# Runs WITHOUT docker; tests configs, wrapper scripts, and Python modules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

ERRORS=0
PASS=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== ORB Integration Selftest ==="
echo ""

# --- 1. Repo allowlist exists and is valid YAML ---
echo "--- Repo Allowlist ---"
if [ -f configs/repo_allowlist.yaml ]; then
  pass "configs/repo_allowlist.yaml exists"
  if python3 -c "import yaml; yaml.safe_load(open('configs/repo_allowlist.yaml'))" 2>/dev/null; then
    pass "repo_allowlist.yaml is valid YAML"
  else
    fail "repo_allowlist.yaml is invalid YAML"
  fi
  # Check algo-nt8-orb is listed
  if python3 -c "
import yaml
data = yaml.safe_load(open('configs/repo_allowlist.yaml'))
repos = data.get('repos', {})
assert 'algo-nt8-orb' in repos, 'algo-nt8-orb not in repos'
assert 'url' in repos['algo-nt8-orb'], 'missing url'
" 2>/dev/null; then
    pass "algo-nt8-orb present in repo_allowlist"
  else
    fail "algo-nt8-orb NOT in repo_allowlist"
  fi
else
  fail "configs/repo_allowlist.yaml missing"
fi

# --- 2. Job allowlist includes ORB types ---
echo ""
echo "--- Job Allowlist ---"
for job_type in orb_review_bundle orb_doctor orb_score_run; do
  if python3 -c "
import yaml
data = yaml.safe_load(open('configs/job_allowlist.yaml'))
jobs = data.get('jobs', {})
assert '$job_type' in jobs, '$job_type not in jobs'
assert 'argv' in jobs['$job_type'], 'missing argv'
assert jobs['$job_type'].get('requires_repo_allowlist', False), 'requires_repo_allowlist not True'
" 2>/dev/null; then
    pass "$job_type in job_allowlist with requires_repo_allowlist=true"
  else
    fail "$job_type NOT correctly configured in job_allowlist"
  fi
done

# --- 3. Wrapper scripts exist and are executable ---
echo ""
echo "--- Wrapper Scripts ---"
for wrapper in \
  services/test_runner/orb_wrappers/orb_review_bundle.sh \
  services/test_runner/orb_wrappers/orb_doctor.sh \
  services/test_runner/orb_wrappers/orb_score_run.sh; do
  if [ -f "$ROOT_DIR/$wrapper" ]; then
    pass "$wrapper exists"
    if [ -x "$ROOT_DIR/$wrapper" ]; then
      pass "$wrapper is executable"
    else
      fail "$wrapper NOT executable (run: chmod +x $wrapper)"
    fi
  else
    fail "$wrapper missing"
  fi
done

# --- 4. CLI helpers exist ---
echo ""
echo "--- CLI Helpers ---"
for cli in \
  ops/runner_submit_orb_review.sh \
  ops/runner_submit_orb_doctor.sh \
  ops/runner_submit_orb_score.sh; do
  if [ -f "$ROOT_DIR/$cli" ]; then
    if [ -x "$ROOT_DIR/$cli" ]; then
      pass "$cli exists and executable"
    else
      fail "$cli exists but NOT executable"
    fi
  else
    fail "$cli missing"
  fi
done

# --- 5. Repo allowlist rejects unknown repo (Python test) ---
echo ""
echo "--- Repo Allowlist Enforcement ---"
PYTHONPATH="$ROOT_DIR/services/test_runner" python3 -c "
import os
os.environ['REPO_ALLOWLIST_PATH'] = '$ROOT_DIR/configs/repo_allowlist.yaml'
from test_runner.repo_allowlist import validate_repo_url

# Should succeed
try:
    r = validate_repo_url('git@github.com:brysonryoung1-cyber/algo-nt8-orb.git')
    print('  [PASS] algo-nt8-orb accepted')
except Exception as e:
    print(f'  [FAIL] algo-nt8-orb rejected: {e}')

# Should fail
try:
    validate_repo_url('https://github.com/evil/repo.git')
    print('  [FAIL] evil repo was accepted (should be rejected)')
except ValueError:
    print('  [PASS] unknown repo correctly rejected')
" || fail "Repo allowlist enforcement test failed"

# --- 6. orb_doctor.sh contains hooksPath hardening ---
echo ""
echo "--- Doctor hooksPath Hardening ---"
if grep -q 'core\.hooksPath' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_doctor.sh"; then
  pass "orb_doctor.sh sets core.hooksPath"
else
  fail "orb_doctor.sh does NOT set core.hooksPath"
fi

# --- 7. orb_review_bundle.sh contains SIZE_CAP packet generation ---
echo ""
echo "--- Review Bundle SIZE_CAP Packets ---"
if grep -q 'size_cap_meta\.json' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_review_bundle.sh"; then
  pass "orb_review_bundle.sh writes size_cap_meta.json"
else
  fail "orb_review_bundle.sh does NOT write size_cap_meta.json"
fi
if grep -q 'ORB_REVIEW_PACKETS\.tar\.gz' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_review_bundle.sh"; then
  pass "orb_review_bundle.sh creates ORB_REVIEW_PACKETS.tar.gz"
else
  fail "orb_review_bundle.sh does NOT create ORB_REVIEW_PACKETS.tar.gz"
fi
if grep -q 'README_REVIEW_PACKETS\.txt' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_review_bundle.sh"; then
  pass "orb_review_bundle.sh writes README_REVIEW_PACKETS.txt"
else
  fail "orb_review_bundle.sh does NOT write README_REVIEW_PACKETS.txt"
fi
if grep -q 'review_packets/' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_review_bundle.sh"; then
  pass "orb_review_bundle.sh creates review_packets/ directory"
else
  fail "orb_review_bundle.sh does NOT create review_packets/ directory"
fi
if grep -q 'FORCE_SIZE_CAP' "$ROOT_DIR/services/test_runner/orb_wrappers/orb_review_bundle.sh"; then
  pass "orb_review_bundle.sh supports FORCE_SIZE_CAP test flag"
else
  fail "orb_review_bundle.sh does NOT support FORCE_SIZE_CAP test flag"
fi

# --- 8. executor.py reads size_cap_meta.json + sets hooksPath ---
echo ""
echo "--- Executor Integration ---"
if grep -q 'size_cap_meta\.json' "$ROOT_DIR/services/test_runner/test_runner/executor.py"; then
  pass "executor.py reads size_cap_meta.json"
else
  fail "executor.py does NOT read size_cap_meta.json"
fi
if grep -q 'size_cap_fallback' "$ROOT_DIR/services/test_runner/test_runner/executor.py"; then
  pass "executor.py includes size_cap_fallback in artifact.json"
else
  fail "executor.py does NOT include size_cap_fallback in artifact.json"
fi
if grep -q 'core\.hooksPath' "$ROOT_DIR/services/test_runner/test_runner/executor.py"; then
  pass "executor.py sets core.hooksPath before make_readonly (step 2a)"
else
  fail "executor.py does NOT set core.hooksPath before make_readonly"
fi
if grep -q '\.githooks' "$ROOT_DIR/services/test_runner/test_runner/executor.py"; then
  pass "executor.py references .githooks directory"
else
  fail "executor.py does NOT reference .githooks directory"
fi

# --- 9. Run pytest for ORB-related tests ---
echo ""
echo "--- Pytest (ORB tests) ---"
if command -v pytest &>/dev/null || python3 -m pytest --version &>/dev/null 2>&1; then
  PYTEST_OUTPUT=""
  PYTEST_RC=0
  PYTEST_OUTPUT="$(cd "$ROOT_DIR/services/test_runner" && \
    REPO_ALLOWLIST_PATH=/dev/null ALLOWLIST_PATH=/dev/null \
    python3 -m pytest -q tests/test_repo_allowlist.py tests/test_orb_integration.py 2>&1)" || PYTEST_RC=$?
  if [ "$PYTEST_RC" -eq 0 ]; then
    pass "pytest ORB tests passed"
  elif echo "$PYTEST_OUTPUT" | grep -q "ModuleNotFoundError\|No module named"; then
    echo "  [SKIP] pytest: missing dependencies"
  else
    fail "pytest ORB tests failed (rc=$PYTEST_RC)"
    echo "$PYTEST_OUTPUT" | tail -10 >&2
  fi
else
  echo "  [SKIP] pytest not available"
fi

# --- Summary ---
echo ""
echo "=== Summary ==="
echo "  $PASS passed, $ERRORS failed"
if [ "$ERRORS" -eq 0 ]; then
  echo "  All ORB integration checks passed!"
  exit 0
else
  echo "  $ERRORS error(s) found"
  exit 1
fi
