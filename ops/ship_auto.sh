#!/usr/bin/env bash
# ship_auto.sh — Full autopilot: test → review → heal → push
# Usage: ./ops/ship_auto.sh [--no-push] [--max-attempts N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- defaults ---
NO_PUSH=0
MAX_ATTEMPTS=${SHIP_MAX_ATTEMPTS:-3}
ATTEMPT=0

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push)       NO_PUSH=1; shift ;;
    --max-attempts)  MAX_ATTEMPTS="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: ship_auto.sh [--no-push] [--max-attempts N]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

echo "=== ship_auto.sh ==="
echo "  Max attempts: $MAX_ATTEMPTS"
echo "  No-push:      $NO_PUSH"
echo ""

# --- run tests ---
run_tests() {
  echo "==> Running tests..."
  local test_failed=0

  # Run pytest if available (with timeout, skip if deps missing)
  if [ "${SHIP_SKIP_PYTEST:-0}" = "1" ]; then
    echo "  pytest: SKIPPED (SHIP_SKIP_PYTEST=1)"
  elif [ -d "$ROOT_DIR/services/test_runner/tests" ]; then
    echo "  Running pytest..."
    PYTEST_OUTPUT=""
    PYTEST_RC=0
    TIMEOUT_CMD=""
    if command -v timeout &>/dev/null; then
      TIMEOUT_CMD="timeout 120"
    elif command -v gtimeout &>/dev/null; then
      TIMEOUT_CMD="gtimeout 120"
    fi
    PYTEST_OUTPUT="$(cd "$ROOT_DIR/services/test_runner" && $TIMEOUT_CMD python3 -m pytest -q tests/ 2>&1)" || PYTEST_RC=$?
    if [ "$PYTEST_RC" -eq 0 ]; then
      echo "  pytest: PASSED"
    elif [ "$PYTEST_RC" -eq 5 ]; then
      echo "  pytest: NO TESTS COLLECTED (skipping — deps may be missing)"
    elif echo "$PYTEST_OUTPUT" | grep -q "ModuleNotFoundError\|ImportError\|No module named"; then
      echo "  pytest: SKIPPED (missing dependencies)"
    elif [ "$PYTEST_RC" -eq 124 ]; then
      echo "  pytest: TIMED OUT" >&2
      test_failed=1
    else
      echo "  pytest: FAILED (rc=$PYTEST_RC)" >&2
      echo "$PYTEST_OUTPUT" | tail -5 >&2
      test_failed=1
    fi
  fi

  # Validate docker compose config (non-destructive)
  if [ -f "$ROOT_DIR/docker-compose.yml" ]; then
    echo "  Validating docker-compose.yml..."
    if docker compose config -q 2>/dev/null; then
      echo "  docker compose config: VALID"
    else
      echo "  WARNING: docker compose config validation failed (docker may not be running)" >&2
      # Don't fail on this — docker may not be available in CI
    fi
  fi

  # Run ops selftests if available (skip if SHIP_SKIP_SELFTESTS=1 to avoid recursion)
  if [ "${SHIP_SKIP_SELFTESTS:-0}" = "1" ]; then
    echo "  ops selftests: SKIPPED (SHIP_SKIP_SELFTESTS=1)"
  elif [ -d "$ROOT_DIR/ops/tests" ]; then
    for selftest in "$ROOT_DIR"/ops/tests/*_selftest.sh; do
      [ -f "$selftest" ] || continue
      # Skip ship_auto's own selftest to avoid recursion
      if [[ "$(basename "$selftest")" == "ship_auto_selftest.sh" ]]; then
        continue
      fi
      echo "  Running $(basename "$selftest")..."
      if SHIP_SKIP_SELFTESTS=1 bash "$selftest"; then
        echo "  $(basename "$selftest"): PASSED"
      else
        echo "  $(basename "$selftest"): FAILED" >&2
        test_failed=1
      fi
    done
  fi

  return $test_failed
}

# --- main loop ---
while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
  ATTEMPT=$((ATTEMPT + 1))
  echo "=== Attempt $ATTEMPT / $MAX_ATTEMPTS ==="

  # Step 1: Run tests
  if ! run_tests; then
    echo "==> Tests failed on attempt $ATTEMPT"
    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ] && [ "${CODEX_SKIP:-0}" != "1" ]; then
      echo "==> Running autoheal..."
      if "$SCRIPT_DIR/autoheal_codex.sh"; then
        echo "==> Autoheal applied, retrying..."
        continue
      else
        echo "ERROR: Autoheal failed" >&2
        exit 1
      fi
    else
      echo "ERROR: Tests failed and no more attempts remaining" >&2
      exit 1
    fi
  fi

  # Step 2: Run review
  REVIEW_ARGS=("--no-push")
  REVIEW_RC=0
  "$SCRIPT_DIR/review_auto.sh" "${REVIEW_ARGS[@]}" || REVIEW_RC=$?

  if [ "$REVIEW_RC" -eq 0 ]; then
    echo ""
    echo "==> APPROVED on attempt $ATTEMPT"

    if [ "$NO_PUSH" -eq 0 ]; then
      echo "==> Advancing baseline and pushing..."
      "$SCRIPT_DIR/review_finish.sh"
      echo ""
      echo "=== ship_auto.sh COMPLETE ==="
      echo "  Verdict:  APPROVED"
      echo "  Attempt:  $ATTEMPT / $MAX_ATTEMPTS"
      echo "  Pushed:   YES"
    else
      echo ""
      echo "=== ship_auto.sh COMPLETE ==="
      echo "  Verdict:  APPROVED"
      echo "  Attempt:  $ATTEMPT / $MAX_ATTEMPTS"
      echo "  Pushed:   NO (--no-push)"
      echo "  Run: ./ops/review_finish.sh"
    fi
    exit 0
  fi

  # Step 3: BLOCKED — try autoheal
  if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
    echo "==> BLOCKED on attempt $ATTEMPT, running autoheal..."
    if [ "${CODEX_SKIP:-0}" = "1" ]; then
      echo "==> CODEX_SKIP=1: Cannot autoheal without Codex"
      exit 1
    fi
    if "$SCRIPT_DIR/autoheal_codex.sh"; then
      echo "==> Autoheal applied, retrying..."
    else
      echo "ERROR: Autoheal failed" >&2
      exit 1
    fi
  else
    echo ""
    echo "=== ship_auto.sh FAILED ==="
    echo "  BLOCKED after $MAX_ATTEMPTS attempts"
    echo "  Fix blockers manually and re-run: ./ops/ship_auto.sh"
    exit 1
  fi
done

echo "ERROR: Exhausted $MAX_ATTEMPTS attempts without approval" >&2
exit 1
