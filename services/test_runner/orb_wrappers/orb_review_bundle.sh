#!/usr/bin/env bash
# orb_review_bundle.sh — Run ORB repo's review_bundle.sh in read-only worktree.
# Called by executor with cwd = worktree.
# Env vars (set by executor): ARTIFACT_DIR, SINCE_SHA (optional)
set -euo pipefail

SINCE_SHA="${SINCE_SHA:-}"
OUT="$ARTIFACT_DIR/REVIEW_BUNDLE.txt"

echo "==> orb_review_bundle"
echo "    cwd=$(pwd)"
echo "    artifact_dir=$ARTIFACT_DIR"

# Determine --since SHA
if [ -z "$SINCE_SHA" ]; then
  if [ -f docs/LAST_REVIEWED_SHA.txt ]; then
    SINCE_SHA="$(tr -d '[:space:]' < docs/LAST_REVIEWED_SHA.txt)"
    echo "    since_sha=$SINCE_SHA (from docs/LAST_REVIEWED_SHA.txt)"
  else
    SINCE_SHA="$(git rev-list --max-parents=0 HEAD | head -1)"
    echo "    since_sha=$SINCE_SHA (first commit — no LAST_REVIEWED_SHA.txt)"
  fi
else
  echo "    since_sha=$SINCE_SHA (from params)"
fi

# Check for review_bundle.sh
if [ -f ./ops/review_bundle.sh ]; then
  echo "==> Running ./ops/review_bundle.sh --since $SINCE_SHA --output $OUT"
  bash ./ops/review_bundle.sh --since "$SINCE_SHA" --output "$OUT" || {
    RC=$?
    echo "==> review_bundle.sh exited with code $RC"
    # Exit code 6 means size cap exceeded — still a valid result
    if [ "$RC" -eq 6 ]; then
      echo "    Size cap exceeded; generating fallback file list"
      {
        echo "SIZE_CAP_EXCEEDED"
        echo ""
        echo "=== FILE LIST ==="
        git diff --name-status "$SINCE_SHA" HEAD 2>/dev/null || true
      } > "$OUT"
    fi
    exit $RC
  }
  echo "==> REVIEW_BUNDLE.txt written ($(wc -c < "$OUT" | tr -d ' ') bytes)"
else
  echo "SCRIPT_NOT_FOUND: ./ops/review_bundle.sh not found in target repo" >&2
  echo "SCRIPT_NOT_FOUND: ./ops/review_bundle.sh" > "$OUT"
  exit 1
fi
