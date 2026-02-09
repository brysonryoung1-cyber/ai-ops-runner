#!/usr/bin/env bash
# orb_score_run.sh — Run ORB repo's scoring harness in read-only worktree.
# Called by executor with cwd = worktree.
# Env vars (set by executor): ARTIFACT_DIR, LOGS_DAY (optional), RUN_ID (optional)
set -euo pipefail

OUT="$ARTIFACT_DIR/SCORE_OUTPUT.txt"
LOGS_DAY="${LOGS_DAY:-}"
RUN_ID="${RUN_ID:-}"

echo "==> orb_score_run"
echo "    cwd=$(pwd)"
echo "    artifact_dir=$ARTIFACT_DIR"
echo "    logs_day=$LOGS_DAY"
echo "    run_id=$RUN_ID"

# Look for scoring harness in standard locations
HARNESS=""
for candidate in \
  ./research/score_harness.sh \
  ./research/score_harness.py \
  ./ops/score_run.sh \
  ./ops/score_harness.sh; do
  if [ -f "$candidate" ]; then
    HARNESS="$candidate"
    break
  fi
done

if [ -z "$HARNESS" ]; then
  echo "HARNESS_NOT_FOUND" >&2
  {
    echo "HARNESS_NOT_FOUND: No scoring harness found in target repo."
    echo ""
    echo "Searched locations:"
    echo "  - research/score_harness.sh"
    echo "  - research/score_harness.py"
    echo "  - ops/score_run.sh"
    echo "  - ops/score_harness.sh"
  } > "$OUT"
  exit 1
fi

echo "==> Found harness: $HARNESS"

# Build args
ARGS=""
[ -n "$LOGS_DAY" ] && ARGS="$LOGS_DAY"
[ -n "$RUN_ID" ] && ARGS="$ARGS $RUN_ID"

# Execute based on file type
case "$HARNESS" in
  *.py)
    echo "==> Running: python3 $HARNESS $ARGS"
    # shellcheck disable=SC2086
    python3 "$HARNESS" $ARGS > "$OUT" 2>&1 || {
      RC=$?
      echo "--- EXIT CODE: $RC ---" >> "$OUT"
      exit $RC
    }
    ;;
  *.sh)
    echo "==> Running: bash $HARNESS $ARGS"
    # shellcheck disable=SC2086
    bash "$HARNESS" $ARGS > "$OUT" 2>&1 || {
      RC=$?
      echo "--- EXIT CODE: $RC ---" >> "$OUT"
      exit $RC
    }
    ;;
esac

echo "==> SCORE_OUTPUT.txt written ($(wc -c < "$OUT" | tr -d ' ') bytes)"
