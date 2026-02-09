#!/usr/bin/env bash
# review_finish.sh — Advance review baseline + commit isolation + push
#
# Only advances baseline when:
#   1. Working tree is clean
#   2. An APPROVED verdict exists for current HEAD
#   3. The verdict is NOT simulated (meta.simulated must be false)
#   4. The verdict range matches baseline..HEAD
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

# --- find and validate the most recent verdict ---
APPROVED_FILE=""
if [ -d "$ROOT_DIR/review_packets" ]; then
  for verdict_file in $(ls -t "$ROOT_DIR"/review_packets/*/CODEX_VERDICT.json 2>/dev/null); do
    # Validate this verdict against all requirements
    RESULT="$(python3 - "$verdict_file" "$HEAD_SHA" "$CURRENT_BASELINE" <<'PYEOF' 2>&1 || true
import json, sys

vfile = sys.argv[1]
head_sha = sys.argv[2]
baseline = sys.argv[3]

with open(vfile) as f:
    v = json.load(f)

# Must be APPROVED
if v.get("verdict") != "APPROVED":
    sys.exit(1)

meta = v.get("meta")
if not isinstance(meta, dict):
    sys.exit(1)

# Must NOT be simulated
if meta.get("simulated") is not False:
    print("SIMULATED")
    sys.exit(1)

# Range must match: since_sha == baseline, to_sha == HEAD
if meta.get("to_sha") != head_sha:
    sys.exit(1)
if meta.get("since_sha") != baseline:
    sys.exit(1)

# codex_cli must be present (non-null) for real verdicts
cli = meta.get("codex_cli")
if not isinstance(cli, dict) or not cli.get("version"):
    sys.exit(1)

print("OK")
PYEOF
)"
    if [ "$RESULT" = "OK" ]; then
      APPROVED_FILE="$verdict_file"
      break
    elif [ "$RESULT" = "SIMULATED" ]; then
      echo "ERROR: Found verdict for HEAD but it is SIMULATED (CODEX_SKIP)." >&2
      echo "  Simulated verdicts cannot advance the baseline." >&2
      echo "  Run a real review: ./ops/review_auto.sh" >&2
      exit 1
    fi
  done
fi

if [ -z "$APPROVED_FILE" ]; then
  echo "ERROR: No valid APPROVED verdict found for HEAD ($HEAD_SHA)" >&2
  echo "  Required: non-simulated APPROVED verdict with range ${CURRENT_BASELINE}..${HEAD_SHA}" >&2
  echo "  Run: ./ops/review_auto.sh" >&2
  exit 1
fi

echo "==> Found valid verdict: $APPROVED_FILE"

# --- advance baseline ---
echo "$HEAD_SHA" > "$BASELINE_FILE"
echo "==> Baseline advanced to $HEAD_SHA"

# --- commit with pathspec isolation ---
git add -- docs/LAST_REVIEWED_SHA.txt
REVIEW_FINISH_COMMIT=1 git commit -m "$(cat <<'EOF'
chore: advance review baseline

Automated baseline advance after APPROVED verdict.
EOF
)" -- docs/LAST_REVIEWED_SHA.txt

echo "==> Committed baseline advance"

# --- push (pre-push gate validates the verdict with baseline-advance allowance) ---
echo "==> Pushing to origin..."
git push origin HEAD
echo "==> Push complete."
