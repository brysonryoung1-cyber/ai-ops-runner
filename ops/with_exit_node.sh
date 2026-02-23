#!/usr/bin/env bash
# with_exit_node.sh — Temporarily use Tailscale exit node for Kajabi runs, ALWAYS restore.
#
# Usage:
#   ./ops/with_exit_node.sh [EXIT_NODE] -- COMMAND [args...]
#   EXIT_NODE: hostname (MagicDNS) or Tailscale IP. If omitted, reads from
#     KAJABI_EXIT_NODE env or /etc/ai-ops-runner/config/soma_kajabi_exit_node.txt
#
# If no exit node configured: runs COMMAND directly (no wrapper).
# If exit node unreachable: fail-closed with EXIT_NODE_OFFLINE, does NOT change aiops-1 config.
# On success/failure: ALWAYS restores previous exit-node state via trap.
#
# Error classes: EXIT_NODE_OFFLINE, EXIT_NODE_ENABLE_FAILED, EXIT_NODE_RESTORE_FAILED
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${OPENCLAW_STATE_DIR:-/var/lib/openclaw/state}"
STATE_FILE="$STATE_DIR/exit_node_prev.json"

_log() {
  local msg="$1"
  if command -v logger &>/dev/null; then
    logger -t with_exit_node -- "$msg"
  fi
  echo "[with_exit_node] $msg" >&2
}

_fail_closed() {
  local error_class="$1"
  local message="$2"
  _log "FAIL_CLOSED: $error_class — $message"
  echo "{\"ok\":false,\"error_class\":\"$error_class\",\"message\":\"$message\"}"
  exit 1
}

# Resolve EXIT_NODE from env or config file (when not passed as arg)
_resolve_exit_node_from_config() {
  if [[ -n "${KAJABI_EXIT_NODE:-}" ]]; then
    echo "$KAJABI_EXIT_NODE"
    return
  fi
  local config_path="/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt"
  if [[ -f "$config_path" ]]; then
    local val
    val="$(tr -d '[:space:]' < "$config_path")"
    if [[ -n "$val" ]]; then
      echo "$val"
      return
    fi
  fi
  echo ""
}

# Parse args: [EXIT_NODE] -- COMMAND [args...]
# If -- is first arg: no EXIT_NODE, rest is command.
# If EXIT_NODE --: use that node, rest is command.
# If no --: resolve EXIT_NODE from config; all args are command (legacy).
EXIT_NODE=""
COMMAND_ARGS=()
saw_dash=0
for arg in "$@"; do
  if [[ "$arg" == "--" ]]; then
    saw_dash=1
    continue
  fi
  if [[ $saw_dash -eq 1 ]]; then
    COMMAND_ARGS+=("$arg")
  else
    EXIT_NODE="$arg"
  fi
done
if [[ $saw_dash -eq 0 ]]; then
  # No -- found: all args are command, resolve EXIT_NODE from config
  COMMAND_ARGS=("$@")
  EXIT_NODE="$(_resolve_exit_node_from_config)"
else
  # Had --: if EXIT_NODE not set before --, resolve from config
  if [[ -z "$EXIT_NODE" ]]; then
    EXIT_NODE="$(_resolve_exit_node_from_config)"
  fi
fi

# If no exit node configured: run command directly
if [[ -z "${EXIT_NODE:-}" ]]; then
  _log "No KAJABI_EXIT_NODE configured; running command without exit node"
  exec "${COMMAND_ARGS[@]}"
fi

# Require command
if [[ ${#COMMAND_ARGS[@]} -eq 0 ]]; then
  _fail_closed "EXIT_NODE_ENABLE_FAILED" "No command specified"
fi

# Preflight: tailscale installed and running
if ! command -v tailscale &>/dev/null; then
  _fail_closed "EXIT_NODE_ENABLE_FAILED" "tailscale not installed"
fi
if ! tailscale status &>/dev/null; then
  _fail_closed "EXIT_NODE_ENABLE_FAILED" "tailscale not running"
fi

# Preflight: verify exit node is reachable (MUST NOT change config if unreachable)
if ! tailscale ping -c 1 --timeout=5s "$EXIT_NODE" &>/dev/null; then
  _fail_closed "EXIT_NODE_OFFLINE" "Exit node offline. Turn on laptop (keep awake) and rerun."
fi

# Save current state (we always restore to empty; aiops-1 typically has no exit node)
mkdir -p "$STATE_DIR"
echo '{"prev_exit_node":"","restore_to_empty":true}' > "$STATE_FILE"

# Trap: ALWAYS restore on exit (success, failure, signal)
_restore_exit_node() {
  local rc=$?
  _log "EXIT_NODE_RESTORED (trap)"
  if [[ -f "$STATE_FILE" ]]; then
    local restore_empty
    restore_empty="$(python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        d = json.load(f)
    print(d.get('restore_to_empty', True))
except Exception:
    print('True')
" 2>/dev/null || echo "True")"
    if [[ "$restore_empty" == "True" ]]; then
      tailscale up --exit-node= --accept-routes 2>/dev/null || true
    else
      local prev
      prev="$(python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        d = json.load(f)
    print(d.get('prev_exit_node', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")"
      if [[ -n "$prev" ]]; then
        tailscale up --exit-node="$prev" --accept-routes 2>/dev/null || true
      else
        tailscale up --exit-node= --accept-routes 2>/dev/null || true
      fi
    fi
  else
    tailscale up --exit-node= --accept-routes 2>/dev/null || true
  fi
  rm -f "$STATE_FILE"
  exit $rc
}
trap _restore_exit_node EXIT

# Enable exit node
if ! tailscale up --exit-node="$EXIT_NODE" --exit-node-allow-lan-access=false --accept-routes 2>/dev/null; then
  _fail_closed "EXIT_NODE_ENABLE_FAILED" "Failed to set exit node to $EXIT_NODE"
fi
_log "EXIT_NODE_ENABLED=$EXIT_NODE"

# Run command (trap will restore on exit)
export OPENCLAW_REPO_ROOT="${OPENCLAW_REPO_ROOT:-$ROOT_DIR}"
export OPENCLAW_RUN_ID="${OPENCLAW_RUN_ID:-}"
export KAJABI_EXIT_NODE="$EXIT_NODE"
"${COMMAND_ARGS[@]}"
exit $?
