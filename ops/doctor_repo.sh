#!/usr/bin/env bash
# doctor_repo.sh — Verify repo health: hooks, files, gitignore
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

ERRORS=0
WARNINGS=0

check_pass() { echo "  [OK]  $1"; }
check_fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }
check_warn() { echo "  [WARN] $1" >&2; WARNINGS=$((WARNINGS + 1)); }

echo "=== doctor_repo.sh ==="
echo ""

# --- Check required files ---
echo "--- Required Files ---"
for f in docs/LAST_REVIEWED_SHA.txt docs/REVIEW_WORKFLOW.md docs/REVIEW_PACKET.md docs/HANDOFF_CURRENT_STATE.md docs/CANONICAL_COMMANDS.md docs/DEPLOY_VPS.md; do
  if [ -f "$ROOT_DIR/$f" ]; then
    check_pass "$f exists"
  else
    check_fail "$f missing"
  fi
done

# --- Check ops scripts ---
echo ""
echo "--- Ops Scripts ---"
for f in ops/openai_key.py ops/ensure_openai_key.sh ops/review_bundle.sh ops/review_auto.sh ops/review_finish.sh ops/ship_auto.sh ops/autoheal_codex.sh ops/doctor_repo.sh ops/INSTALL_HOOKS.sh ops/bootstrap_branch_protection.sh ops/runner_smoke.sh ops/runner_submit_orb_review.sh ops/runner_submit_orb_doctor.sh ops/runner_submit_orb_score.sh ops/vps_bootstrap.sh ops/vps_deploy.sh ops/vps_doctor.sh ops/vps_self_update.sh; do
  if [ -f "$ROOT_DIR/$f" ]; then
    if [ -x "$ROOT_DIR/$f" ]; then
      check_pass "$f exists and executable"
    else
      check_warn "$f exists but NOT executable (run: chmod +x $f)"
    fi
  else
    check_fail "$f missing"
  fi
done

# --- Check schema ---
echo ""
echo "--- Schema ---"
if [ -f "$ROOT_DIR/ops/schemas/codex_review_verdict.schema.json" ]; then
  if python3 -c "import json; json.load(open('$ROOT_DIR/ops/schemas/codex_review_verdict.schema.json'))" 2>/dev/null; then
    check_pass "codex_review_verdict.schema.json valid JSON"
  else
    check_fail "codex_review_verdict.schema.json invalid JSON"
  fi
else
  check_fail "codex_review_verdict.schema.json missing"
fi

# --- Check git hooks ---
echo ""
echo "--- Git Hooks ---"
HOOKS_DST="$ROOT_DIR/.git/hooks"
for hook in pre-push post-commit; do
  if [ -f "$HOOKS_DST/$hook" ] || [ -L "$HOOKS_DST/$hook" ]; then
    if [ -x "$HOOKS_DST/$hook" ] || [ -L "$HOOKS_DST/$hook" ]; then
      check_pass "$hook hook installed"
    else
      check_warn "$hook hook present but not executable"
    fi
  else
    check_fail "$hook hook NOT installed (run: ./ops/INSTALL_HOOKS.sh)"
  fi
done

# --- Check merge driver for REVIEW_PACKET.md (avoids merge conflicts) ---
echo ""
echo "--- Merge driver (REVIEW_PACKET.md) ---"
if grep -q 'REVIEW_PACKET.md merge=ours' "$ROOT_DIR/.gitattributes" 2>/dev/null; then
  OUR_DRIVER="$(git config merge.ours.driver 2>/dev/null)" || true
  if [ "$OUR_DRIVER" = "true" ]; then
    check_pass "merge.ours.driver configured (REVIEW_PACKET.md merge=ours)"
  else
    check_warn "REVIEW_PACKET.md uses merge=ours but merge.ours.driver not set — run: git config merge.ours.driver true"
  fi
else
  check_pass ".gitattributes (no REVIEW_PACKET merge=ours)"
fi

# --- Check .githooks source ---
echo ""
echo "--- Hook Sources ---"
for hook in pre-push post-commit; do
  if [ -f "$ROOT_DIR/.githooks/$hook" ]; then
    check_pass ".githooks/$hook exists"
  else
    check_fail ".githooks/$hook missing"
  fi
done

# --- Check repo allowlist ---
echo ""
echo "--- Repo Allowlist ---"
if [ -f "$ROOT_DIR/configs/repo_allowlist.yaml" ]; then
  if python3 -c "import yaml; yaml.safe_load(open('$ROOT_DIR/configs/repo_allowlist.yaml'))" 2>/dev/null; then
    check_pass "configs/repo_allowlist.yaml valid YAML"
  else
    check_fail "configs/repo_allowlist.yaml invalid YAML"
  fi
else
  check_fail "configs/repo_allowlist.yaml missing"
fi

# --- Check ORB wrapper scripts ---
echo ""
echo "--- ORB Wrapper Scripts ---"
for f in services/test_runner/orb_wrappers/orb_review_bundle.sh services/test_runner/orb_wrappers/orb_doctor.sh services/test_runner/orb_wrappers/orb_score_run.sh; do
  if [ -f "$ROOT_DIR/$f" ]; then
    if [ -x "$ROOT_DIR/$f" ]; then
      check_pass "$f exists and executable"
    else
      check_warn "$f exists but NOT executable"
    fi
  else
    check_fail "$f missing"
  fi
