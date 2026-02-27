#!/usr/bin/env bash
# resolve_deploy_target.sh — Canonical deploy target resolution (no manual exports required).
#
# Resolution order:
#   1) ops/config/deploy_targets.json (committed; preferred)
#   2) /etc/ai-ops-runner/deploy_target.env (operator machine)
#   3) Env vars OPENCLAW_AIOPS1_SSH, OPENCLAW_HQ_BASE (fallback)
#
# Usage: source ops/scripts/resolve_deploy_target.sh
#        or: eval "$(ops/scripts/resolve_deploy_target.sh)"
#
# Exports: OPENCLAW_AIOPS1_SSH, OPENCLAW_HQ_BASE
# Exits 1 with ONE human-only instruction if unresolved.
set -euo pipefail

# Use BASH_SOURCE so path is correct when script is sourced (e.g. from ship_deploy_verify.sh)
_SCRIPT="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$_SCRIPT")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"

AIOPS_SSH=""
HQ_BASE=""
RESOLVED_VIA=""

# 1) ops/config/deploy_targets.json
CONFIG_JSON="$OPS_DIR/config/deploy_targets.json"
if [ -f "$CONFIG_JSON" ]; then
  AIOPS_SSH="$(python3 -c "
import json, sys
try:
    with open('$CONFIG_JSON') as f:
        d = json.load(f)
    a = d.get('aiops1', {})
    print(a.get('ssh', '') or '')
except Exception:
    print('')
" 2>/dev/null)" || AIOPS_SSH=""
  HQ_BASE="$(python3 -c "
import json, sys
try:
    with open('$CONFIG_JSON') as f:
        d = json.load(f)
    a = d.get('aiops1', {})
    print(a.get('hq_base', '') or '')
except Exception:
    print('')
" 2>/dev/null)" || HQ_BASE=""
  if [ -n "$AIOPS_SSH" ]; then
    RESOLVED_VIA="ops/config/deploy_targets.json"
  fi
fi

# 2) /etc/ai-ops-runner/deploy_target.env
if [ -z "$AIOPS_SSH" ] && [ -f /etc/ai-ops-runner/deploy_target.env ]; then
  set -a
  # shellcheck source=/dev/null
  . /etc/ai-ops-runner/deploy_target.env 2>/dev/null || true
  set +a
  AIOPS_SSH="${OPENCLAW_AIOPS1_SSH:-}"
  HQ_BASE="${OPENCLAW_HQ_BASE:-}"
  if [ -n "$AIOPS_SSH" ]; then
    RESOLVED_VIA="/etc/ai-ops-runner/deploy_target.env"
  fi
fi

# 3) Env vars fallback
if [ -z "$AIOPS_SSH" ]; then
  AIOPS_SSH="${OPENCLAW_AIOPS1_SSH:-}"
  HQ_BASE="${OPENCLAW_HQ_BASE:-https://aiops-1.tailc75c62.ts.net}"
  if [ -n "$AIOPS_SSH" ]; then
    RESOLVED_VIA="env"
  fi
fi

# Default HQ_BASE if SSH resolved but HQ_BASE empty
if [ -n "$AIOPS_SSH" ] && [ -z "$HQ_BASE" ]; then
  HQ_BASE="https://aiops-1.tailc75c62.ts.net"
fi

if [ -z "$AIOPS_SSH" ]; then
  echo "ERROR: Deploy target aiops-1 unresolved. No manual OPENCLAW_AIOPS1_SSH export required." >&2
  echo "" >&2
  echo "ONE-TIME SETUP: Create ops/config/deploy_targets.json with:" >&2
  echo '  {"aiops1": {"ssh": "root@aiops-1.tailc75c62.ts.net", "hq_base": "https://aiops-1.tailc75c62.ts.net"}}' >&2
  echo "" >&2
  echo "Or create /etc/ai-ops-runner/deploy_target.env with:" >&2
  echo "  OPENCLAW_AIOPS1_SSH=root@aiops-1.tailc75c62.ts.net" >&2
  echo "  OPENCLAW_HQ_BASE=https://aiops-1.tailc75c62.ts.net" >&2
  echo "" >&2
  echo "Then rerun." >&2
  exit 1
fi

export OPENCLAW_AIOPS1_SSH="$AIOPS_SSH"
export OPENCLAW_HQ_BASE="$HQ_BASE"
