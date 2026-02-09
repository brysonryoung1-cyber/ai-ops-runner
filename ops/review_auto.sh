#!/usr/bin/env bash
# review_auto.sh — One-command Codex review with auto packet-mode fallback
# Usage: ./ops/review_auto.sh [--no-push] [--since <sha>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- defaults ---
NO_PUSH=0
SINCE_SHA=""
MAX_PACKETS=20

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push) NO_PUSH=1; shift ;;
    --since)   SINCE_SHA="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: review_auto.sh [--no-push] [--since <sha>]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --- preflight: clean repo ---
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  git status --short >&2
  exit 1
fi

# --- resolve baseline ---
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
STAMP="$(date -u +%Y%m%d_%H%M%S)"
PACK_DIR="$ROOT_DIR/review_packets/${STAMP}"
mkdir -p "$PACK_DIR"

echo "=== review_auto.sh ==="
echo "  Baseline: $SINCE_SHA"
echo "  HEAD:     $HEAD_SHA"
echo "  Pack dir: $PACK_DIR"

# --- resolve codex command ---
resolve_codex_cmd() {
  if command -v codex >/dev/null 2>&1; then
    echo "codex"
  elif command -v npx >/dev/null 2>&1; then
    echo "npx -y @openai/codex"
  else
    echo ""
  fi
}

CODEX_CMD="$(resolve_codex_cmd)"

run_codex_review() {
  local bundle_file="$1"
  local verdict_file="$2"

  if [ -z "$CODEX_CMD" ]; then
    echo "ERROR: Neither 'codex' nor 'npx' found. Cannot run review." >&2
    exit 1
  fi

  local prompt
  prompt="$(cat "$bundle_file")"

  # Run codex in non-interactive mode
  local raw_output
  raw_output="$($CODEX_CMD exec \
    -c approval_policy=never \
    --prompt "$prompt" \
    2>/dev/null || true)"

  # Extract JSON from output (find first { to last })
  local json_output
  json_output="$(echo "$raw_output" | python3 -c "
import sys, json, re

text = sys.stdin.read()
# Find JSON object in output
match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
if match:
    obj = json.loads(match.group())
    print(json.dumps(obj))
else:
    sys.exit(1)
" 2>/dev/null || echo "")"

  if [ -z "$json_output" ]; then
    echo "ERROR: Failed to extract valid JSON from Codex output" >&2
    echo "Raw output saved to ${verdict_file}.raw" >&2
    echo "$raw_output" > "${verdict_file}.raw"
    return 1
  fi

  echo "$json_output" > "$verdict_file"
}

