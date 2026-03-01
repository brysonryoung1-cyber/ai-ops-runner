#!/usr/bin/env bash
# playbook.recover_infra_verify — Idempotent infra remediation for verify failures.
# Runs SSH tailscale fix, guard install, guard, then verify_production.
# Writes proof artifact when invoked with OPENCLAW_RUN_ID or generates one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
OPS_DIR="$ROOT_DIR/ops"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="${OPENCLAW_RUN_ID:-recover_infra_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$ARTIFACTS/playbooks/recover_infra_verify/$RUN_ID"
mkdir -p "$OUT_DIR"

echo "=== playbook.recover_infra_verify ($RUN_ID) ==="
echo ""

FAILURES=0

# 1. SSH Tailscale-only
echo "--- Step 1: SSH Tailscale-only ---"
if sudo "$OPS_DIR/openclaw_fix_ssh_tailscale_only.sh" 2>&1 | tee "$OUT_DIR/ssh_fix.log"; then
  echo "  SSH fix: PASS"
else
  echo "  SSH fix: FAIL" >&2
  FAILURES=$((FAILURES + 1))
fi
echo ""

# 2. Install guard timer
echo "--- Step 2: Install guard timer ---"
if sudo "$OPS_DIR/openclaw_install_guard.sh" 2>&1 | tee "$OUT_DIR/guard_install.log"; then
  echo "  Guard install: PASS"
else
  echo "  Guard install: FAIL" >&2
  FAILURES=$((FAILURES + 1))
fi
echo ""

# 3. Run guard
echo "--- Step 3: Run guard ---"
if sudo "$OPS_DIR/openclaw_guard.sh" 2>&1 | tee "$OUT_DIR/guard_run.log"; then
  echo "  Guard: PASS"
else
  echo "  Guard: FAIL" >&2
  FAILURES=$((FAILURES + 1))
fi
echo ""

# 4. Verify production
echo "--- Step 4: Verify production ---"
export SHIP_ARTIFACT_DIR="$OUT_DIR"
if sudo "$OPS_DIR/verify_production.sh" 2>&1 | tee "$OUT_DIR/verify.log"; then
  echo "  Verify: PASS"
else
  echo "  Verify: FAIL" >&2
  FAILURES=$((FAILURES + 1))
fi
echo ""

# 5. Write proof / summary
SS_SNIPPET="$(ss -tlnp 2>/dev/null | grep ':22 ' | head -5 || echo 'ss unavailable')"
GUARD_TIMER="$(systemctl is-active openclaw-guard.timer 2>/dev/null || echo 'unknown')"

if [ "$FAILURES" -eq 0 ]; then
  RESULT="PASS"
else
  RESULT="FAIL ($FAILURES step(s) failed)"
fi

cat > "$OUT_DIR/SUMMARY.md" << EOF
# recover_infra_verify

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Result:** $RESULT

## Steps

| Step | Result |
|------|--------|
| SSH Tailscale-only | $([ -f "$OUT_DIR/ssh_fix.log" ] && tail -1 "$OUT_DIR/ssh_fix.log" || echo "N/A") |
| Guard install | $([ -f "$OUT_DIR/guard_install.log" ] && tail -1 "$OUT_DIR/guard_install.log" || echo "N/A") |
| Guard run | $([ -f "$OUT_DIR/guard_run.log" ] && tail -1 "$OUT_DIR/guard_run.log" || echo "N/A") |
| Verify production | $([ -f "$OUT_DIR/verify.log" ] && tail -1 "$OUT_DIR/verify.log" || echo "N/A") |

## sshd :22 listeners (redacted)

\`\`\`
$SS_SNIPPET
\`\`\`

## Guard timer status

$GUARD_TIMER
EOF

PROOF_DIR="$ARTIFACTS/hq_proofs/recover_infra_verify/$RUN_ID"
mkdir -p "$PROOF_DIR"
cp "$OUT_DIR/SUMMARY.md" "$PROOF_DIR/PROOF.md"

echo "=== recover_infra_verify: $RESULT ==="
echo "  Artifacts: $OUT_DIR"
echo "  Proof: $PROOF_DIR/PROOF.md"
echo '{"ok":'"$([ "$FAILURES" -eq 0 ] && echo true || echo false)"',"artifact_dir":"'"$OUT_DIR"'","proof":"'"$PROOF_DIR/PROOF.md"'","run_id":"'"$RUN_ID"'"}'

[ "$FAILURES" -eq 0 ] || exit 1
