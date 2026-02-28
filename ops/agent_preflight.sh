#!/usr/bin/env bash
# agent_preflight.sh — Capability Gate: provable full access check.
#
# Outputs:
#   artifacts/system/preflight/<run_id>/preflight.json
#   artifacts/system/preflight/<run_id>/PROOF.md
#
# Checks: repo control, git fetch, HQ reachable, hostd reachable,
#          rootd reachable, tailscale, deploy target, drift status.
#
# Exit 0 = all checks PASS (ok).  Exit 1 = at least one BLOCKED.
# No secrets printed. Deterministic. No LLM.

set -euo pipefail

REPO_ROOT="${OPENCLAW_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_ID="preflight_$(date -u +%Y%m%dT%H%M%SZ)_$(head -c4 /dev/urandom | xxd -p)"
ARTIFACT_DIR="${REPO_ROOT}/artifacts/system/preflight/${RUN_ID}"
mkdir -p "$ARTIFACT_DIR"

TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
HQ_BASE="https://${TS_HOSTNAME}"
HOSTD_URL="${OPENCLAW_HOSTD_URL:-http://127.0.0.1:8877}"
ROOTD_SOCKET="${OPENCLAW_ROOTD_SOCKET:-/run/openclaw-rootd.sock}"

declare -A results
overall="ok"

check() {
    local name="$1"
    local status="$2"
    local detail="$3"
    results["$name"]="$status"
    if [ "$status" != "ok" ]; then
        overall="blocked"
    fi
    echo "  ${name}: ${status} — ${detail}"
}

echo "=== Agent Preflight ${RUN_ID} ==="
echo ""

# 1. Repo control
cd "$REPO_ROOT"
if git diff --quiet HEAD 2>/dev/null; then
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    check "repo_clean" "ok" "Clean working tree on branch ${branch}"
else
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    check "repo_clean" "warn" "Dirty working tree on branch ${branch}"
fi

# 2. Git fetch
if git fetch origin --dry-run 2>/dev/null; then
    check "git_fetch" "ok" "git fetch origin reachable"
else
    check "git_fetch" "blocked" "git fetch origin failed"
fi

# 3. HQ reachable
hq_health=""
if command -v curl &>/dev/null; then
    hq_health=$(curl -sf --max-time 5 "${HQ_BASE}/api/ui/health_public" 2>/dev/null || echo "")
fi
if [ -n "$hq_health" ]; then
    build_sha=$(echo "$hq_health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_sha','unknown'))" 2>/dev/null || echo "unknown")
    check "hq_health_public" "ok" "build_sha=${build_sha}"
else
    # Try localhost fallback
    hq_health=$(curl -sf --max-time 5 "http://127.0.0.1:8788/api/ui/health_public" 2>/dev/null || echo "")
    if [ -n "$hq_health" ]; then
        build_sha=$(echo "$hq_health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_sha','unknown'))" 2>/dev/null || echo "unknown")
        check "hq_health_public" "ok" "build_sha=${build_sha} (localhost)"
    else
        check "hq_health_public" "blocked" "HQ health_public unreachable"
    fi
fi

hq_version=""
if command -v curl &>/dev/null; then
    hq_version=$(curl -sf --max-time 5 "http://127.0.0.1:8788/api/ui/version" 2>/dev/null || echo "")
fi
if [ -n "$hq_version" ]; then
    check "hq_version" "ok" "version endpoint reachable"
else
    check "hq_version" "warn" "version endpoint unreachable"
fi

autopilot_status=""
if command -v curl &>/dev/null; then
    autopilot_status=$(curl -sf --max-time 5 "http://127.0.0.1:8788/api/autopilot/status" 2>/dev/null || echo "")
fi
if [ -n "$autopilot_status" ]; then
    check "hq_autopilot_status" "ok" "autopilot/status reachable"
else
    check "hq_autopilot_status" "warn" "autopilot/status unreachable"
fi

# 4. Hostd reachable
hostd_health=""
if command -v curl &>/dev/null; then
    hostd_health=$(curl -sf --max-time 3 "${HOSTD_URL}/healthz" 2>/dev/null || echo "")
fi
if [ -n "$hostd_health" ]; then
    check "hostd_reachable" "ok" "hostd healthz OK"
else
    check "hostd_reachable" "blocked" "hostd unreachable at ${HOSTD_URL}"
fi

