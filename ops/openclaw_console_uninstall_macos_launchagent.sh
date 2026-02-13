#!/usr/bin/env bash
# ops/openclaw_console_uninstall_macos_launchagent.sh — Remove macOS LaunchAgent
#
# Idempotent: exits 0 even if not installed.

set -euo pipefail

PLIST_NAME="com.openclaw.console"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [ ! -f "$PLIST_PATH" ]; then
  echo "LaunchAgent not installed ($PLIST_PATH does not exist)."
  exit 0
fi

# Unload
if launchctl list "$PLIST_NAME" >/dev/null 2>&1; then
  echo "Unloading LaunchAgent..."
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# Remove plist
rm -f "$PLIST_PATH"

echo "LaunchAgent removed: $PLIST_PATH"
echo "The OpenClaw Console will no longer start at login."
