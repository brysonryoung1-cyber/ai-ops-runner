#!/usr/bin/env bash
# vps_self_update.sh — Review-gated self-update for ai-ops-runner
# Runs ON the VPS via systemd timer. Fails closed if review gate not passed.
#
# Review gate logic:
#   origin/main's docs/LAST_REVIEWED_SHA.txt must match origin/main HEAD.
#   This means ship_auto.sh APPROVED the code before it was pushed.
#   If the gate doesn't match, the update is SKIPPED (fail-closed).
#
# Rollback:
#   If docker compose fails after merge, rolls back to the previous HEAD.
set -euo pipefail

REPO_DIR="/opt/ai-ops-runner"
LOG_TAG="ai-ops-runner-update"

cd "$REPO_DIR"

log() {
  local msg="[$(date -Iseconds)] $*"
  echo "$msg"
  logger -t "$LOG_TAG" "$*" 2>/dev/null || true
}

# --- Fetch latest ---
log "Fetching origin/main..."
if ! git fetch origin main 2>&1; then
  log "FAIL: git fetch failed (network issue?)"
  exit 1
fi

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/main)"

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
  log "Already up to date at ${LOCAL_SHA:0:12}"
  exit 0
fi

log "Update available: ${LOCAL_SHA:0:12} -> ${REMOTE_SHA:0:12}"

# --- Review gate: LAST_REVIEWED_SHA must match origin/main HEAD ---
REVIEWED_SHA="$(git show origin/main:docs/LAST_REVIEWED_SHA.txt 2>/dev/null | tr -d '[:space:]' || echo '')"

if [ -z "$REVIEWED_SHA" ]; then
  log "FAIL CLOSED: Cannot read LAST_REVIEWED_SHA.txt from origin/main"
  log "Update skipped — cannot verify review gate."
  exit 1
fi

if [ "$REVIEWED_SHA" != "$REMOTE_SHA" ]; then
  log "FAIL CLOSED: LAST_REVIEWED_SHA (${REVIEWED_SHA:0:12}) != origin/main HEAD (${REMOTE_SHA:0:12})"
  log "Update skipped — code has not passed review gate."
  exit 1
fi

log "Review gate PASSED: ${REVIEWED_SHA:0:12} == origin/main HEAD"

# --- Fast-forward merge ---
log "Merging origin/main (fast-forward only)..."
if ! git merge --ff-only origin/main 2>&1; then
  log "FAIL: fast-forward merge failed — manual intervention needed"
  exit 1
fi

# --- Rebuild and restart ---
log "Rebuilding Docker Compose stack..."
if ! docker compose up -d --build 2>&1; then
  log "FAIL: docker compose failed — rolling back to ${LOCAL_SHA:0:12}"
  git reset --hard "$LOCAL_SHA"
  docker compose up -d --build 2>&1 || log "CRITICAL: rollback docker compose also failed"
  exit 1
fi

# --- Quick health check ---
log "Waiting for API health..."
for i in $(seq 1 15); do
  if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    log "API healthy after update."
    break
  fi
  if [ "$i" -eq 15 ]; then
    log "WARNING: API not healthy after 15s — may still be starting"
  fi
  sleep 1
done

# --- Clean up old smoke logs (>30 days) ---
find /var/log/ai-ops-runner -name 'smoke-*.log' -mtime +30 -delete 2>/dev/null || true

log "Update successful: ${LOCAL_SHA:0:12} -> ${REMOTE_SHA:0:12}"
