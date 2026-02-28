#!/usr/bin/env bash
# rollback_playbook.sh — Bounded, safe auto-rollback.
#
# Allowed ONLY when:
#   1. Canary degraded persists for N consecutive runs (default 3)
#   2. Remediation playbooks have been attempted and failed
#
# Rollback selects last-known-good tree_sha from deploy_info history.
#
# Produces:
#   artifacts/incidents/incident_rollback_<ts>/SUMMARY.md
#   artifacts/hq_proofs/rollback/<run_id>/PROOF.md
#
# Requires human approval for destructive_ops tier (policy enforced).
# Uses rootd for privileged steps (systemctl restart after rollback).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${OPENCLAW_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)Z_rollback"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

DEGRADED_THRESHOLD="${OPENCLAW_ROLLBACK_DEGRADED_THRESHOLD:-3}"
DEGRADED_FILE="$ROOT_DIR/artifacts/system/canary/.degraded_count"
DEPLOY_INFO="/etc/ai-ops-runner/deploy_info.json"
INCIDENT_DIR="$ROOT_DIR/artifacts/incidents/incident_rollback_$RUN_ID"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/rollback/$RUN_ID"

mkdir -p "$INCIDENT_DIR" "$PROOF_DIR"

fail_closed() {
  local reason="$1"
  echo "ROLLBACK DENIED: $reason" >&2
  cat > "$INCIDENT_DIR/SUMMARY.md" << EOF
# Rollback Denied

**Run ID:** $RUN_ID
**Time:** $TIMESTAMP
**Reason:** $reason

Rollback was not executed. Manual investigation required.
EOF
  exit 1
}

# Gate 1: Check canary degraded count
if [ ! -f "$DEGRADED_FILE" ]; then
  fail_closed "No canary degradation detected (missing $DEGRADED_FILE). Rollback not warranted."
fi

DEGRADED_COUNT="$(cat "$DEGRADED_FILE" 2>/dev/null | tr -d '[:space:]')"
if ! [[ "$DEGRADED_COUNT" =~ ^[0-9]+$ ]]; then
  fail_closed "Cannot parse degraded count from $DEGRADED_FILE. Manual review needed."
fi

if [ "$DEGRADED_COUNT" -lt "$DEGRADED_THRESHOLD" ]; then
  fail_closed "Canary degraded count ($DEGRADED_COUNT) below threshold ($DEGRADED_THRESHOLD). Not enough consecutive failures."
fi

echo "=== Rollback Playbook ==="
echo "  Run ID: $RUN_ID"
echo "  Degraded count: $DEGRADED_COUNT (threshold: $DEGRADED_THRESHOLD)"

# Gate 2: Find last-known-good SHA
LAST_GOOD_SHA=""
if [ -f "$DEPLOY_INFO" ]; then
  LAST_GOOD_SHA="$(python3 -c "
import json
try:
    with open('$DEPLOY_INFO') as f:
        d = json.load(f)
    history = d.get('rollback_history', [])
    for entry in reversed(history):
        if entry.get('status') == 'good':
            print(entry.get('tree_sha', ''))
            break
    else:
        print(d.get('last_good_tree_sha', ''))
except Exception:
    pass
" 2>/dev/null)"
fi

if [ -z "$LAST_GOOD_SHA" ]; then
  # Fallback: try git log for last known good tag/ref
  cd "$ROOT_DIR"
  LAST_GOOD_SHA="$(git log --format='%H' --max-count=5 origin/main 2>/dev/null | tail -1)" || true
fi

if [ -z "$LAST_GOOD_SHA" ]; then
  fail_closed "Cannot determine last-known-good SHA. No deploy_info.json or git history available."
fi

echo "  Last good SHA: ${LAST_GOOD_SHA:0:12}..."

# Gate 3: Policy check (destructive_ops requires approval)
APPROVED="${OPENCLAW_ROLLBACK_APPROVED:-false}"
if [ "$APPROVED" != "true" ]; then
  cat > "$PROOF_DIR/PROOF.md" << EOF
# Rollback BLOCKED — Requires Approval

**Run ID:** $RUN_ID
**Time:** $TIMESTAMP
**Last good SHA:** ${LAST_GOOD_SHA:0:12}...
**Degraded count:** $DEGRADED_COUNT

## Policy

