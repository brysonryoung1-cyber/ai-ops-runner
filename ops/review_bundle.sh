#!/usr/bin/env bash
# review_bundle.sh — Generate a bounded-size review bundle (diff + file list)
# Exit codes:
#   0  = bundle generated successfully
#   6  = size cap exceeded (switch to packet mode)
#   1  = general error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- defaults ---
SIZE_CAP=${REVIEW_BUNDLE_SIZE_CAP:-204800}   # 200 KB
SINCE_SHA=""
OUTPUT_FILE=""

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since)   SINCE_SHA="$2"; shift 2 ;;
    --output)  OUTPUT_FILE="$2"; shift 2 ;;
    --size-cap) SIZE_CAP="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: review_bundle.sh --since <sha> [--output <file>] [--size-cap <bytes>]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$SINCE_SHA" ]; then
  BASELINE_FILE="$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt"
  if [ -f "$BASELINE_FILE" ]; then
    SINCE_SHA="$(tr -d '[:space:]' < "$BASELINE_FILE")"
  else
    echo "ERROR: No --since provided and docs/LAST_REVIEWED_SHA.txt not found" >&2
    exit 1
  fi
fi

HEAD_SHA="$(git rev-parse HEAD)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- validate range ---
if ! git cat-file -e "${SINCE_SHA}^{commit}" 2>/dev/null; then
  echo "ERROR: Baseline SHA not found in repo: $SINCE_SHA" >&2
  exit 1
fi

# --- generate file list ---
FILE_LIST="$(git diff --name-status "$SINCE_SHA" "$HEAD_SHA")"

if [ -z "$FILE_LIST" ]; then
  echo "INFO: No changes between $SINCE_SHA and $HEAD_SHA"
  # Still produce valid output
  BUNDLE="=== REVIEW PACKET ===
Repository: ai-ops-runner
Range: ${SINCE_SHA}..${HEAD_SHA}
Generated: ${TIMESTAMP}

=== CHANGED FILES ===
(no changes)

=== DIFF ===
(no diff)

=== INSTRUCTIONS ===
No changes to review. Output APPROVED verdict.
"
  if [ -n "$OUTPUT_FILE" ]; then
    echo "$BUNDLE" > "$OUTPUT_FILE"
  else
    echo "$BUNDLE"
  fi
  exit 0
fi

# --- generate diff ---
DIFF="$(git diff "$SINCE_SHA" "$HEAD_SHA")"

# --- assemble bundle ---
INSTRUCTIONS='You are reviewing a diff for the ai-ops-runner repository.
Output ONLY valid JSON matching the schema below. Do NOT include any text outside the JSON object.

Schema:
{
  "verdict": "APPROVED" or "BLOCKED",
  "blockers": ["string array of blocking issues"],
  "non_blocking": ["string array of non-blocking suggestions"],
  "tests_run": "string summary of tests"
}

BLOCK only for:
- Deploy/compile failures
- Unsafe behavior (shell injection, credential leaks, etc.)
- Logging or run-day regressions
- Non-idempotent operations that could cause drift

If no blocking issues exist, verdict MUST be "APPROVED".'

BUNDLE="=== REVIEW PACKET ===
Repository: ai-ops-runner
Range: ${SINCE_SHA}..${HEAD_SHA}
Generated: ${TIMESTAMP}

=== CHANGED FILES ===
${FILE_LIST}

=== DIFF ===
${DIFF}

=== INSTRUCTIONS ===
${INSTRUCTIONS}
"

# --- check size ---
BUNDLE_SIZE=${#BUNDLE}
if [ "$BUNDLE_SIZE" -gt "$SIZE_CAP" ]; then
  echo "ERROR: Bundle size ($BUNDLE_SIZE bytes) exceeds cap ($SIZE_CAP bytes)" >&2
  echo "" >&2
  echo "Per-file sizes:" >&2
  while IFS=$'\t' read -r status filepath; do
    if [ -n "$filepath" ]; then
      FILE_DIFF_SIZE=$(git diff "$SINCE_SHA" "$HEAD_SHA" -- "$filepath" | wc -c | tr -d ' ')
      echo "  ${status}  ${filepath}  (${FILE_DIFF_SIZE} bytes)" >&2
    fi
  done <<< "$FILE_LIST"
  exit 6
fi

# --- output ---
if [ -n "$OUTPUT_FILE" ]; then
  echo "$BUNDLE" > "$OUTPUT_FILE"
  echo "Bundle written to $OUTPUT_FILE ($BUNDLE_SIZE bytes)"
else
  echo "$BUNDLE"
fi
