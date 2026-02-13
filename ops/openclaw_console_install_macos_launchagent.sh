#!/usr/bin/env bash
# ops/openclaw_console_install_macos_launchagent.sh — Install macOS LaunchAgent
#
# Installs a LaunchAgent that runs openclaw_console_start.sh at login.
# Idempotent: unloads existing agent before reinstalling.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_NAME="com.openclaw.console"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
START_SCRIPT="$REPO_ROOT/ops/openclaw_console_start.sh"
LOG_BASE="$REPO_ROOT/logs/openclaw_console"

# ─── Preflight ────────────────────────────────────────────────────────────────

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This script is for macOS only." >&2
  exit 1
fi

if [ ! -f "$START_SCRIPT" ]; then
  echo "ERROR: Start script not found: $START_SCRIPT" >&2
  exit 1
fi

if [ ! -x "$START_SCRIPT" ]; then
  chmod +x "$START_SCRIPT"
fi

# ─── Unload existing (idempotent) ────────────────────────────────────────────

if launchctl list "$PLIST_NAME" >/dev/null 2>&1; then
  echo "Unloading existing LaunchAgent..."
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# ─── Create log directory ────────────────────────────────────────────────────

mkdir -p "$LOG_BASE"

# ─── Write plist ──────────────────────────────────────────────────────────────

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_NAME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${START_SCRIPT}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>${LOG_BASE}/launchagent_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_BASE}/launchagent_stderr.log</string>
  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
PLIST

# ─── Load ─────────────────────────────────────────────────────────────────────

launchctl load "$PLIST_PATH"

echo "LaunchAgent installed: $PLIST_PATH"
echo "The OpenClaw Console will start automatically at login."
echo ""
echo "To uninstall: ./ops/openclaw_console_uninstall_macos_launchagent.sh"
echo "To check:     launchctl list | grep openclaw"
