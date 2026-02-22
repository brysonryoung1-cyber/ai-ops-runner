#!/usr/bin/env bash
# stability_verify_aiops1.sh — CSR/Opus stability verification (no code changes).
# Run on aiops-1: /opt/ai-ops-runner/ops/stability_verify_aiops1.sh
#
# Goals:
#   1) Confirm guard timer produces PASS (not stale FAIL).
#   2) Confirm litellm-proxy stays HEALTHY for ~3 minutes.
#   3) If it flaps, capture diagnostics into a single artifact folder.
#
# Exit: 0 = STABLE, 1 = UNSTABLE or guard FAIL
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/ai-ops-runner}"
cd "$REPO_DIR"

echo "=== A+B: Repo and timer/service names ==="
sudo systemctl list-timers --all | grep -Ei 'openclaw|guard|doctor' || true
echo "---"
sudo systemctl list-units --type=service | grep -Ei 'openclaw|guard|doctor' || true
echo ""

echo "=== C: Force-run guard now ==="
sudo ./ops/openclaw_guard.sh | tee "/tmp/openclaw_guard_manual_$(date +%Y%m%d_%H%M%S).log" || GUARD_RC=$?
# If guard failed we still want to record and continue to collect diagnostics
if [ "${GUARD_RC:-0}" -ne 0 ]; then
  echo "WARNING: guard exited ${GUARD_RC}; continuing to collect logs and health samples."
fi
echo ""

echo "=== D: Newest guard log entries ==="
if [ -f /var/log/openclaw_guard.log ]; then
  sudo tail -n 120 /var/log/openclaw_guard.log
else
  echo "No /var/log/openclaw_guard.log present"
fi
echo ""

# Fail early if guard did not PASS (we require PASS for stability)
if ! sudo grep -q 'RESULT: PASS' /var/log/openclaw_guard.log 2>/dev/null; then
  echo "VERDICT: Guard has no recent PASS in /var/log/openclaw_guard.log (stale FAIL or missing)."
  exit 1
fi
if ! [ -f /var/log/openclaw_guard.log ] || ! sudo tail -n 50 /var/log/openclaw_guard.log | grep -q 'RESULT: PASS'; then
  echo "VERDICT: Newest guard log entries do not show PASS."
  exit 1
fi

echo "=== E: Short stability window (~3 min) ==="
ART="/tmp/litellm_healthcheck_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ART"
CONTAINER="ai-ops-runner-litellm-proxy-1"

for i in $(seq 1 18); do
  date -u +"%Y-%m-%dT%H:%M:%SZ" | tee -a "$ART/timestamps.log"
  docker compose ps | tee -a "$ART/compose_ps.log"
  docker inspect "$CONTAINER" --format '{{json .State.Health}}' >> "$ART/health_state.jsonl" 2>/dev/null || echo "{}" >> "$ART/health_state.jsonl"
  sleep 10
done

docker inspect "$CONTAINER" --format '{{json .State.Health.Log}}' > "$ART/health_log.json" 2>/dev/null || true
docker logs --tail 250 "$CONTAINER" > "$ART/litellm_tail.log" 2>/dev/null || true

echo "=== F: Verdict ==="
HEALTHY_COUNT=$(grep -c '"Status":"healthy"' "$ART/health_state.jsonl" 2>/dev/null || echo 0)
TOTAL=$(wc -l < "$ART/health_state.jsonl" 2>/dev/null || echo 0)

if [ "$TOTAL" -eq 18 ] && [ "$HEALTHY_COUNT" -eq 18 ]; then
  echo "STABLE: litellm remained healthy ($HEALTHY_COUNT/18 samples)"
  echo "ART path: $ART"
  exit 0
fi

echo "UNSTABLE: litellm flapped (healthy=$HEALTHY_COUNT, total=$TOTAL)"
echo "ART path: $ART"
exit 1