# 5. Rootd reachable
if [ -S "$ROOTD_SOCKET" ]; then
    rootd_resp=""
    if command -v curl &>/dev/null; then
        rootd_resp=$(curl -sf --max-time 3 --unix-socket "$ROOTD_SOCKET" "http://localhost/health" 2>/dev/null || echo "")
    fi
    if [ -n "$rootd_resp" ]; then
        check "rootd_reachable" "ok" "rootd socket responsive"
    else
        check "rootd_reachable" "warn" "rootd socket exists but unresponsive"
    fi
else
    check "rootd_reachable" "warn" "rootd socket not found (${ROOTD_SOCKET})"
fi

# 6. Tailscale
if command -v tailscale &>/dev/null; then
    ts_status=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('BackendState','unknown'))" 2>/dev/null || echo "unknown")
    if [ "$ts_status" = "Running" ]; then
        check "tailscale" "ok" "Tailscale ${ts_status}"
    else
        check "tailscale" "blocked" "Tailscale state: ${ts_status}"
    fi
else
    check "tailscale" "warn" "tailscale CLI not found"
fi

# 7. Deploy target resolvable
deploy_targets_file="${REPO_ROOT}/ops/config/deploy_targets.json"
if [ -f "$deploy_targets_file" ]; then
    check "deploy_target" "ok" "deploy_targets.json exists"
else
    if [ -n "${DEPLOY_HOST:-}" ]; then
        check "deploy_target" "ok" "DEPLOY_HOST=${DEPLOY_HOST}"
    else
        check "deploy_target" "warn" "No deploy_targets.json or DEPLOY_HOST"
    fi
fi

# 8. Drift status
drift_status="unknown"
drift_value="unknown"
if [ -n "$hq_version" ]; then
    drift_status=$(echo "$hq_version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('drift_status','unknown'))" 2>/dev/null || echo "unknown")
    drift_value=$(echo "$hq_version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('drift',False))" 2>/dev/null || echo "unknown")
fi
if [ "$drift_status" = "ok" ] && [ "$drift_value" = "False" ]; then
    check "drift" "ok" "drift_status=ok drift=false"
elif [ "$drift_status" = "unknown" ]; then
    check "drift" "warn" "drift status unknown (version endpoint unreachable)"
else
    check "drift" "blocked" "drift_status=${drift_status} drift=${drift_value}"
fi

# 9. Browser Gateway
bg_health=""
if command -v curl &>/dev/null; then
    bg_health=$(curl -sf --max-time 3 "http://127.0.0.1:8890/health" 2>/dev/null || echo "")
fi
if [ -n "$bg_health" ]; then
    check "browser_gateway" "ok" "Browser Gateway responsive"
else
    check "browser_gateway" "warn" "Browser Gateway not running (non-critical)"
fi

# Write preflight.json
{
    echo "{"
    echo "  \"run_id\": \"${RUN_ID}\","
    echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"overall\": \"${overall}\","
    echo "  \"checks\": {"
    first=true
    for key in "${!results[@]}"; do
        if [ "$first" = true ]; then first=false; else echo ","; fi
        printf "    \"%s\": \"%s\"" "$key" "${results[$key]}"
    done
    echo ""
    echo "  }"
    echo "}"
} > "${ARTIFACT_DIR}/preflight.json"

# Write PROOF.md
{
    echo "# Agent Preflight Proof"
    echo ""
    echo "**Run ID:** ${RUN_ID}"
    echo "**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "**Overall:** ${overall}"
    echo ""
    echo "## Checks"
    echo ""
    for key in "${!results[@]}"; do
        status="${results[$key]}"
        icon="?"
        [ "$status" = "ok" ] && icon="PASS"
        [ "$status" = "blocked" ] && icon="FAIL"
        [ "$status" = "warn" ] && icon="WARN"
        echo "- **${key}**: ${icon}"
    done
    echo ""
    echo "## Artifact"
    echo ""
    echo "- \`${ARTIFACT_DIR}/preflight.json\`"
} > "${ARTIFACT_DIR}/PROOF.md"

echo ""
echo "=== Preflight: ${overall} ==="
echo "Artifacts: ${ARTIFACT_DIR}"

# Output JSON for downstream consumption
cat "${ARTIFACT_DIR}/preflight.json"

if [ "$overall" = "blocked" ]; then
    exit 1
fi
exit 0