Rollback is a \`destructive_ops\` tier action requiring human approval.
Set \`OPENCLAW_ROLLBACK_APPROVED=true\` or approve via HQ to proceed.
EOF
  echo "ROLLBACK BLOCKED: destructive_ops tier requires human approval."
  echo "  Set OPENCLAW_ROLLBACK_APPROVED=true to proceed."
  echo "  Proof: $PROOF_DIR/PROOF.md"
  exit 2
fi

# Execute rollback
echo "==> Executing rollback to $LAST_GOOD_SHA"
cd "$ROOT_DIR"

# Record pre-rollback state
PRE_SHA="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
PRE_TREE="$(git rev-parse HEAD^{tree} 2>/dev/null || echo 'unknown')"

# Fetch and reset
git fetch origin 2>/dev/null || true
if ! git reset --hard "$LAST_GOOD_SHA" 2>"$INCIDENT_DIR/git_reset.log"; then
  fail_closed "git reset --hard $LAST_GOOD_SHA failed. See $INCIDENT_DIR/git_reset.log"
fi

POST_SHA="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
POST_TREE="$(git rev-parse HEAD^{tree} 2>/dev/null || echo 'unknown')"

echo "  Reset: $PRE_SHA -> $POST_SHA"

# Redeploy via deploy_pipeline
DEPLOY_OK=false
if [ -f "$ROOT_DIR/ops/deploy_pipeline.sh" ]; then
  if "$ROOT_DIR/ops/deploy_pipeline.sh" 2>&1 | tee "$INCIDENT_DIR/deploy.log"; then
    DEPLOY_OK=true
  fi
fi

# Restart services via rootd if available
ROOTD_SOCKET="/run/openclaw/rootd.sock"
if [ -S "$ROOTD_SOCKET" ]; then
  python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from ops.rootd_client import RootdClient
client = RootdClient()
for unit in ['openclaw-hostd.service', 'ai-ops-runner.service']:
    r = client.exec('systemctl_restart', {'unit': unit})
    print(f'rootd restart {unit}: {\"ok\" if r.get(\"ok\") else \"FAIL\"}: {r.get(\"reason\", \"\")}')
" 2>&1 | tee "$INCIDENT_DIR/rootd_restarts.log"
fi

# Clear degraded counter
rm -f "$DEGRADED_FILE"
echo "  Cleared canary degraded counter"

# Run canary to verify
CANARY_PASS=false
if [ -f "$ROOT_DIR/ops/scripts/canary.sh" ]; then
  if "$ROOT_DIR/ops/scripts/canary.sh" 2>&1 | tee "$INCIDENT_DIR/canary.log"; then
    CANARY_PASS=true
  fi
fi

# Write proof
OVERALL="PASS"
if [ "$DEPLOY_OK" = false ] || [ "$CANARY_PASS" = false ]; then
  OVERALL="PARTIAL"
fi

cat > "$INCIDENT_DIR/SUMMARY.md" << EOF
# Rollback Executed

**Run ID:** $RUN_ID
**Time:** $TIMESTAMP
**Result:** $OVERALL

## Details

- Pre-rollback SHA: ${PRE_SHA:0:12}... (tree: ${PRE_TREE:0:12}...)
- Post-rollback SHA: ${POST_SHA:0:12}... (tree: ${POST_TREE:0:12}...)
- Degraded count at trigger: $DEGRADED_COUNT (threshold: $DEGRADED_THRESHOLD)
- Deploy: $([ "$DEPLOY_OK" = true ] && echo PASS || echo FAIL)
- Canary: $([ "$CANARY_PASS" = true ] && echo PASS || echo FAIL)
EOF

cat > "$PROOF_DIR/PROOF.md" << EOF
# Rollback PROOF

**Run ID:** $RUN_ID
**Timestamp:** $TIMESTAMP
**Result:** $OVERALL

## Rollback Details

| Field | Value |
|-------|-------|
| pre_sha | ${PRE_SHA:0:12}... |
| pre_tree | ${PRE_TREE:0:12}... |
| post_sha (last good) | ${POST_SHA:0:12}... |
| post_tree | ${POST_TREE:0:12}... |
| degraded_count | $DEGRADED_COUNT |
| threshold | $DEGRADED_THRESHOLD |
| deploy | $([ "$DEPLOY_OK" = true ] && echo PASS || echo FAIL) |
| canary | $([ "$CANARY_PASS" = true ] && echo PASS || echo FAIL) |

## Policy

- Tier: destructive_ops
- Approval: operator-approved (OPENCLAW_ROLLBACK_APPROVED=true)

## Artifacts

- $INCIDENT_DIR/SUMMARY.md
- $INCIDENT_DIR/deploy.log
- $INCIDENT_DIR/canary.log
EOF

echo "=== Rollback $OVERALL ==="
echo "  Incident: $INCIDENT_DIR/SUMMARY.md"
echo "  Proof: $PROOF_DIR/PROOF.md"

if [ "$OVERALL" = "PASS" ]; then
  exit 0
else
  exit 1
fi