done

# --- Check verdict gate branch protection (main) ---
# Fail if protection is missing, or required_status_checks is missing/empty, or verdict-gate is not in required checks.
# Never auto-modify branch protection; only report and print remediation.
echo ""
echo "--- Verdict gate (branch protection) ---"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
if [ -n "$ORIGIN_URL" ]; then
  if echo "$ORIGIN_URL" | grep -q '^https://github.com/'; then
    OWNER_REPO="$(echo "$ORIGIN_URL" | sed -n 's|https://github.com/\([^/]*\)/\([^.]*\)\.git|\1/\2|p')"
  elif echo "$ORIGIN_URL" | grep -q '^git@github.com:'; then
    OWNER_REPO="$(echo "$ORIGIN_URL" | sed -n 's|git@github.com:\([^/]*\)/\([^.]*\)\.git|\1/\2|p')"
  else
    OWNER_REPO=""
  fi
  if [ -n "$OWNER_REPO" ]; then
    FULL_PROT="$(gh api -H "Accept: application/vnd.github+json" "/repos/$OWNER_REPO/branches/main/protection" 2>/dev/null)" || true
    if [ -z "$FULL_PROT" ]; then
      check_fail "Branch protection missing or no API access: main must require status check 'verdict-gate'."
      echo "" >&2
      echo "  Remediation:" >&2
      echo "    1. Run ./ops/bootstrap_branch_protection.sh and follow steps (workflow must run once so 'verdict-gate' exists)." >&2
      echo "    2. GitHub UI: Settings → Branches → Add rule for main → Require status checks → add 'verdict-gate'." >&2
      echo "    3. Or add context via API (after check exists):" >&2
      echo "       gh api -X POST -H 'Accept: application/vnd.github+json' \\" >&2
      echo "         /repos/$OWNER_REPO/branches/main/protection/required_status_checks/contexts -f 'contexts[]=verdict-gate'" >&2
      echo "  Never clear required checks to bypass the gate." >&2
    else
      RSC_RC=0
      echo "$FULL_PROT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    rsc = d.get('required_status_checks')
    if rsc is None:
        sys.exit(2)  # no required status checks at all
    ctx = list(rsc.get('contexts') or [])
    for c in (rsc.get('checks') or []):
        if isinstance(c, dict) and c.get('context'):
            ctx.append(c['context'])
    if 'verdict-gate' not in ctx:
        sys.exit(1)
except Exception:
    sys.exit(3)
" 2>/dev/null || RSC_RC=$?
      if [ "$RSC_RC" -eq 2 ]; then
        check_fail "Branch protection exists but required status checks are not configured (bypass risk)."
        echo "  Remediation: Enable 'Require status checks' and add 'verdict-gate'. See docs/REVIEW_WORKFLOW.md and ./ops/bootstrap_branch_protection.sh" >&2
      elif [ "$RSC_RC" -eq 1 ] || [ "$RSC_RC" -eq 3 ]; then
        check_fail "Required status check 'verdict-gate' not set on main (or could not parse protection)."
        echo "  Remediation: Add 'verdict-gate' to required checks. UI: Settings → Branches → main → Edit → Status checks → verdict-gate." >&2
        echo "  API (add without removing others): gh api -X POST .../branches/main/protection/required_status_checks/contexts -f 'contexts[]=verdict-gate'" >&2
      else
        check_pass "main requires status check verdict-gate"
      fi
    fi
  else
    check_warn "Could not parse owner/repo from origin; skipping branch protection check"
  fi
else
  check_warn "No git origin; skipping branch protection check"
fi

# --- Check .gitignore ---
echo ""
echo "--- Gitignore ---"
if grep -q "review_packets/" "$ROOT_DIR/.gitignore" 2>/dev/null; then
  check_pass "review_packets/ is gitignored"
else
  check_fail "review_packets/ NOT in .gitignore"
fi

# --- Check review_packets not tracked ---
TRACKED="$(git ls-files "$ROOT_DIR/review_packets" 2>/dev/null || true)"
if [ -z "$TRACKED" ]; then
  check_pass "review_packets/ not tracked by git"
else
  check_fail "review_packets/ has tracked files (should be gitignored)"
fi

# --- Check baseline ---
echo ""
echo "--- Baseline ---"
if [ -f "$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt" ]; then
  BASELINE="$(tr -d '[:space:]' < "$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt")"
  if git cat-file -e "${BASELINE}^{commit}" 2>/dev/null; then
    check_pass "Baseline SHA exists in repo: ${BASELINE:0:12}..."
  else
    check_fail "Baseline SHA not found in repo: $BASELINE"
  fi
else
  check_fail "docs/LAST_REVIEWED_SHA.txt missing"
fi

# --- Summary ---
echo ""
echo "=== Summary ==="
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
  echo "  All checks passed!"
  exit 0
elif [ "$ERRORS" -eq 0 ]; then
  echo "  $WARNINGS warning(s), 0 errors"
  exit 0
else
  echo "  $ERRORS error(s), $WARNINGS warning(s)"
  exit 1
fi
