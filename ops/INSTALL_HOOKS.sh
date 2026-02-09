#!/usr/bin/env bash
# INSTALL_HOOKS.sh — Install .githooks/* into .git/hooks/* idempotently
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

HOOKS_SRC="$ROOT_DIR/.githooks"
HOOKS_DST="$ROOT_DIR/.git/hooks"

if [ ! -d "$HOOKS_SRC" ]; then
  echo "ERROR: .githooks directory not found at $HOOKS_SRC" >&2
  exit 1
fi

if [ ! -d "$HOOKS_DST" ]; then
  echo "ERROR: .git/hooks directory not found. Is this a git repo?" >&2
  exit 1
fi

INSTALLED=0
for hook_file in "$HOOKS_SRC"/*; do
  [ -f "$hook_file" ] || continue
  HOOK_NAME="$(basename "$hook_file")"

  # Skip if already a symlink to the right place
  if [ -L "$HOOKS_DST/$HOOK_NAME" ]; then
    LINK_TARGET="$(readlink "$HOOKS_DST/$HOOK_NAME")"
    if [ "$LINK_TARGET" = "$hook_file" ] || [ "$LINK_TARGET" = "../../.githooks/$HOOK_NAME" ]; then
      echo "  $HOOK_NAME: already installed (symlink)"
      INSTALLED=$((INSTALLED + 1))
      continue
    fi
  fi

  # Backup existing hook if present
  if [ -f "$HOOKS_DST/$HOOK_NAME" ] && [ ! -L "$HOOKS_DST/$HOOK_NAME" ]; then
    echo "  $HOOK_NAME: backing up existing to ${HOOK_NAME}.bak"
    mv "$HOOKS_DST/$HOOK_NAME" "$HOOKS_DST/${HOOK_NAME}.bak"
  fi

  # Create relative symlink
  ln -sf "../../.githooks/$HOOK_NAME" "$HOOKS_DST/$HOOK_NAME"
  chmod +x "$hook_file"
  echo "  $HOOK_NAME: installed"
  INSTALLED=$((INSTALLED + 1))
done

echo ""
echo "==> $INSTALLED hook(s) installed."
echo "==> Verify with: ./ops/doctor_repo.sh"
