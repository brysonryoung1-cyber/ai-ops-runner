#!/usr/bin/env bash
# review_finish.sh — Advance review baseline + commit isolation + push
# Only advances baseline when APPROVED verdict exists for current HEAD.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

BASELINE_FILE="$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt"

# --- preflight: clean repo ---
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  git status --short >&2
  exit 1
fi

HEAD_SHA="$(git rev-parse HEAD)"
CURRENT_BASELINE="$(tr -d '[:space:]' < "$BASELINE_FILE")"

if [ "$CURRENT_BASELINE" = "$HEAD_SHA" ]; then
  echo "INFO: Baseline already at HEAD ($HEAD_SHA). Nothing to advance."
  exit 0
fi

# --- check for APPROVED verdict ---
APPROVED_FOUND=0
if [ -d "$ROOT_DIR/review_packets" ]; then
  # Find the most recent verdict covering this range
  for meta_file in $(ls -t "$ROOT_DIR"/review_packets/*/META.json 2>/dev/null); do
    VERDICT="$(python3 -c "
import json
with open('$meta_file') as f:
    m = json.load(f)
print(m.get('verdict', ''))
" 2>/dev/null || echo "")"
    META_HEAD="$(python3 -c "
import json
with open('$meta_file') as f:
    m = json.load(f)
print(m.get('head_sha', ''))
" 2>/dev/null || echo "")"

    if [ "$VERDICT" = "APPROVED" ] && [ "$META_HEAD" = "$HEAD_SHA" ]; then
      APPROVED_FOUND=1
      break
    fi
  done
fi

if [ "$APPROVED_FOUND" -eq 0 ]; then
  echo "ERROR: No APPROVED verdict found for HEAD ($HEAD_SHA)" >&2
  echo "  Run: ./ops/review_auto.sh" >&2
  exit 1
fi

# --- advance baseline ---
echo "$HEAD_SHA" > "$BASELINE_FILE"
echo "==> Baseline advanced to $HEAD_SHA"

# --- commit with pathspec isolation ---
REVIEW_FINISH_COMMIT=1 git add -- docs/LAST_REVIEWED_SHA.txt
git commit -m "$(cat <<'EOF'
chore: advance review baseline

Automated baseline advance after APPROVED verdict.
EOF
)" -- docs/LAST_REVIEWED_SHA.txt

echo "==> Committed baseline advance"

# --- push ---
echo "==> Pushing to origin..."
REVIEW_PUSH_APPROVED=1 git push origin HEAD
echo "==> Push complete."
