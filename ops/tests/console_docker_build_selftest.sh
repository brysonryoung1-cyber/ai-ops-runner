#!/usr/bin/env bash
# console_docker_build_selftest.sh — Hermetic tests for the console Docker build.
#
# Validates the Dockerfile stages and package.json to catch build-time failures
# (e.g. lifecycle hooks calling missing tools) WITHOUT running a full Docker build.
# Fail-closed: exits nonzero on any test failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"
CONSOLE_DIR="$ROOT_DIR/apps/openclaw-console"

PASS=0
FAIL=0
TOTAL=0

assert() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc" >&2
  fi
}

assert_not() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc" >&2
  fi
}

assert_contains() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc (expected '$needle')" >&2
  fi
}

echo "=== console_docker_build_selftest ==="

# --- 1. Dockerfile exists ---
assert "Dockerfile exists" test -f "$CONSOLE_DIR/Dockerfile"

# --- 2. Dockerfile uses node base image ---
DOCKERFILE_CONTENT="$(cat "$CONSOLE_DIR/Dockerfile")"
assert_contains "Dockerfile deps stage uses node image" "$DOCKERFILE_CONTENT" "FROM node:"
assert_contains "Dockerfile has builder stage" "$DOCKERFILE_CONTENT" "AS builder"
assert_contains "Dockerfile has runner stage" "$DOCKERFILE_CONTENT" "AS runner"

# --- 3. Dockerfile builder runs npm run build ---
assert_contains "Builder stage runs npm run build" "$DOCKERFILE_CONTENT" "npm run build"

# --- 4. Dockerfile deps stage runs npm ci ---
assert_contains "Deps stage runs npm ci" "$DOCKERFILE_CONTENT" "npm ci"

# --- 5. package.json exists ---
assert "package.json exists" test -f "$CONSOLE_DIR/package.json"

# --- 6. CRITICAL: No prebuild lifecycle hook (would fail in alpine without python3) ---
PKG_JSON="$(cat "$CONSOLE_DIR/package.json")"
assert_not "No prebuild lifecycle hook in package.json" grep -q '"prebuild"' "$CONSOLE_DIR/package.json"

# --- 7. No preinstall lifecycle hook that requires external tools ---
assert_not "No preinstall lifecycle hook" grep -q '"preinstall"' "$CONSOLE_DIR/package.json"

# --- 8. build script is plain next build (no python/shell dependencies) ---
BUILD_CMD="$(echo "$PKG_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('scripts',{}).get('build',''))" 2>/dev/null)"
assert_contains "build script is 'next build'" "$BUILD_CMD" "next build"

# --- 9. next is a production dependency (not pruned) ---
assert_contains "next is a dependency" "$PKG_JSON" '"next"'

# --- 10. package-lock.json exists (required for npm ci) ---
assert "package-lock.json exists" test -f "$CONSOLE_DIR/package-lock.json"

# --- 11. next.config.js uses standalone output (required for Docker) ---
NEXT_CONFIG="$(cat "$CONSOLE_DIR/next.config.js" 2>/dev/null || echo "")"
assert_contains "next.config.js uses standalone output" "$NEXT_CONFIG" 'output: "standalone"'

# --- 12. .dockerignore exists (keeps context lean) ---
assert ".dockerignore exists" test -f "$CONSOLE_DIR/.dockerignore"

# --- 13. .dockerignore excludes node_modules ---
DOCKERIGNORE="$(cat "$CONSOLE_DIR/.dockerignore" 2>/dev/null || echo "")"
assert_contains ".dockerignore excludes node_modules" "$DOCKERIGNORE" "node_modules"

# --- 14. Dockerfile copies standalone output ---
assert_contains "Dockerfile copies standalone output" "$DOCKERFILE_CONTENT" ".next/standalone"

# --- 15. Generated action_registry file is committed (so Docker build doesn't need codegen) ---
assert "action_registry.generated.ts is committed" test -f "$CONSOLE_DIR/src/lib/action_registry.generated.ts"

# --- Summary ---
echo ""
echo "=== console_docker_build_selftest: $PASS/$TOTAL passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  echo "FAIL-CLOSED: Console Docker build would likely fail. Fix the above issues." >&2
  exit 1
fi
exit 0