validate_verdict() {
  local verdict_file="$1"

  if [ ! -f "$verdict_file" ]; then
    echo "ERROR: Verdict file not found: $verdict_file" >&2
    return 1
  fi

  python3 -c "
import json, sys

schema = {
    'required': ['verdict', 'blockers', 'non_blocking', 'tests_run'],
    'allowed_verdicts': ['APPROVED', 'BLOCKED']
}

try:
    with open('$verdict_file') as f:
        v = json.load(f)

    # Check required keys
    for key in schema['required']:
        if key not in v:
            print(f'ERROR: Missing required key: {key}', file=sys.stderr)
            sys.exit(1)

    # Check no extra keys
    extra = set(v.keys()) - set(schema['required'])
    if extra:
        print(f'ERROR: Extra keys not allowed: {extra}', file=sys.stderr)
        sys.exit(1)

    # Check verdict value
    if v['verdict'] not in schema['allowed_verdicts']:
        print(f'ERROR: Invalid verdict: {v[\"verdict\"]}', file=sys.stderr)
        sys.exit(1)

    # Check types
    if not isinstance(v['blockers'], list):
        print('ERROR: blockers must be an array', file=sys.stderr)
        sys.exit(1)
    if not isinstance(v['non_blocking'], list):
        print('ERROR: non_blocking must be an array', file=sys.stderr)
        sys.exit(1)
    if not isinstance(v['tests_run'], str):
        print('ERROR: tests_run must be a string', file=sys.stderr)
        sys.exit(1)

    print(v['verdict'])
except json.JSONDecodeError as e:
    print(f'ERROR: Invalid JSON: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1
}

# --- Try single bundle mode first ---
BUNDLE_FILE="$PACK_DIR/REVIEW_BUNDLE.txt"
VERDICT_FILE="$PACK_DIR/CODEX_VERDICT.json"
META_FILE="$PACK_DIR/META.json"

REVIEW_MODE="single"
BUNDLE_RC=0
"$SCRIPT_DIR/review_bundle.sh" --since "$SINCE_SHA" --output "$BUNDLE_FILE" || BUNDLE_RC=$?

if [ "$BUNDLE_RC" -eq 6 ]; then
  echo "==> Bundle exceeds size cap, switching to packet mode..."
  REVIEW_MODE="packet"
elif [ "$BUNDLE_RC" -ne 0 ]; then
  echo "ERROR: review_bundle.sh failed with exit code $BUNDLE_RC" >&2
  exit 1
fi

# --- Skip codex if CODEX_SKIP=1 (for testing) ---
if [ "${CODEX_SKIP:-0}" = "1" ]; then
  echo "==> CODEX_SKIP=1: Simulating APPROVED verdict"
  cat > "$VERDICT_FILE" <<SIMEOF
{"verdict":"APPROVED","blockers":[],"non_blocking":["CODEX_SKIP=1: simulated"],"tests_run":"skipped (CODEX_SKIP=1)"}
SIMEOF
  REVIEW_MODE="single-simulated"
else
  if [ "$REVIEW_MODE" = "single" ]; then
    echo "==> Running Codex review (single bundle)..."
    run_codex_review "$BUNDLE_FILE" "$VERDICT_FILE"
  else
    echo "==> Running Codex review (packet mode)..."
    # Generate per-file packets
    FILE_LIST="$(git diff --name-only "$SINCE_SHA" "$HEAD_SHA")"
    PACKET_NUM=0
    ALL_VERDICTS=()

    while IFS= read -r filepath; do
      [ -z "$filepath" ] && continue
      PACKET_NUM=$((PACKET_NUM + 1))
      if [ "$PACKET_NUM" -gt "$MAX_PACKETS" ]; then
        echo "WARNING: Exceeded max packets ($MAX_PACKETS), stopping" >&2
        break
      fi

      PACKET_FILE="$PACK_DIR/PACKET_${PACKET_NUM}.txt"
      PACKET_VERDICT="$PACK_DIR/VERDICT_${PACKET_NUM}.json"

      FILE_DIFF="$(git diff "$SINCE_SHA" "$HEAD_SHA" -- "$filepath")"
      cat > "$PACKET_FILE" <<PKTEOF
=== REVIEW PACKET (${PACKET_NUM}/${MAX_PACKETS}) ===
Repository: ai-ops-runner
Range: ${SINCE_SHA}..${HEAD_SHA}
File: ${filepath}
Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

=== DIFF ===
${FILE_DIFF}

=== INSTRUCTIONS ===
You are reviewing changes to a single file in the ai-ops-runner repository.
Output ONLY valid JSON. Verdict: APPROVED or BLOCKED.
Schema: {"verdict":"...","blockers":[...],"non_blocking":[...],"tests_run":"..."}
BLOCK only for: deploy/compile failures, unsafe behavior, logging regressions, non-idempotent ops.
PKTEOF

      echo "  Reviewing packet $PACKET_NUM: $filepath"
      run_codex_review "$PACKET_FILE" "$PACKET_VERDICT"
      ALL_VERDICTS+=("$PACKET_VERDICT")
    done <<< "$FILE_LIST"

    # Aggregate verdicts
    python3 -c "
import json, sys, glob, os

pack_dir = '$PACK_DIR'
verdict_files = sorted(glob.glob(os.path.join(pack_dir, 'VERDICT_*.json')))

if not verdict_files:
    print('ERROR: No verdict files found', file=sys.stderr)
    sys.exit(1)

final = {
    'verdict': 'APPROVED',
    'blockers': [],
    'non_blocking': [],
    'tests_run': f'Reviewed {len(verdict_files)} packets'
}

for vf in verdict_files:
    with open(vf) as f:
        v = json.load(f)
    if v.get('verdict') == 'BLOCKED':
        final['verdict'] = 'BLOCKED'
    final['blockers'].extend(v.get('blockers', []))
    final['non_blocking'].extend(v.get('non_blocking', []))

with open('$VERDICT_FILE', 'w') as f:
    json.dump(final, f, indent=2)
print(final['verdict'])
"
  fi
fi

# --- validate verdict ---
VERDICT_RESULT="$(validate_verdict "$VERDICT_FILE")"
if echo "$VERDICT_RESULT" | grep -q "^ERROR"; then
  echo "$VERDICT_RESULT" >&2
  echo "ERROR: Verdict validation failed. NOT writing an empty verdict." >&2
  exit 1
fi

# --- write meta ---
python3 -c "
import json
meta = {
    'since_sha': '$SINCE_SHA',
    'head_sha': '$HEAD_SHA',
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'mode': '$REVIEW_MODE',
    'verdict': '$VERDICT_RESULT'
}
with open('$META_FILE', 'w') as f:
    json.dump(meta, f, indent=2)
"

echo ""
echo "=== Review Result ==="
echo "  Verdict: $VERDICT_RESULT"
echo "  Artifacts: $PACK_DIR"
python3 -c "
import json
with open('$VERDICT_FILE') as f:
    v = json.load(f)
if v['blockers']:
    print('  Blockers:')
    for b in v['blockers']:
        print(f'    - {b}')
if v['non_blocking']:
    print('  Non-blocking:')
    for n in v['non_blocking']:
        print(f'    - {n}')
"

if [ "$VERDICT_RESULT" = "APPROVED" ]; then
  echo ""
  echo "==> APPROVED"

  if [ "$NO_PUSH" -eq 0 ]; then
    echo "==> Advancing baseline and pushing..."
    "$SCRIPT_DIR/review_finish.sh"
  else
    echo "==> --no-push: Skipping baseline advance and push."
    echo "    Run: ./ops/review_finish.sh"
  fi
  exit 0
else
  echo ""
  echo "==> BLOCKED — fix the blockers above and re-run:"
  echo "    ./ops/review_auto.sh"
  exit 1
fi
